"""Ask every check that does not need a run, and say what is wrong with the project.

    python -m rl_researcher.lint [--list] [--stage lint,ci] [--fix-nothing]

`check` asks about one spec before one run. This asks about the whole project: the documents on
disk, the skills, the lessons file, and every spec under ``[paths].specs``. It is what the git
hook runs, and what a session runs before deciding anything.

Exit 1 on any error finding, 0 otherwise. Warnings are printed and pass — the rule being that an
error is something which, acted on after the fact, would mean discarding work.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from rl_researcher.checks import STAGES, catalogue, run_checks
from rl_researcher.cli import console
from rl_researcher.config import kind_for, load_config, out_dir_for
from rl_researcher.kinds import Finding

#: Asked once for the whole project.
PROJECT_STAGES = ("lint", "ci")

#: Asked once per spec. `run` is in here because C06 -- do not commit a run's outputs while it
#: is running -- is a run-stage question whose whole point is to be asked at the moment someone
#: is about to make the mistake, which is a commit rather than a launch.
SPEC_STAGES = ("load", "check", "pin", "run")


def lint(config, *, stages: List[str]) -> List[Finding]:
    """Every finding, project-wide. One unreadable spec is a finding, not a stop."""
    found: List[Finding] = []
    root = Path(config.root)
    for stage in stages:
        if stage in PROJECT_STAGES:
            found += run_checks(stage, config=config, root=root)
    specs = Path(config.path("specs"))
    for path in sorted(specs.glob("*.toml")) if specs.is_dir() else []:
        try:
            kind = kind_for(path, config)
            spec = kind.load(path)
        except Exception as exc:  # noqa: BLE001 - a spec that stopped parsing is a finding
            found.append(Finding(check="spec", level="error",
                                 message=f"{path.name}: {type(exc).__name__}: {exc}"))
            continue
        out = out_dir_for(spec, config)
        for stage in stages:
            if stage in SPEC_STAGES:
                for f in run_checks(stage, spec, kind, config, out=out, root=root):
                    found.append(Finding(check=f.check, level=f.level,
                                         message=f"{spec.name}: {f.message}"))
    return found


def main(argv=None) -> int:
    console()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--list", action="store_true", help="print the check catalogue and stop")
    p.add_argument("--stage", default=",".join(PROJECT_STAGES + SPEC_STAGES),
                   help=f"comma-separated, from {', '.join(STAGES)}")
    a = p.parse_args(argv)

    if a.list:
        print(f"{'id':4} {'stage':6} {'lesson':7} what")
        for c in catalogue():
            print(f"{c.id:4} {c.stage:6} {c.lesson:7} {c.what}")
        return 0

    stages = [s.strip() for s in a.stage.split(",") if s.strip()]
    unknown = [s for s in stages if s not in STAGES]
    if unknown:
        print(f"unknown stage(s): {', '.join(unknown)}; known: {', '.join(STAGES)}")
        return 1

    # The `ci` checks are about the tooling itself and need no project: C10 and C11 are what
    # this package runs against its own skills and lessons. Outside a project, ask those and
    # say plainly that the rest had nothing to ask about, rather than raising.
    try:
        config = load_config()
    except FileNotFoundError as exc:
        outside = [s for s in stages if s in PROJECT_STAGES]
        if not outside:
            print(f"{exc}")
            return 1
        found = []
        for stage in outside:
            found += run_checks(stage, root=Path.cwd())
        for f in found:
            print(f"  [{f.check}] {f.level}: {f.message}")
        skipped = [s for s in stages if s not in PROJECT_STAGES]
        n = len([f for f in found if f.level == "error"])
        print(f"no project here: asked {', '.join(outside)}"
              + (f", skipped {', '.join(skipped)} (they need a spec)" if skipped else "")
              + f" — {n} error(s), {len(found) - n} warning(s)")
        return 1 if any(f.level == "error" for f in found) else 0

    found = lint(config, stages=stages)
    errors = [f for f in found if f.level == "error"]
    for f in sorted(found, key=lambda f: (f.level != "error", f.check, f.message)):
        print(f"  [{f.check}] {f.level}: {f.message}")
    if not found:
        print(f"{len(catalogue())} checks, nothing to report")
    else:
        print(f"{len(errors)} error(s), {len(found) - len(errors)} warning(s) "
              f"from {len(catalogue())} checks")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
