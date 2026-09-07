"""Record what the human decided about a finished run.

    python -m rl_researcher.decide <spec> [--note "..."]

The decision itself is made in the report: a box ticked in its authored decision region. This
reads that box, writes a ``decision`` row to the ledger, and refreshes the state page so the
run moves from "Awaiting you" to "Recently decided".

It refuses when no box is ticked. A decision the runner invents is not a decision, and a state
page that quietly moves a run out of the waiting list because a command was typed would hide
exactly the thing the page exists to show.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rl_researcher.artefacts.state import _decision_region, options, ticked, write_state
from rl_researcher.cli import console, load_all, spec_parser
from rl_researcher.ledger import Finding, open_ledger
from rl_researcher.units import stamp_now


def main(argv=None) -> int:
    console()
    p = spec_parser(__doc__.split("\n\n")[0])
    p.add_argument("--note", default="", help="the reasoning, in the human's words")
    a = p.parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)

    artefact = out / "README.md"
    body = _decision_region(artefact)
    if body is None:
        print(f"{artefact} has no decision region; nothing to record. "
              f"(Has the run finished and written its report?)")
        return 1
    chose = ticked(body)
    if not chose:
        print(f"no box is ticked in the decision region of {artefact}.")
        print("  options: " + (", ".join(options(body)) or "(the stub lists none)"))
        print("  tick one, then run this again. A decision has to be made by a person.")
        return 1

    summary_path = out / "results.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    ledger = open_ledger(config)
    row = ledger.add(Finding(
        kind="decision", run=spec.name, date=stamp_now()[:10],
        note=a.note or "; ".join(chose),
        commit=str(summary.get("git_sha", ""))[:12],
        fingerprint=str(summary.get("fingerprint", "")),
        artefact=_rel(config.root, artefact),
        touches=list(getattr(spec, "decision_touches", []) or []),
    ))
    print(f"{spec.name}: decided — {'; '.join(chose)}  [{row.id}]")
    state = write_state(config, ledger=ledger)
    print(f"state -> {state}")
    return 0


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


if __name__ == "__main__":
    sys.exit(main())
