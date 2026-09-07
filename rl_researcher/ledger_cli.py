"""Read and fill the findings ledger.

    python -m rl_researcher.ledger_cli show   [--touches D4] [--metric r2] [--run study5] [--kind registered]
    python -m rl_researcher.ledger_cli backfill [--dry-run]
    python -m rl_researcher.ledger_cli add    --kind post-hoc --run <name> --note "..." [--touches D4 ...]

``show`` prints the table a hypothesis cites, with a banner when the rows are not comparable.
``backfill`` walks every finished run in the project and writes the registered rows it is
missing; it is safe to run repeatedly, because a registered row's identity is
``(kind, run, unit, metric, fingerprint, commit)`` and one already on file is not written twice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from rl_researcher.cli import console
from rl_researcher.config import Config, load_config
from rl_researcher.kinds import load_kind
from rl_researcher.ledger import Finding, Ledger, findings_from_summary, open_ledger
from rl_researcher.spec import kind_of


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def committed_on(path: Path) -> str:
    """The date a result was committed, for a row written long after the run.

    A backfilled finding dated today would say Study 1 was found this morning, which is exactly
    the kind of quietly wrong provenance the ledger exists to prevent. Git knows when the
    result landed; if it does not, the row is left undated rather than misdated, and
    :meth:`Ledger.add` stamps it with today's date as a last resort.
    """
    import subprocess

    for target in _dated_candidates(path):
        try:
            # --follow through renames, and the *oldest* commit, not the newest. `git log -1`
            # would date a finding by the day its file was last touched, so moving a finished
            # run onto a new layout would redate every result it contains to the day of the
            # move -- which is how a backfill quietly claims Phase 4 was measured this morning.
            out = subprocess.run(
                ["git", "log", "--follow", "--format=%ad", "--date=short", "--", str(target)],
                capture_output=True, text=True, timeout=20, cwd=str(path.parent))
        except (OSError, subprocess.SubprocessError):
            return ""
        stamps = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
        if stamps:
            return stamps[-1]
    return ""


def _dated_candidates(summary: Path):
    """The summary first, then the units under it.

    A run that finished before this framework existed has no run-level summary until one is
    derived from its units, and a file created this morning is dated this morning. The units
    are the ones that were actually committed when the run happened, so they carry the date.
    """
    yield summary
    for unit in sorted(summary.parent.glob("*/seed*/results.json")):
        yield unit


def backfill(config: Config, ledger: Ledger, *, dry_run: bool = False) -> List[Finding]:
    """Registered rows for every run that has a summary on disk and no rows yet."""
    specs_dir = config.path("specs")
    written: List[Finding] = []
    for spec_path in sorted(specs_dir.glob("*.toml")) if specs_dir.is_dir() else []:
        try:
            kind_name = kind_of(spec_path)
            kind = load_kind(config.kind_entry(kind_name))
            spec = kind.load(spec_path)
        except Exception as exc:  # noqa: BLE001 - report it and keep going
            print(f"  {spec_path.name}: unreadable ({exc})")
            continue
        out = config.out_root(kind_name) / spec.name
        summary_path = out / "results.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = findings_from_summary(
            summary, spec,
            artefact=_rel(config.root, out / "README.md"),
            kind=kind,
            date=committed_on(summary_path),
        )
        new = [r for r in rows if not any(x.identity == r.identity for x in ledger.rows)]
        print(f"  {spec.name}: {len(rows)} row(s), {len(new)} new")
        if not dry_run:
            written += [ledger.add(r) for r in new]
        else:
            written += new
    return written


def _show(ledger: Ledger, a) -> int:
    rows = ledger.query(touches=a.touches, metric=a.metric, run=a.run, kind=a.kind, data=a.data)
    if not rows:
        print("no findings match")
        return 0
    for note in ledger.mixed(rows):
        print(f"!! {note}")
    gone = ledger.superseded
    for r in sorted(rows, key=lambda r: (r.date, r.run, r.unit, r.metric)):
        mark = " (superseded)" if r.id in gone else ""
        print(f"{r.line()}{mark}")
    print(f"\n{len(rows)} finding(s)")
    return 0


def main(argv=None) -> int:
    console()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="print the findings matching a filter")
    for flag in ("touches", "metric", "run", "kind", "data"):
        s.add_argument(f"--{flag}", default=None)

    b = sub.add_parser("backfill", help="write registered rows for every finished run")
    b.add_argument("--dry-run", action="store_true", help="say what would be written, write nothing")

    ad = sub.add_parser("add", help="append one row by hand (post-hoc, decision or lesson)")
    ad.add_argument("--kind", default="post-hoc")
    ad.add_argument("--run", default="")
    ad.add_argument("--metric", default="")
    ad.add_argument("--value", type=float, default=None)
    ad.add_argument("--note", default="")
    ad.add_argument("--artefact", default="")
    ad.add_argument("--touches", action="append", default=[])
    ad.add_argument("--supersedes", action="append", default=[])

    a = p.parse_args(argv)
    config = load_config()
    ledger = open_ledger(config)

    if a.cmd == "show":
        return _show(ledger, a)
    if a.cmd == "backfill":
        print(f"backfilling {ledger.path}")
        rows = backfill(config, ledger, dry_run=a.dry_run)
        verb = "would write" if a.dry_run else "wrote"
        print(f"{verb} {len(rows)} row(s); the ledger holds {len(ledger.rows)}")
        return 0
    if a.cmd == "add":
        if a.kind == "registered":
            print("registered rows are written by the report writer, not by hand: they have to "
                  "carry the run's fingerprint and commit. Use `backfill`, or re-run the report.")
            return 1
        row = ledger.add(Finding(kind=a.kind, run=a.run, metric=a.metric, value=a.value,
                                 note=a.note, artefact=a.artefact, touches=list(a.touches),
                                 supersedes=list(a.supersedes)))
        print(f"[{row.id}] {row.line()}")
        return 0
    return 1


def find_spec(config: Config, name: str) -> Optional[Path]:
    p = config.path("specs") / f"{name}.toml"
    return p if p.is_file() else None


if __name__ == "__main__":
    sys.exit(main())
