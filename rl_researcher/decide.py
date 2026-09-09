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

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List

from rl_researcher.artefacts.state import _decision_region, options, ticked, write_state
from rl_researcher.cli import console, load_all, spec_parser


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


def record(config: Any, spec: Any, out: Path, *, note: str = "", via: str = "cli",
           evidence_revision: str = "", operation_id: str = "") -> Decision:
    from rl_researcher.workflow import submit, resolve
    from rl_researcher.workflow_store import WorkflowError, digest
    from rl_researcher.evidence import acknowledgement, read_evidence
    body = _decision_region(Path(out) / "README.md")
    chose = ticked(body)
    if not chose:
        return Decision(1, "no box is ticked. Options: " + ", ".join(options(body)), options=options(body))
    if all(acknowledgement(c) for c in chose) and not evidence_revision:
        kind, current, _ = resolve(config, spec.name)
        evidence_revision = read_evidence(current, kind, Path(out))["revision"]
    if not evidence_revision:
        return Decision(1, "An evidence revision is required. Read /api/run/<name> and pass --evidence-revision.")
    payload = {"run": spec.name, "choices": chose, "note": note, "via": via,
               "evidence_revision": evidence_revision}
    payload["operation_id"] = operation_id or digest(payload)
    try:
        receipt = submit(config, "decide", payload)
        state = write_state(config)
        return Decision(0, receipt["message"] + " " + ", ".join(chose), chose=chose, finding=receipt.get("finding", ""), state=str(state))
    except WorkflowError as exc:
        return Decision(1, str(exc), options=options(body))


def main(argv=None) -> int:
    console()
    p = spec_parser(__doc__.split("\n\n")[0])
    p.add_argument("--note", default="", help="the reasoning, in the human's words")
    p.add_argument("--evidence-revision", default="", help="revision of the evidence you considered")
    p.add_argument("--operation-id", default="", help="stable request ID for retries")
    a = p.parse_args(argv)
    config, kind, spec, out = load_all(a.spec, a.out)

    decision = record(config, spec, out, note=a.note, evidence_revision=a.evidence_revision, operation_id=a.operation_id)
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
