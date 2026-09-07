"""Copy the framework's Claude Code skills into the user's skills folder.

    python -m rl_researcher.install_skills [--dest ~/.claude/skills] [--dry-run]

Each directory under the package's ``skills/`` holding a ``SKILL.md`` is copied whole,
overwriting what is there, so re-running after an upgrade updates them. Prints one line per
file that changed. Skills live in the user folder rather than a project's ``.claude/skills`` so
every repository that imports the package gets them.

Four are shipped: ``rl-researcher``, ``rl-design``, ``rl-operate`` and ``rl-interpret``. They
share one byte-identical invariants block, which C10 checks.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path
from typing import List

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def default_dest() -> Path:
    return Path.home() / ".claude" / "skills"


def install(dest: Path, *, dry_run: bool = False, source: Path = SKILLS_DIR) -> List[str]:
    """Copy every skill directory under ``source`` into ``dest``; return the changed paths."""
    if not source.is_dir():
        # The normal state of a fresh clone: the skills are not written yet, and are not
        # shipped in the wheel either. Nothing to install is not an error, and a traceback
        # here would read as a broken install rather than as an empty one.
        return []
    changed: List[str] = []
    for skill in sorted(p for p in source.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
        for src in sorted(p for p in skill.rglob("*") if p.is_file()):
            rel = src.relative_to(source)
            dst = dest / rel
            if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
                continue
            changed.append(str(rel).replace("\\", "/"))
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    return changed


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dest", default=None, help="skills folder (default ~/.claude/skills)")
    p.add_argument("--dry-run", action="store_true", help="report what would change, copy nothing")
    a = p.parse_args(argv)
    dest = Path(a.dest) if a.dest else default_dest()
    changed = install(dest, dry_run=a.dry_run)
    verb = "would update" if a.dry_run else "updated"
    for rel in changed:
        print(f"{verb} {dest / rel}")
    if not changed:
        print(f"no skills to install: {SKILLS_DIR} holds none"
              if not SKILLS_DIR.is_dir() or not any(SKILLS_DIR.iterdir())
              else f"skills in {dest} are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
