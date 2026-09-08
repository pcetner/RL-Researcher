"""Writers for the documents a run leaves behind.

Each module here turns a finished run's files into one artefact kind: the run report
(`run_report`), the measurement (`measurement`), the project's state page (`state`), the live
dashboard and the index over every run (`dashboard`). They are built on the shared writer
(`writer`), the per-kind section orders (`layouts`) and the statistics every artefact agrees on
(`report`). The diagnosis writer is not written yet.

They share the block inventory and one stylesheet, so every instance of a kind has the same
shape.

:func:`write_artefact_for` is the one place that decides *which* of them a finished run gets.
It lives here rather than in a command because two callers need it and they must not be able to
disagree: ``python -m rl_researcher.report``, and the watcher acting on a run that finished with
nobody watching.
"""

from pathlib import Path
from typing import Any, Dict, Optional


def write_artefact_for(spec: Any, summary: Dict[str, Any], out: Path, *, kind: Any = None,
                       ledger: Optional[Any] = None, command: str = "") -> Path:
    """Write the document this run's kind calls for, and return the markdown path.

    Which document a run gets is the kind's to say, through ``artefact_kind``. A measurement is
    not a report: it has no arms, no bars and nothing that was predicted, and laying it out as
    one would present numbers that answer a question as though they had settled a registered
    comparison — and would write a ``registered`` ledger row for a run that registered nothing,
    which an append-only ledger cannot take back.
    """
    out = Path(out)
    command = command or f"python -m rl_researcher.report {getattr(spec, 'name', '')}"
    if getattr(kind, "artefact_kind", "report") == "measurement":
        from rl_researcher.artefacts.measurement import write_measurement
        from rl_researcher.measurement import MeasurementKind

        payload = (MeasurementKind.payload_of(kind, summary)
                   if isinstance(kind, MeasurementKind) else summary)
        return write_measurement(spec, payload, out, kind=kind, ledger=ledger, command=command)

    from rl_researcher.artefacts.run_report import write_report

    return write_report(spec, summary, out, kind=kind, ledger=ledger, command=command)


def page_for(md_path: Path, kind: Any = None) -> Path:
    """The HTML page :func:`write_artefact_for` writes beside ``md_path``.

    Beside the dispatch and keyed on the same attribute, because the two cannot be allowed to
    disagree: a report is ``README.md`` and ``report.html``, a measurement is ``README.md`` and
    ``README.html``, and `report` printed the report name for both -- so every measurement this
    package has ever written announced a page that was not there.

    A caller that wants the page without rewriting it -- to re-render after an edit, or to say
    where it is -- asks here rather than deriving a name of its own.
    """
    md_path = Path(md_path)
    if getattr(kind, "artefact_kind", "report") == "measurement":
        # `write_measurement` passes no `html_path`, so `writer.write` derives it from the stem.
        return md_path.with_suffix(".html")
    return md_path.with_name("report.html")
