"""Run a spec: every unit without a result, resumable, never silent, gated on cost.

    python -m rl_researcher.run <spec> [--out DIR] [--max-steps N] [--max-seconds S] [--units a,b]
                                       [--no-resume] [--allow-guards] [--no-gate]

Re-running the same command continues a stopped run. Exit codes: 0 done, 1 failed, 2 locked
(another process holds the output directory), 3 gated without an approval, 4 blocked by a
guard.
"""

from __future__ import annotations

import sys

from rl_researcher.cli import console, load_all, spec_parser
from rl_researcher.estimate import add_budget_args
from rl_researcher.gate import GateRefused, enforce
from rl_researcher.lock import RunLocked
from rl_researcher.runner import GuardBlocked, run
from rl_researcher.stop import HotStop


def main(argv=None) -> int:
    console()
    p = spec_parser(__doc__.split("\n\n")[0])
    add_budget_args(p)
    p.add_argument("--no-resume", action="store_true", help="start every unit over")
    p.add_argument("--allow-guards", action="store_true", help="run even when a kind's guard is blocked")
    p.add_argument("--no-gate", action="store_true", help="skip the cost gate (say why in the log)")
    a = p.parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)
    units = a.units.split(",") if a.units else None
    page_writer = None
    try:
        from rl_researcher.artefacts.dashboard import page_writer_factory as page_writer  # type: ignore
    except ImportError:
        page_writer = None
    kwargs: dict = {} if page_writer is None else {"page_writer": page_writer}
    try:
        run(spec, kind, out, config=config, resume=not a.no_resume, units=units, max_steps=a.max_steps,
            max_seconds=a.max_seconds, allow_guards=a.allow_guards, gate=None if a.no_gate else enforce, **kwargs)
    except GateRefused as exc:
        print(f"refused: {exc}")
        return 3
    except RunLocked as exc:
        print(f"locked: {exc}")
        return 2
    except GuardBlocked as exc:
        print(f"blocked: {exc}")
        return 4
    except HotStop as exc:
        print(f"stopped: {exc}")
        return 0
    except Exception as exc:  # noqa: BLE001 - the runner already logged the traceback
        print(f"failed: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
