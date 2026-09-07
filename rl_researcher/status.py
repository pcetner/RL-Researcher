"""Where a run stands, from disk only: the authority on liveness.

    python -m rl_researcher.status <spec> [--out DIR]

Exits 2 when any unit is stale or has failed, and 0 otherwise, including when units have not
started, so a monitor calling it on a timer gets a signal that means something. A stale unit
is a hang until proven otherwise; never wait it out.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rl_researcher.kinds import RunKind
from rl_researcher.lock import lock_holder
from rl_researcher.spec import RunSpec
from rl_researcher.units import UnitState, read_unit, unit_dir

STATE_RANK = {"FAILED": 0, "STALE": 1, "running": 2, "stopped": 3, "finished": 4, "not started": 5}


@dataclass
class RunStatus:
    run: str
    kind: str
    out: Path
    lock: Optional[Dict] = None
    units: List[UnitState] = field(default_factory=list)
    state: str = "not started"
    tone: str = "muted"

    @property
    def bad(self) -> bool:
        return any(u.stale or u.failed for u in self.units)

    @property
    def done(self) -> int:
        return sum(1 for u in self.units if u.done)

    @property
    def finished(self) -> bool:
        return bool(self.units) and all(u.done for u in self.units)

    def to_dict(self) -> Dict:
        return {"run": self.run, "kind": self.kind, "out": str(self.out), "lock": self.lock,
                "state": self.state, "tone": self.tone, "done": self.done, "total": len(self.units),
                "units": [u.to_dict() for u in self.units]}


def state_of(units: List[UnitState], lock: Optional[Dict]) -> Tuple[str, str]:
    """One word for the whole run, worst news first, and the tone a chip shows it in."""
    if any(u.failed for u in units):
        return "FAILED", "crit"
    if any(u.stale for u in units):
        return "STALE", "crit"
    if units and all(u.done for u in units):
        return "finished", "ok"
    if any(u.live for u in units) and (lock is None or lock.get("alive") is not False):
        return "running", "accent"
    if any(u.resumable or u.status in ("stopped", "incomplete") or u.live for u in units):
        return "stopped", "warn"
    if any(u.done for u in units):
        return "stopped", "warn"
    return "not started", "muted"


def run_status(spec: RunSpec, kind: RunKind, out: Path) -> RunStatus:
    out = Path(out)
    units = [read_unit(unit_dir(out, u), unit=u, heartbeat_seconds=spec.cadence.heartbeat_seconds)
             for u in kind.units(spec)]
    lock = lock_holder(out)
    state, tone = state_of(units, lock)
    return RunStatus(run=spec.name, kind=kind.name, out=out, lock=lock, units=units, state=state, tone=tone)


def format_status(st: RunStatus) -> List[str]:
    lines: List[str] = []
    held = st.lock
    if held is not None and held.get("alive") is False:
        lines.append(f"** the process that owned this run (pid {held.get('pid')}, started "
                     f"{held.get('started', '?')}) is no longer alive **")
        lines.append("   re-run the same `run` command to continue from the checkpoints below")
    elif held is not None:
        where = "this machine" if held.get("alive") else held.get("host", "another machine")
        lines.append(f"a run is in progress (pid {held.get('pid')} on {where}, started {held.get('started', '?')})")
    width = max([len(u.arm) for u in st.units] + [8])
    for u in st.units:
        head = f"{u.arm:>{width}} seed {u.seed}:"
        if u.status == "not started":
            lines.append(f"{head} not started")
        elif u.failed:
            lines.append(f"{head} ** FAILED ** at step {u.step if u.step is not None else '?'}: "
                         f"{u.error or 'see the run log'}")
        elif u.done:
            lines.append(f"{head} done, {u.step} steps in {u.elapsed_seconds or 0:.0f}s"
                         + (f" (resumed from {u.resumed_from_step})" if u.resumed_from_step is not None else ""))
        else:
            eta = "" if u.eta_seconds is None else f" eta {u.eta_seconds / 60:.0f} min"
            age = "?" if u.age is None else f"{u.age:.0f}"
            stale = "  ** STALE: no heartbeat **" if u.stale else ""
            resumable = (f"  (resumable from step {u.checkpoint_step}: re-run the same `run` command)"
                         if u.resumable and not u.live else "")
            lines.append(f"{head} {u.status:<11} step {u.step if u.step is not None else '?'}/"
                         f"{u.max_steps if u.max_steps is not None else '?'} {u.rate or 0:.2f} step/s{eta} "
                         f"(updated {age}s ago){stale}{resumable}")
    lines.append(f"state: {st.state} ({st.done}/{len(st.units)} units done)")
    return lines


def print_status(st: RunStatus, log=print) -> int:
    for line in format_status(st):
        log(line)
    return 2 if st.bad else 0


def main(argv=None) -> int:
    from rl_researcher.cli import console
    from rl_researcher.config import kind_for, load_config, out_dir_for

    console()
    p = argparse.ArgumentParser(description="where a run stands; exit 2 on a stale or failed unit")
    p.add_argument("spec")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    config = load_config()
    kind = kind_for(a.spec, config)
    spec = kind.load(Path(a.spec))
    out = Path(a.out) if a.out else out_dir_for(spec, config)
    return print_status(run_status(spec, kind, out))


if __name__ == "__main__":
    sys.exit(main())
