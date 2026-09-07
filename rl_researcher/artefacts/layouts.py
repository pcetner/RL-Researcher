"""The section order of each artefact kind, and the check that a file still has it.

Every instance of a kind has the same headings in the same order, so a reader learns the shape
once and afterwards knows where to look. That is not tidiness: the reason a report puts the
registered outcome first and the reading eighth is that the outcome was fixed before the run and
the reading was written after it, and a document that lets those two swap places is a document
in which a post-hoc story can be read as a prediction.

Each section says who owns it. ``generated`` is rewritten from disk every time; ``authored`` is
written by a person or by the LLM and is preserved byte for byte across regeneration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class Section:
    heading: str
    region: str          # the region argument, e.g. "summary"
    owner: str           # "generated" | "authored"
    why: str = ""


@dataclass(frozen=True)
class Layout:
    kind: str
    sections: Sequence[Section]

    @property
    def headings(self) -> List[str]:
        return [s.heading for s in self.sections]

    def section(self, region: str) -> Section:
        return next(s for s in self.sections if s.region == region)


REPORT = Layout("report", (
    Section("Summary", "summary", "generated",
            "the registered outcome, as the conjunction the spec declared, before anything else"),
    Section("Registered metrics", "metrics", "generated",
            "every metric x arm with its bar, its spread and how many seeds it is over"),
    Section("Units", "units", "generated", "what each unit actually did, including the ones that did not finish"),
    Section("Figures", "figures", "generated", "the run-level pictures"),
    Section("Evidence", "evidence", "generated", "per-unit images, linked in markdown and shown in the page"),
    Section("Provenance", "provenance", "generated", "what would have to be true to reproduce this"),
    Section("Ledger", "ledger", "generated", "the finding ids this run wrote"),
    Section("Reading", "reading", "authored",
            "what the numbers say, what the pictures say, and where they disagree - every number cited"),
    Section("Decision (human)", "decision", "authored", "the go / iterate / stop box and the notes"),
))

MEASUREMENT = Layout("measurement", (
    Section("Question", "question", "generated", "the one sentence this was run to answer"),
    Section("Method", "method", "generated", "the script, its inputs, the data hash, how long it took"),
    Section("Result", "result", "generated", "the tables the script computed"),
    Section("Figure", "figures", "generated", ""),
    Section("Verdict", "verdict", "generated", "the one line the script itself concluded"),
    Section("Provenance", "provenance", "generated", ""),
    Section("Ledger", "ledger", "generated", "post-hoc rows"),
    Section("Reading", "reading", "authored", "what this cannot show"),
    Section("Decision (human)", "decision", "authored", "only when the spec declared options"),
))

DIAGNOSIS = Layout("diagnosis", (
    Section("Post-hoc", "posthoc", "generated",
            "run after seeing which result; changes nothing about that run's registered outcome"),
    Section("Question", "question", "authored", "the reading stated as a claim that could be refuted"),
    Section("Sources", "sources", "generated", "the runs and ledger ids this draws on"),
    Section("Findings", "findings", "generated", "the measurements it ran"),
    Section("Figures", "figures", "generated", ""),
    Section("Reading", "reading", "authored", "what is ruled out, and what is not"),
    Section("Consequence", "consequence", "authored", "which families of fix survive"),
))

STATE = Layout("state", (
    Section("Awaiting you", "waiting", "generated", "worst news first: what has been sitting unread"),
    Section("Running", "running", "generated", ""),
    Section("Queued", "queued", "generated", ""),
    Section("Recently decided", "decided", "generated", ""),
    Section("Health", "health", "generated", "the machinery itself"),
))

LAYOUTS: Dict[str, Layout] = {la.kind: la for la in (REPORT, MEASUREMENT, DIAGNOSIS, STATE)}


class LayoutError(ValueError):
    pass


def layout_for(kind: str) -> Layout:
    if kind not in LAYOUTS:
        raise LayoutError(f"no layout for artefact kind {kind!r}; known: {sorted(LAYOUTS)}")
    return LAYOUTS[kind]


def check_layout(markdown: str, kind: str) -> List[str]:
    """What is wrong with this file's shape. Empty means it matches its kind.

    Extra headings are allowed after the last required one (a kind may say more than the
    minimum); missing ones and out-of-order ones are not.
    """
    from rl_researcher.regions import headings

    want = layout_for(kind).headings
    got = headings(markdown)
    problems = []
    missing = [h for h in want if h not in got]
    if missing:
        problems.append(f"missing section(s): {', '.join(missing)}")
    present = [h for h in got if h in want]
    if present != [h for h in want if h in got]:
        problems.append(f"sections out of order: expected {want}, found {present}")
    return problems


def assert_layout(markdown: str, kind: str) -> None:
    problems = check_layout(markdown, kind)
    if problems:
        raise LayoutError(f"{kind}: " + "; ".join(problems))


def skeleton(kind: str, title: str, header: Sequence[Tuple[str, str]] = ()) -> str:
    """An empty artefact of this kind: the stamp, the title, and every section as an empty
    region. What a writer fills in, and what a regeneration writes into."""
    from rl_researcher.regions import Region

    stamp = " ".join(f"{k}={v}" for k, v in header)
    out = [f"<!-- rl: kind={kind} {stamp} -->".replace("  ", " ").replace(" -->", " -->"), f"# {title}", ""]
    for s in layout_for(kind).sections:
        out += [f"## {s.heading}", "", Region(kind=s.owner, arg=s.region, body="").rendered(), ""]
    return "\n".join(out).rstrip("\n") + "\n"
