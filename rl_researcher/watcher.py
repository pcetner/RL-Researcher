"""Watch every run, and do the things a person would otherwise have to remember.

    python -m rl_researcher.watcher [--once] [--interval S] [--quiet] [--dry-run]

One tick reads the status of every registered spec, compares it against the tick before, and
acts on what changed. What it does on a run that just finished is exactly what a person does:
write the report, upsert the ledger, rewrite the state page and the index. What it does on a
run that failed, went quiet, or blew past its own projection is tell someone.

What it does not do is start anything. ``[watcher] launch`` is off by default and there is no
code here that runs a unit: starting queued work is a decision, and a decision needs a person.
The watcher's whole job is to make sure the person finds out.

Its own record is ``<paths.logs>/watcher.log`` (a flushed line per event) and
``<paths.logs>/watcher.json`` (the last tick, which the state page's health section reads). The
JSON is what a tick is diffed against, so the watcher survives being killed and restarted: it
picks up from what it last wrote rather than from what it happens to remember.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from rl_researcher import atomic, notify
from rl_researcher.cli import console
from rl_researcher.config import Config, kind_for, load_config, out_dir_for
from rl_researcher.status import RunStatus, run_status

TICK_NAME = "watcher.json"

#: A state a run does not come back from on its own. Reaching one is the event; being in one is
#: not, or every tick would re-report every finished run in the project.
TERMINAL = ("finished", "FAILED")


@dataclass
class Change:
    """One thing that happened to one run between two ticks."""

    run: str
    kind: str
    what: str        # "finished" | "failed" | "stale" | "overrun" | "started"
    detail: str = ""
    did: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.run}: {self.what}"


@dataclass
class Seen:
    """What the last tick knew about one run. This is what a tick is diffed against."""

    state: str = "not started"
    done: int = 0
    total: int = 0
    started: Optional[float] = None         # epoch seconds, the first tick that saw it running
    projected_end: Optional[float] = None   # epoch seconds, set from that tick's own estimate
    overrun_told: bool = False
    stale_told: bool = False


def tick_path(config: Config) -> Path:
    d = Path(config.path("logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d / TICK_NAME


def read_last(config: Config) -> Dict[str, Seen]:
    """The previous tick, from disk. A watcher that keeps this only in memory forgets
    everything it was about to tell you the moment the machine sleeps."""
    path = tick_path(config)
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    runs = blob.get("runs") or {}
    out: Dict[str, Seen] = {}
    for name, row in runs.items():
        if isinstance(row, dict):
            out[str(name)] = Seen(**{k: v for k, v in row.items()
                                     if k in Seen.__dataclass_fields__})
    return out


def write_tick(config: Config, seen: Dict[str, Seen]) -> Path:
    payload = {"tick": notify.stamp(), "runs": {k: asdict(v) for k, v in seen.items()}}
    return atomic.write_text(tick_path(config), json.dumps(payload, indent=2) + "\n")


def statuses(config: Config, log: Callable[[str], None]) -> Dict[str, RunStatus]:
    """Every registered run's status. One unreadable spec must not stop the others."""
    out: Dict[str, RunStatus] = {}
    specs = Path(config.path("specs"))
    for path in sorted(specs.glob("*.toml")) if specs.is_dir() else []:
        try:
            kind = kind_for(path, config)
            spec = kind.load(path)
            out[spec.name] = run_status(spec, kind, out_dir_for(spec, config))
        except Exception as exc:  # noqa: BLE001 - one bad spec is not a reason to stop watching
            log(f"skipped {path.name}: {type(exc).__name__}: {exc}")
    return out


def _progress(st: RunStatus) -> tuple:
    done = st.done
    total = len(st.units)
    return done, total


def _eta(st: RunStatus) -> Optional[float]:
    """Seconds left over the whole run, from the units that have finished — the same
    ratio-of-sums the page draws, so the two cannot disagree about when it will end."""
    fin = [u for u in st.units if u.done and u.elapsed_seconds and u.step]
    if not fin:
        return None
    per_step = (sum(float(u.elapsed_seconds or 0) for u in fin)
                / max(sum(int(u.step or 0) for u in fin), 1))
    left = sum(max(int(u.max_steps or 0) - int(u.step or 0), 0)
               for u in st.units if not u.done)
    return per_step * left if left else 0.0


def changes(config: Config, before: Dict[str, Seen], now: Dict[str, RunStatus],
            *, clock: Callable[[], float] = time.time) -> tuple:
    """What happened since the last tick, and what the next tick should be diffed against."""
    seen: Dict[str, Seen] = {}
    found: List[Change] = []
    overrun_by = float(config.watcher.eta_overrun)
    for name, st in now.items():
        was = before.get(name, Seen())
        done, total = _progress(st)
        row = Seen(state=st.state, done=done, total=total, started=was.started,
                   projected_end=was.projected_end,
                   overrun_told=was.overrun_told, stale_told=was.stale_told)
        kind = st.kind

        if st.state == "running" and row.projected_end is None:
            eta = _eta(st)
            if eta:
                row.started = clock()
                row.projected_end = row.started + eta

        if st.state != was.state:
            row.stale_told = False           # a fresh state deserves a fresh warning
            if st.state == "finished" and was.state not in TERMINAL:
                found.append(Change(name, kind, "finished",
                                    f"{done}/{total} units; the report is ready to read"))
            elif st.state == "FAILED" and was.state != "FAILED":
                why = next((u.error or "" for u in st.units if u.failed), "")
                found.append(Change(name, kind, "failed",
                                    (why.splitlines() or [""])[0][:160] or "no reason recorded"))
            elif st.state == "running" and was.state != "running":
                found.append(Change(name, kind, "started", f"{total} units"))

        if st.state == "STALE" and not was.stale_told:
            quiet = min((u.age for u in st.units if u.age is not None), default=None)
            row.stale_told = True
            found.append(Change(name, kind, "stale",
                                f"no heartbeat for {int(quiet or 0)}s; the process may be gone"))

        # Overrun is measured against the projection this run made when it started, not
        # against the one it is making now: a run that keeps revising its estimate upward is
        # exactly the run that never trips a rolling threshold.
        if st.state == "running" and row.projected_end and not was.overrun_told:
            span = max(row.projected_end - _first_seen(was, row, clock), 1.0)
            over = clock() - row.projected_end
            if over >= overrun_by * span:
                row.overrun_told = True
                found.append(Change(name, kind, "overrun",
                                    f"{int(over / 60)} min past its own projection "
                                    f"of {int(span / 60)} min"))
        seen[name] = row
    return found, seen


def _first_seen(was: "Seen", row: "Seen", clock: Callable[[], float]) -> float:
    """When the run's projection was made. Stored implicitly: `projected_end` was set one tick
    after the run started, so the span it projected is what is left of it from here."""
    return float(row.started or clock())


def act(config: Config, change: Change, *, dry_run: bool = False) -> List[str]:
    """What a person would do about this change, done. Only ``finished`` has an action.

    A failure, a silence and an overrun are all things to be told about and none of them are
    things to do something about without knowing why — which is the person's part.
    """
    if change.what != "finished" or dry_run:
        return []
    from rl_researcher.artefacts.run_report import write_report
    from rl_researcher.ledger import open_ledger

    did: List[str] = []
    spec_path = Path(config.path("specs")) / f"{change.run}.toml"
    kind = kind_for(spec_path, config)
    spec = kind.load(spec_path)
    out = out_dir_for(spec, config)
    summary = json.loads((out / "results.json").read_text(encoding="utf-8"))
    ledger = open_ledger(config)
    before = len(ledger.rows)
    path = write_report(spec, summary, out, kind=kind, ledger=ledger,
                        command=f"python -m rl_researcher.report {spec.name}")
    did.append(f"report -> {path}")
    did.append(f"ledger -> {len(ledger.rows) - before} new finding(s)")
    return did


def refresh(config: Config, log: Callable[[str], None]) -> None:
    """Rewrite the two documents that are about the project rather than about one run."""
    from rl_researcher.artefacts.dashboard import write_index
    from rl_researcher.artefacts.state import write_state

    for what, fn in (("state", lambda: write_state(config)),
                     ("index", lambda: write_index(config))):
        try:
            log(f"{what} -> {fn()}")
        except Exception as exc:  # noqa: BLE001 - a page must never stop the watcher
            log(f"{what} failed: {type(exc).__name__}: {exc}")


def tick(config: Config, *, quiet: bool = False, dry_run: bool = False,
         clock: Callable[[], float] = time.time,
         log: Optional[Callable[[str], None]] = None) -> List[Change]:
    """One pass. Returns what changed, having already acted on and reported it."""
    say = log if log is not None else (lambda t: notify.write_line(config, t))
    before = read_last(config)
    now = statuses(config, say)
    found, seen = changes(config, before, now, clock=clock)
    for change in found:
        try:
            change.did = act(config, change, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - report it, keep watching
            change.did = [f"action failed: {type(exc).__name__}: {exc}"]
            say(f"{change.run}: {change.did[0]}\n{traceback.format_exc()}")
        body = "; ".join([change.detail] + change.did) if change.did else change.detail
        notify.notify(config, change.title, body, quiet=quiet)
    if dry_run:
        # Not even the tick file. Writing it would mean the next real tick saw no change and
        # skipped every report the dry run had just told you was ready -- a dry run that
        # silently costs you the thing it was previewing.
        return found
    if found:
        refresh(config, say)
    write_tick(config, seen)
    return found


def main(argv=None) -> int:
    console()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--once", action="store_true", help="one tick, then exit")
    p.add_argument("--interval", type=float, default=None,
                   help="seconds between ticks (default: [watcher] interval_seconds)")
    p.add_argument("--quiet", action="store_true", help="log every event but raise no toast")
    p.add_argument("--dry-run", action="store_true",
                   help="say what changed and write no report, ledger row, state or index")
    a = p.parse_args(argv)
    config = load_config()
    interval = a.interval if a.interval is not None else float(config.watcher.interval_seconds)
    notify.write_line(config, f"watcher up: every {interval:.0f}s over "
                              f"{len(list(Path(config.path('specs')).glob('*.toml')))} spec(s)"
                              + (" (dry run)" if a.dry_run else ""))
    while True:
        try:
            found = tick(config, quiet=a.quiet, dry_run=a.dry_run)
        except Exception as exc:  # noqa: BLE001 - a watcher that dies is a project unwatched
            notify.write_line(config, f"tick failed: {type(exc).__name__}: {exc}")
            notify.write_line(config, traceback.format_exc())
            found = []
        if a.once:
            print(f"{len(found)} change(s)")
            return 0
        time.sleep(max(interval, 5.0))


if __name__ == "__main__":
    sys.exit(main())
