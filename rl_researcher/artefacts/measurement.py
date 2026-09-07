"""A measurement's document: one question, the method, the numbers, one verdict.

A measurement is not a run. It has no arms, no seeds and no bars, because it is not testing a
prediction — it is answering a question about data that already exists, usually to work out
which family of fix a null implicates. So it gets its own layout, and the thing it must be
prevented from doing is the opposite of a report's: a report must not let a post-hoc story be
read as a prediction, and a measurement must not let a number be read as a registered result.
The ``## Post-hoc`` framing lives in the diagnosis layout; here it is the ``Question`` section,
which is written before the numbers and says what was being asked.

Everything below is generic. What a particular measurement asks, how it says it, and what it
concluded come from its kind — see :class:`rl_researcher.measurement.MeasurementKind`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rl_researcher.artefacts.writer import Artefact, stamp
from rl_researcher.artefacts.writer import write as write_artefact
from rl_researcher.blocks.text import Banner, Figure, Footer, KV, Prose
from rl_researcher.ledger import Ledger


def _posix(path: Any) -> str:
    return str(path).replace("\\", "/") if path else ""


def _pairs(kind: Any, name: str, *args: Any) -> List[Tuple[str, str]]:
    fn = getattr(kind, name, None)
    if not callable(fn):
        return []
    return [(str(k), str(v)) for k, v in (fn(*args) or [])]


def _text(kind: Any, name: str, *args: Any) -> str:
    fn = getattr(kind, name, None)
    if callable(fn):
        return str(fn(*args) or "")
    return str(fn or "")


def build(spec: Any, payload: Dict[str, Any], out: Path, *, kind: Any = None,
          ledger: Optional[Ledger] = None, command: str = "") -> Artefact:
    """The measurement as an :class:`Artefact`, before it meets what is on disk."""
    art = Artefact(kind="measurement", title=getattr(spec, "name", str(spec)),
                   header=stamp(run=getattr(spec, "name", ""),
                                commit=str(payload.get("git_sha", ""))[:12],
                                data=str(payload.get("snapshot", "") or "")))

    question = _text(kind, "question", spec, payload) or getattr(spec, "hypothesis", "")
    art.say("question", question.strip())

    art.add("method", KV(title="Method", pairs=_pairs(kind, "method", spec, payload)))

    # A measurement that could not calibrate is not a measurement. Say so at the top of the
    # numbers rather than in a line the reader reaches after believing them.
    for note in (getattr(kind, "notices", None) or (lambda *_: [])) (spec, payload) or []:
        art.add("result", Banner("Not calibrated.", str(note), tone="crit"))

    # A table is markdown, and the page beside this file is rendered from that markdown, so a
    # table written here renders as a table in both. That is the whole reason there is one
    # document: the page cannot say something the file does not.
    for title, body in (getattr(kind, "tables", None) or (lambda *_: [])) (spec, payload) or []:
        head = f"**{title}**" + "\n\n" if title else ""
        art.add("result", Prose(text=head + str(body).strip()))

    figs = payload.get("figures") or {}
    art.add("figures", *[Figure(src=_posix(src), caption=_caption(kind, name), alt=name)
                         for name, src in sorted(figs.items())])

    verdict = _text(kind, "verdict", spec, payload)
    if verdict:
        art.say("verdict", verdict.strip())

    art.add("provenance", KV(title="Provenance", pairs=_provenance(spec, payload, kind)),
            Footer(text=f"Generated from {out.name}/{payload.get('source', 'results.json')}.",
                   command=command))

    rows: List[Tuple[str, str, str]] = []
    if ledger is not None:
        rows = [(f.id, f"{f.run} {f.metric}", str(f.value))
                for f in ledger.extend(_findings(spec, payload, kind, out))]
    from rl_researcher.blocks.text import Claims

    art.add("ledger", Claims(rows=rows))

    art.stub("reading", "_(write here: what this measurement cannot show. It answers one "
                        "question about data that already exists; it registers nothing.)_")
    art.stub("decision", _text(kind, "decision_stub", spec, payload)
             or "_(no decision is registered on a measurement; say here if one is implied.)_")
    return art


def _caption(kind: Any, name: str) -> str:
    captions = getattr(kind, "figure_captions", None) or {}
    return str(captions.get(name, ""))


def _provenance(spec: Any, payload: Dict[str, Any], kind: Any) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = [
        ("Measured", str(payload.get("measured", "") or "—")),
        ("Commit", str(payload.get("git_sha", "unknown"))[:12]),
    ]
    if payload.get("snapshot"):
        pairs.append(("Snapshot", str(payload["snapshot"])))
    if payload.get("device"):
        pairs.append(("Device", str(payload["device"])))
    if payload.get("seconds"):
        pairs.append(("Wall", f"{float(payload['seconds']):.0f}s"))
    source = getattr(spec, "source_path", None)
    if source:
        pairs.append(("Spec", _posix(source)))
    pairs += _pairs(kind, "provenance", spec, payload)
    # A measurement trains nothing, and saying so is the point: it is what makes it cheap to
    # re-run and what stops it being read as another result.
    pairs.append(("Trains", "nothing"))
    return pairs


def _findings(spec: Any, payload: Dict[str, Any], kind: Any, out: Path) -> Sequence[Any]:
    """Post-hoc rows, from the kind. A measurement never writes a `registered` row: nothing
    about it was declared before the numbers existed."""
    from rl_researcher.ledger import Finding

    fn = getattr(kind, "findings", None)
    if not callable(fn):
        return []
    rows = []
    for got in fn(spec, payload) or []:
        if isinstance(got, Finding):
            rows.append(got)
        else:
            rows.append(Finding(kind="post-hoc", run=getattr(spec, "name", ""),
                                artefact=str(out.name), **got))
    return rows


def write_measurement(spec: Any, payload: Dict[str, Any], out: Path, *, kind: Any = None,
                      ledger: Optional[Ledger] = None, command: str = "",
                      name: str = "README.md") -> Path:
    """Write the measurement's markdown and the page beside it, keeping what a person wrote."""
    art = build(spec, payload, Path(out), kind=kind, ledger=ledger, command=command)
    return write_artefact(art, Path(out) / name, subtitle=str(payload.get("measured", "")))
