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
import os
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
                                 stamp_now, thin, unit_dir, write_progress)

PageWriter = Callable[..., None]
PageWriterFactory = Callable[[RunSpec, RunKind, Path, Log], PageWriter]


class Refused(RuntimeError):
    """The run did not start, and nothing is wrong with it.

    A guard that is blocked and a check that returned an error are the same event to whoever
    launched the run: it was asked whether to start, and the answer was no. Neither is a
    failure, so neither is logged as one — a traceback here reads as a crash and buries the
    sentence the human has to act on.
    """


class GuardBlocked(Refused):
    pass


class CheckFailed(Refused):
    pass


def git_sha(cwd: Optional[Path] = None) -> str:
    """The code a result was produced with. ``cwd`` chooses whose repository is asked; the
    default is the working directory, which is the consuming project."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, timeout=5,
                                       cwd=None if cwd is None else str(cwd),
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def framework_stamp() -> str:
    """Which version of *this package* computed a number.

    ``git_sha`` records the consuming project, which is the other half. The README's argument
    for pinning a commit is that a finished run cannot be reproduced from the two repositories
    alone if the dependency can move underneath it — and until this existed, nothing on disk
    said which version of the dependency had been underneath it.

    A wheel has no repository to ask, so it is the version alone; a checkout adds the commit,
    which is what the framework is actually run from while it is being built.
    """
    from rl_researcher import __version__

    sha = git_sha(Path(__file__).resolve().parent)
    return f"{__version__}+g{sha[:8]}" if sha != "unknown" else __version__


def _no_page(*_a: Any, **_k: Any) -> None:
    return None


def _no_page_factory(spec: RunSpec, kind: RunKind, out: Path, log: Log) -> PageWriter:
    return _no_page


def _run_checks(kind: RunKind, spec: RunSpec, config: Any, log: Log, skip: bool,
                out: Optional[Path] = None) -> None:
    """Ask the kind what it knows before the first unit, and refuse on anything at error level.

    This runs before ``prepare``, so a refusal costs nothing: no model is loaded, no engine has
    booted, no unit directory exists. The alternative the kinds were writing before this existed
    was a raise inside ``run_unit``, which discovers halfway through the second unit that the
    data was wrong and throws away everything in front of it.

    ``out`` reaches the ``run``-stage checks, whose question is about this machine now rather
    than about the registration.
    """
    from rl_researcher.findings import collect, errors

    findings = collect(kind, spec, config, out)
    for f in findings:
        log(f"check [{f.check}] {f.level}: {f.message}")
    bad = errors(findings)
    if not bad:
        return
    if skip:
        # Said out loud, in the run's own log, because the numbers this run produces were
        # made against a registration something already objected to.
        log(f"waived {len(bad)} check error(s) with --no-check; this run's numbers stand on that")
        return
    raise CheckFailed(f"{len(bad)} check(s) failed: "
                      + "; ".join(f"[{f.check}] {f.message}" for f in bad)
                      + ". Fix them, or pass --no-check to run anyway.")


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
    skip_checks: bool = False,
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
    if config is not None:
        from rl_researcher.research_queue import hold
        reason = hold(config, spec.name)
        if reason:
            raise Refused("Research hold: " + reason)
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
        if config is not None:
            from rl_researcher.workflow_store import exclusive, clear_launch, pending, read, launch_path
            with exclusive(config):
                reason = hold(config, spec.name)
                if reason:
                    raise Refused("Research hold: " + reason)
                reservation = read(launch_path(config), {}).get(spec.name, {})
                if pending(config, spec.name) and reservation.get("pid") != os.getpid():
                    raise Refused("A launch is already pending for this run.")
                lock = acquire_lock(out, spec.name, log)
                clear_launch(config, spec.name)
        else:
            lock = acquire_lock(out, spec.name, log)
        if gate is not None:
            gate(spec, kind, out, max_steps=max_steps, max_seconds=max_seconds, config=config, device=device)
        else:
            # Said out loud for the same reason `--no-check` is: a gate walked past leaves the
            # same numbers on disk as one that was cleared, and the log is the only place that
            # can say which happened. The library default is no gate, so this is honest there too.
            log("no cost gate was applied to this run (--no-gate, or a caller that passed none)")
        install_stop_handler()
        try:
            page = page_writer(spec, kind, out, log)
        except Exception as exc:  # noqa: BLE001 - a page must never take a run down
            log(f"dashboard disabled: {type(exc).__name__}: {exc}")
            page = _no_page
        page(force=True)
        for g in kind.guards(spec):
            if not g.is_blocked():
                continue
            if not allow_guards:
                raise GuardBlocked(f"{g.name}: {g.message}")
            # A waived guard is a condition the kind said a run must not start under, started
            # anyway. It goes in the run's own log beside the numbers it produced.
            log(f"waived a blocked guard with --allow-guards: {g.name}: {g.message}")
        _run_checks(kind, spec, config, log, skip_checks, out)
        all_units = kind.units(spec)
        selected = list(all_units)
        if units:
            wanted = set(units)
            selected = [u for u in all_units if u in wanted or parse_unit(u)[0] in wanted]
            unknown = wanted - set(all_units) - {parse_unit(u)[0] for u in all_units}
            if unknown:
                raise ValueError(f"unknown units {sorted(unknown)}; the run has {all_units}")
            log(f"running only {len(selected)} of {len(all_units)} units on this machine")
        # Preparing is what costs: a snapshot read into memory, a model built, an engine binary
        # demanded. When every selected unit already has a result there is nothing to prepare
        # for, and the run is really a request to rebuild the summary from what is on disk —
        # which must work on a machine that could not have produced it.
        todo = [u for u in selected
                if not (resume and (unit_dir(out, u) / RESULTS_NAME).is_file())]
        previous = read_json(out / RESULTS_NAME) or {} if (out / RESULTS_NAME).is_file() else {}
        # The commit that produced the units, which is only today's HEAD when a unit ran today.
        # Rebuilding a summary must not restamp measurements with the commit that re-rendered
        # them: the ledger's row identity carries the commit, so a restamp writes a second row
        # claiming the same numbers were measured by code that never ran them.
        commit = git_sha() if todo else str(previous.get("git_sha") or git_sha())
        framework = (framework_stamp() if todo
                     else str(previous.get("rl_researcher") or framework_stamp()))
        ctx = RunContext(out=out, log=log, device=device, max_steps=max_steps, max_seconds=max_seconds,
                         config=config, selected=selected, commit=commit, previous=previous,
                         framework=framework)
        prepared = None
        if todo:
            prepared = kind.prepare(spec, ctx)
        else:
            log(f"every unit of {spec.name} already has a result; rebuilding the summary only "
                f"(provenance kept at {commit[:12]})")

        results: List[Dict[str, Any]] = []
        for unit in all_units:
            arm, seed = parse_unit(unit)
            cell = unit_dir(out, unit)
            res_path = cell / RESULTS_NAME
            if unit not in selected:
                if res_path.is_file():
                    results.append(kind.read_result(read_json(res_path) or {}))
                continue
            cell.mkdir(parents=True, exist_ok=True)
            if resume and res_path.is_file():
                # Through the kind, because a result written before the current contract is
                # still evidence and the kind is the only thing that knows its old shape. Read
                # raw, a summary of finished units carries no `metrics` at all, and a report
                # over it prints every registered number as n/a under a cleared-every-bar
                # headline -- an all-clear made of nothing.
                results.append(kind.read_result(read_json(res_path) or {}))
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
                unit=unit, arm=arm, seed=seed,
                # The unit's own word for how it ended. This always said "done", so a unit that
                # hit the time cap before its step budget was recorded as having finished, and
                # only the result file said otherwise.
                status="done" if r.status == "complete" else r.status,
                step=r.steps, max_steps=max_steps,
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
            "git_sha": commit,
            # The project's commit is half the provenance; this is the other half. On a rebuild
            # it is carried forward with the rest, so a regenerated document attributes the
            # numbers to the framework that made them rather than the one re-rendering them.
            "rl_researcher": framework,
            "device": device.name if device else None,
            "device_fingerprint": device.fingerprint if device else None,
            "budget": {"max_steps": max_steps, "max_seconds": max_seconds},
            "seeds": list(spec.seeds),
            "runs": results,
            "missing_units": missing_units(kind, spec, out),
            "figures": {},
            # How long the *run* took, which on a rebuild is not how long the rebuild took.
            "wall_seconds": (round(time.time() - started, 1) if todo
                             else previous.get("wall_seconds")
                             or round(sum(float(r.get("seconds") or 0) for r in results), 1)),
        }
        if not todo:
            summary["regenerated"] = stamp_now()
        summary.update(kind.summarise(spec, results, out, ctx) or {})
        # Provenance is the framework's to state, not the kind's. A kind that stamps its own
        # `git_sha` was, on a rebuild, asking git for today's HEAD and winning this merge.
        if summary.get("git_sha") != commit:
            log(f"note: {kind.name}.summarise set git_sha to {summary.get('git_sha')}; the "
                f"run's own provenance ({commit[:12]}) stands")
        summary["git_sha"] = commit
        atomic.write_text(out / RESULTS_NAME, json.dumps(summary, indent=2, default=str))
        log(f"summary -> {out / RESULTS_NAME}" + (f" ({len(summary['missing_units'])} units missing)"
                                                   if summary["missing_units"] else ""))
        completed = True
        return summary
    except (GateRefused, Refused) as exc:
        # A refusal is not a failure. It happens before any unit exists, so there is nothing to
        # mark and nothing to diagnose; a traceback here would read as a crash and bury the one
        # thing the human has to act on, which is the sentence saying what to fix.
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
