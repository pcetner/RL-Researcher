"""The run loop every kind shares.

``run`` takes the lock, opens the flushed log, refuses over the gate, asks the kind's guards,
prepares once, then runs each unit that has no result yet: a unit directory, a heartbeat, a
result written atomically, the checkpoint cleared once the result is the proof. It is idempotent
and hot-restartable: run the same command again and finished units are skipped, a checkpointed
unit continues (the kind's ``run_unit`` decides how), and a unit that raises is marked failed
where the status command looks. The summary is written to ``<out>/results.json`` and the kind
adds its figures to it.
"""

from __future__ import annotations

import json
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from rl_researcher import atomic
from rl_researcher.checkpoint import clear_checkpoint
from rl_researcher.gate import GateRefused
from rl_researcher.kinds import DeviceInfo, RunContext, RunKind, UnitContext
from rl_researcher.lock import acquire_lock, release_lock
from rl_researcher.runlog import Log, open_run_log
from rl_researcher.spec import RunSpec, spec_fingerprint
from rl_researcher.stop import install_stop_handler
from rl_researcher.units import (PROGRESS_NAME, RESULTS_NAME, mark_failed, parse_unit, read_json,
                                 thin, unit_dir, write_progress)

PageWriter = Callable[..., None]
PageWriterFactory = Callable[[RunSpec, RunKind, Path, Log], PageWriter]


class GuardBlocked(RuntimeError):
    pass


def git_sha() -> str:
    """The code a result was produced with."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=5,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _no_page(*_a: Any, **_k: Any) -> None:
    return None


def _no_page_factory(spec: RunSpec, kind: RunKind, out: Path, log: Log) -> PageWriter:
    return _no_page


def missing_units(kind: RunKind, spec: RunSpec, out: Path) -> List[str]:
    return [u for u in kind.units(spec) if not (unit_dir(out, u) / RESULTS_NAME).is_file()]


def run(
    spec: RunSpec,
    kind: RunKind,
    out: "str | Path",
    *,
    config: Any = None,
    device: Optional[DeviceInfo] = None,
    log: Log = print,
    resume: bool = True,
    units: Optional[List[str]] = None,
    max_steps: Optional[int] = None,
    max_seconds: Optional[float] = None,
    allow_guards: bool = False,
    page_writer: PageWriterFactory = _no_page_factory,
    gate: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Run every unit of ``spec`` that has no result yet, and write the summary.

    ``units`` names the arms (or unit ids) this machine takes when a run is split across
    machines; the summary covers whichever units exist and says which are missing.
    ``page_writer`` builds the dashboard writer that the heartbeat drives; it must never raise
    into the run. ``gate`` is called before the lock with the spec and the effective budget
    and raises to refuse; the default is no gate, the framework's is
    :func:`rl_researcher.gate.enforce`.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    max_steps = max_steps or spec.budget.max_steps
    max_seconds = max_seconds if max_seconds is not None else spec.budget.max_seconds
    if device is None:
        # Through the cost module, so the fingerprint the runner records a unit's throughput
        # under is the one the next estimate looks it up by. Resolving it here as the bare
        # kind.device() left every run recorded against an empty fingerprint, and every
        # estimate reading "no record on this device" however many units had finished on it.
        try:
            from rl_researcher.cost import probe_device

            device = probe_device(kind, config)
        except Exception:  # noqa: BLE001 - a cost lookup must never stop a run
            device = kind.device()
    fingerprint = spec_fingerprint(spec)
    log_name = getattr(kind, "log_name", "run.log")
    log, close_log = open_run_log(out / log_name, log)
    current: Optional[Path] = None
    lock: Optional[Path] = None
    completed = False
    page: PageWriter = _no_page
    started = time.time()
    try:
        if gate is not None:
            gate(spec, kind, out, max_steps=max_steps, max_seconds=max_seconds, config=config, device=device)
        lock = acquire_lock(out, spec.name, log)
        install_stop_handler()
        try:
            page = page_writer(spec, kind, out, log)
        except Exception as exc:  # noqa: BLE001 - a page must never take a run down
            log(f"dashboard disabled: {type(exc).__name__}: {exc}")
            page = _no_page
        page(force=True)
        for g in kind.guards(spec):
            if g.is_blocked() and not allow_guards:
                raise GuardBlocked(f"{g.name}: {g.message}")
        all_units = kind.units(spec)
        selected = list(all_units)
        if units:
            wanted = set(units)
            selected = [u for u in all_units if u in wanted or parse_unit(u)[0] in wanted]
            unknown = wanted - set(all_units) - {parse_unit(u)[0] for u in all_units}
            if unknown:
                raise ValueError(f"unknown units {sorted(unknown)}; the run has {all_units}")
            log(f"running only {len(selected)} of {len(all_units)} units on this machine")
        ctx = RunContext(out=out, log=log, device=device, max_steps=max_steps, max_seconds=max_seconds,
                         config=config, selected=selected)
        prepared = kind.prepare(spec, ctx)

        results: List[Dict[str, Any]] = []
        for unit in all_units:
            arm, seed = parse_unit(unit)
            cell = unit_dir(out, unit)
            res_path = cell / RESULTS_NAME
            if unit not in selected:
                if res_path.is_file():
                    results.append(read_json(res_path) or {})
                continue
            cell.mkdir(parents=True, exist_ok=True)
            if resume and res_path.is_file():
                results.append(read_json(res_path) or {})
                log(f"[{arm} seed {seed}] already finished; {res_path} kept")
                continue
            if not resume:
                clear_checkpoint(cell)
            current = cell
            uctx = UnitContext(unit=unit, arm=arm, seed=seed, cell=cell, progress=cell / PROGRESS_NAME,
                               log=log, device=device, max_steps=max_steps, max_seconds=max_seconds,
                               resume=resume, fingerprint=fingerprint, cadence=spec.cadence,
                               on_beat=lambda: page())
            uctx.beat("starting", step=0, elapsed_seconds=0.0)
            t_unit = time.time()
            r = kind.run_unit(spec, unit, prepared, uctx)
            d = r.to_dict()
            d["wall_seconds"] = round(time.time() - t_unit, 1)
            atomic.write_text(res_path, json.dumps(d, indent=2, default=str))
            clear_checkpoint(cell)  # the result is the proof; the checkpoint has no further use
            # The kind's own `extra` fields go into the last heartbeat too, not only into the
            # result: whoever reads progress.json after a unit finishes (a status command, a
            # dashboard, a watcher) is reading the same record and should see the same names.
            beat_fields: Dict[str, Any] = dict(r.extra)
            beat_fields.update(
                unit=unit, arm=arm, seed=seed, status="done", step=r.steps, max_steps=max_steps,
                elapsed_seconds=round(r.seconds, 1), result=res_path.relative_to(out).as_posix(),
                resumed_from_step=r.resumed_from_step,
                history={k: thin(v) for k, v in r.history.items() if v})
            write_progress(cell / PROGRESS_NAME, **beat_fields)
            if config is not None:
                try:
                    from rl_researcher.cost import record_throughput

                    record_throughput(config, kind, spec, unit, d, device)
                except Exception as exc:  # noqa: BLE001 - bookkeeping must never fail a run
                    log(f"throughput record skipped: {type(exc).__name__}: {exc}")
            results.append(d)
            current = None
            log(f"[{arm} seed {seed}] {r.status} after {r.steps} steps / {r.seconds:.0f}s: "
                + " ".join(f"{m.name}={r.metrics.get(m.name, float('nan')):.3f}" for m in spec.metrics))
            page(force=True)

        summary: Dict[str, Any] = {
            "run": spec.name,
            "kind": kind.name,
            "fingerprint": fingerprint,
            "git_sha": git_sha(),
            "device": device.name if device else None,
            "device_fingerprint": device.fingerprint if device else None,
            "budget": {"max_steps": max_steps, "max_seconds": max_seconds},
            "seeds": list(spec.seeds),
            "runs": results,
            "missing_units": missing_units(kind, spec, out),
            "figures": {},
            "wall_seconds": round(time.time() - started, 1),
        }
        summary.update(kind.summarise(spec, results, out, ctx) or {})
        atomic.write_text(out / RESULTS_NAME, json.dumps(summary, indent=2, default=str))
        log(f"summary -> {out / RESULTS_NAME}" + (f" ({len(summary['missing_units'])} units missing)"
                                                   if summary["missing_units"] else ""))
        completed = True
        return summary
    except GateRefused as exc:
        # A refusal is not a failure. It happens before the lock and before any unit exists, so
        # there is nothing to mark and nothing to diagnose; a traceback here would read as a
        # crash and bury the one thing the human has to act on, which is the approve command.
        for line in str(exc).splitlines():
            log(line)
        raise
    except Exception as exc:
        # A hot-stop (KeyboardInterrupt / HotStop) is not a failure and is not caught here.
        log(f"FAILED: {type(exc).__name__}: {exc}")
        for line in traceback.format_exc().rstrip().splitlines():
            log(f"  | {line}")
        if current is not None:
            mark_failed(current / PROGRESS_NAME, exc)
            log(f"marked {current / PROGRESS_NAME} failed; `status` exits 2 while it stands")
        raise
    finally:
        # A run that finished will not change again, so its page stops reloading itself; one
        # that was stopped or failed keeps refreshing, because resuming it will change it.
        try:
            page(force=True, refresh=not completed)
        except Exception:  # noqa: BLE001
            pass
        release_lock(lock)
        close_log()


def load_summary(out: Path) -> Dict[str, Any]:
    p = Path(out) / RESULTS_NAME
    if not p.is_file():
        raise FileNotFoundError(f"no {RESULTS_NAME} in {out}; the run has not finished")
    return read_json(p) or {}
