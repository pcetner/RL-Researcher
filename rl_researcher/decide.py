"""Record what the human decided about a finished run.

    python -m rl_researcher.decide <spec> [--note "..."]

The decision itself is made in the report: a box ticked in its authored decision region. This
reads that box, writes a ``decision`` row to the ledger, and refreshes the state page so the
run moves from "Awaiting you" to "Recently decided".

It refuses when no box is ticked. A decision the runner invents is not a decision, and a state
page that quietly moves a run out of the waiting list because a command was typed would hide
exactly the thing the page exists to show.

:func:`record` is that whole job as a function, and :func:`main` is the argv-and-print shim over
it. The split is so a second caller -- the dashboard -- can record a decision by calling the same
code rather than shelling out and reading its stdout back. Every refusal below is therefore
worded once and reaches both callers identically.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from rl_researcher.artefacts.state import _decision_region, options, ticked, write_state
from rl_researcher.cli import console, load_all, spec_parser
from rl_researcher.ledger import Finding, open_ledger
from rl_researcher.units import stamp_now


@dataclass(frozen=True)
class Decision:
    """What :func:`record` found and did.

    ``message`` is the whole thing a terminal should print, refusals included, so the command
    and the dashboard cannot word the same refusal two ways. The rest is the same content in
    parts, for a caller that wants to lay it out rather than print it.
    """

    code: int
    message: str
    chose: List[str] = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    finding: str = ""
    state: str = ""


def record(config: Any, spec: Any, out: Path, *, note: str = "", via: str = "cli") -> Decision:
    """Read the ticked box, write the ledger row, refresh the state page.

    ``via`` says how the decision reached here. It is provenance for an audit and nothing reads
    it to decide anything: a row recorded from the dashboard is otherwise identical to one typed
    at a terminal, down to the commit and the fingerprint, both of which come from the run's own
    ``results.json`` rather than from whoever is asking.
    """
    out = Path(out)
    artefact = out / "README.md"
    body = _decision_region(artefact)
    if body is None:
        return Decision(1, f"{artefact} has no decision region; nothing to record. "
                            f"(Has the run finished and written its report?)")
    chose = ticked(body)
    offered = options(body)
    if not chose:
        return Decision(1, f"no box is ticked in the decision region of {artefact}.\n"
                            f"  options: {', '.join(offered) or '(the stub lists none)'}\n"
                            f"  tick one, then run this again. A decision has to be made by a "
                            f"person.", options=offered)

    summary_path = out / "results.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    ledger = open_ledger(config)
    row = ledger.add(Finding(
        kind="decision", run=spec.name, date=stamp_now()[:10],
        note=note or "; ".join(chose),
        commit=str(summary.get("git_sha", ""))[:12],
        fingerprint=str(summary.get("fingerprint", "")),
        artefact=_rel(config.root, artefact),
        via=via,
        touches=list(getattr(spec, "decision_touches", []) or []),
    ))
    state = write_state(config, ledger=ledger)
    return Decision(0, f"{spec.name}: decided — {'; '.join(chose)}  [{row.id}]",
                    chose=chose, options=offered, finding=row.id, state=str(state))


def main(argv=None) -> int:
    console()
    p = spec_parser(__doc__.split("\n\n")[0])
    p.add_argument("--note", default="", help="the reasoning, in the human's words")
    a = p.parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)

    decision = record(config, spec, out, note=a.note)
    print(decision.message)
    if decision.state:
        print(f"state -> {decision.state}")
    return decision.code


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


if __name__ == "__main__":
    sys.exit(main())
