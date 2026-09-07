"""The run report: registered numbers beside their evidence, ending in a decision.

This is the artefact the human actually reads, and its order is an argument. The registered
outcome comes first, stated as the conjunction the spec declared before the run, so what the run
was *for* is settled before any picture is shown. The reading comes eighth, after every number
and every figure, and is marked as written by hand. Nothing in between is typed by anyone: the
tables, the units, the provenance and the ledger ids are regenerated from what is on disk, so
running the writer twice changes nothing but the timestamp.

The one thing a rewrite must never touch is the reading and the decision, and that is not a
convention here — :func:`rl_researcher.regions.preserve_authored` carries them across byte for
byte, and a test holds it to that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rl_researcher.artefacts.report import (aggregate, degenerate_comparison, fmt,
                                            judge, tied)
from rl_researcher.artefacts.writer import Artefact, stamp, write
from rl_researcher.blocks import (Banner, Claims, Figure, Footer, Gallery, KV, Lane, Mark,
                                  MetricRow, MetricsTable, Notices, Scorecard, Stub,
                                  Tiles, UnitsTable, fmt_duration, fmt_number, tone_of)
from rl_researcher.blocks.viz import SHAPES
from rl_researcher.ledger import Ledger, findings_from_summary

DECISION_OPTIONS = (
    ("go", "the result is what the run was for; build on it"),
    ("iterate", "what changes, and which registered metric should move"),
    ("stop", "the approach or the bar is wrong; reopen the question"),
)


def _arms(spec: Any, summary: Dict[str, Any]) -> List[str]:
    """The arms in the order the spec declares them, not the order they happened to finish."""
    declared = [str(a) for a in (getattr(spec, "arms", None) or [])]
    seen = []
    for r in summary.get("runs") or []:
        a = str(r.get("arm", r.get("variant", "")))
        if a and a not in seen:
            seen.append(a)
    return [a for a in declared if a in seen] + [a for a in seen if a not in declared]


def _reference(agg: Dict[str, Dict[str, tuple]], metric: Any, name: str) -> Optional[float]:
    if not metric.compare_to:
        return None
    ref = agg.get(metric.compare_to, {}).get(name)
    return None if ref is None else ref[0]


def outcome(spec: Any, summary: Dict[str, Any]) -> Tuple[str, Dict[str, List[str]]]:
    """The registered outcome: for each arm, which of the conjunction's bars it missed.

    When the spec declares a ``conjunction`` that is the test; otherwise every metric carrying
    a bar has to hold at once. Either way the sentence is fixed before the run, which is the
    only reason it can be believed after it.
    """
    names = [m.name for m in spec.metrics]
    agg = aggregate(summary.get("runs") or [], names)
    wanted = list(getattr(spec, "conjunction", []) or
                  [m.name for m in spec.metrics if m.bar is not None or m.compare_to])
    dead = degenerate(spec, summary, _arms(spec, summary))
    ties: Dict[str, List[str]] = {}
    missed: Dict[str, List[str]] = {}
    judged: Dict[str, int] = {}
    for arm in _arms(spec, summary):
        bad = []
        seen = 0
        for name in wanted:
            m = spec.metric(name)
            if m is None:
                continue
            mean, _s, _n, div = agg.get(arm, {}).get(name, (float("nan"), float("nan"), 0, 0))
            ref = _reference(agg, m, name)
            if name in dead:
                continue
            verdict = judge(m, mean, div, reference=ref, arm=arm)
            if verdict is not None:
                seen += 1
            if tied(m, mean, reference=ref, arm=arm):
                seen += 1
                ties.setdefault(arm, []).append(name)
                bad.append(name)          # a tie is not a pass; the sentence below says which
            elif verdict is False:
                bad.append(name)
        missed[arm] = bad
        judged[arm] = seen
    if not wanted:
        return "No bars were registered, so this run reports rather than decides.", missed
    # An arm none of whose bars could be judged has not cleared them. Nothing said no, but
    # nothing said yes either, and "cleared every registered bar" printed over a column of
    # `n/a` is the most confident sentence in the document making the least contact with it.
    unjudged = [a for a in missed if not judged.get(a)]
    cleared = [a for a, bad in missed.items() if not bad and judged.get(a)]
    parts = []
    if cleared:
        parts.append("**Cleared every registered bar:** " + ", ".join(f"`{a}`" for a in cleared) + ".")
    if unjudged:
        parts.append("**Not judged:** " + ", ".join(f"`{a}`" for a in unjudged)
                     + " — no registered bar produced a verdict for "
                     + ("them" if len(unjudged) > 1 else "it") + ".")
    for arm, bad in missed.items():
        if not bad:
            continue
        drew = [n for n in bad if n in ties.get(arm, [])]
        lost = [n for n in bad if n not in drew]
        said = []
        if lost:
            said.append(f"missed {', '.join(lost)}")
        if drew:
            said.append(f"tied its control on {', '.join(drew)}")
        parts.append(f"`{arm}` " + " and ".join(said) + ".")
    if not cleared and not unjudged:
        parts.insert(0, "**No arm cleared every registered bar.**")
    return " ".join(parts), missed


def degenerate(spec: Any, summary: Dict[str, Any], arms: Sequence[str]) -> Dict[str, str]:
    """``{metric: why}`` for every registered comparison that cannot separate anything.

    A metric in here carries no mark on any arm. Saying "these three arms failed" when the
    measure did not move on this data is stating a result the run does not have, and it is how
    `parked_fraction` came out as three crosses against a bar of zero that nothing could be
    below.
    """
    agg = aggregate(summary.get("runs") or [], [m.name for m in spec.metrics])
    out: Dict[str, str] = {}
    for m in spec.metrics:
        stats = {a: agg.get(a, {}).get(m.name, (float("nan"), 0.0, 0, 0)) for a in arms}
        note = degenerate_comparison(m, stats)
        if note:
            out[m.name] = note
    return out


def metric_rows(spec: Any, summary: Dict[str, Any], arms: Sequence[str]) -> List[MetricRow]:
    agg = aggregate(summary.get("runs") or [], [m.name for m in spec.metrics])
    dead = degenerate(spec, summary, arms)
    rows = []
    for m in spec.metrics:
        cells, tones = [], []
        for arm in arms:
            mean, spread, n, div = agg.get(arm, {}).get(m.name, (float("nan"), float("nan"), 0, 0))
            ref = _reference(agg, m, m.name)
            if m.name in dead:
                verdict, mark = None, ""
            elif tied(m, mean, reference=ref, arm=arm):
                verdict, mark = None, " ="
            else:
                verdict = judge(m, mean, div, reference=ref, arm=arm)
                mark = "" if verdict is None else (" ✓" if verdict else " ✗")
            cells.append(fmt(mean, spread, n, div) + mark)
            tones.append("warn" if mark == " =" else tone_of(verdict))
        rows.append(MetricRow(name=m.name, cells=cells, tones=tones,
                              target=_target(m), why=m.why or ""))
    return rows


def _target(m: Any) -> str:
    if m.compare_to:
        return f"> `{m.compare_to}`" if m.direction == "higher" else f"< `{m.compare_to}`"
    if m.bar is None:
        return "report"
    # Strictly greater, strictly less: `passes` judges `v > bar`, so a column that reads
    # "at least 0.900" beside a value of exactly 0.900 marked failed is the document
    # disagreeing with itself about its own rule.
    return ("> " if m.direction == "higher" else "< ") + fmt_number(m.bar)


def lanes(spec: Any, summary: Dict[str, Any]) -> List[Lane]:
    """One lane per registered metric, one marker per finished unit.

    A marker per *unit*, not per arm: two seeds passing and one failing is a different result
    from three seeds scraping past, and only the mean would hide which happened.
    """
    agg = aggregate(summary.get("runs") or [], [m.name for m in spec.metrics])
    arms = _arms(spec, summary)
    dead = degenerate(spec, summary, arms)
    out = []
    for m in spec.metrics:
        ref = _reference(agg, m, m.name)
        bar = m.bar if not m.compare_to else ref
        marks = []
        for r in summary.get("runs") or []:
            arm = str(r.get("arm", r.get("variant", "")))
            value = (r.get("metrics") or {}).get(m.name)
            if value is None:
                continue
            marks.append(Mark(value=float(value), label=f"{arm} seed {r.get('seed')}",
                              passed=None if m.name in dead
                              else judge(m, float(value), reference=ref, arm=arm),
                              shape=SHAPES[arms.index(arm) % len(SHAPES)] if arm in arms else "circle",
                              seed=int(r.get("seed", 0))))
        out.append(Lane(metric=m.name, direction=m.direction, bar=bar, marks=marks,
                        target_text=_target(m).replace("`", "")))
    return out


def unit_rows(summary: Dict[str, Any]) -> Tuple[List[List[Any]], List[List[str]]]:
    rows, tones = [], []
    for r in summary.get("runs") or []:
        arm = r.get("arm", r.get("variant", ""))
        status = str(r.get("status", ""))
        resumed = r.get("resumed_from_step")
        rows.append([f"{arm}/seed{r.get('seed')}", r.get("steps"), fmt_duration(r.get("seconds")),
                     status, "" if resumed is None else f"resumed from {resumed}"])
        tones.append(["", "", "", "" if status == "complete" else "warn", "muted"])
    return rows, tones


def build(spec: Any, summary: Dict[str, Any], out: Path, *, kind: Any = None,
          ledger: Optional[Ledger] = None, figures_note: str = "",
          command: str = "") -> Artefact:
    """The report as an :class:`Artefact`, before it is merged with what is on disk."""
    arms = _arms(spec, summary)
    line, missed = outcome(spec, summary)
    runs = summary.get("runs") or []
    incomplete = [r for r in runs if r.get("status") not in ("complete", None)]
    missing = summary.get("missing_units") or summary.get("missing_cells") or []

    art = Artefact(kind="report", title=f"{spec.name}", header=stamp(
        run=spec.name, fingerprint=_fingerprint(spec, summary),
        commit=str(summary.get("git_sha", ""))[:12], data=_data(summary)))

    if getattr(spec, "screening", False):
        art.add("summary", Banner("Screening.", "One seed per arm. This narrows what to run next; "
                                  "it does not settle anything.", tone="accent"))
    if incomplete:
        art.add("summary", Banner(
            "Incomplete.", f"{len(incomplete)} unit(s) hit the time cap before the step budget. "
            "Their numbers are about a smaller budget than the one registered."))
    if missing:
        art.add("summary", Banner("Units missing.", f"{len(missing)} unit(s) have no result: "
                                  + ", ".join(map(str, missing))))

    # A registered comparison that cannot separate anything says so, above the marks it would
    # otherwise print. Registered against random, parked_fraction came out 0.000 for every arm
    # and three arms were marked failed for not being strictly below zero: the bar was not wrong
    # and the arms were not failing, the measure could not move on this data.
    for _name, note in degenerate(spec, summary, arms).items():
        art.add("summary", Banner("Comparison is degenerate.", note))

    art.say("summary", f"**Hypothesis.** {spec.hypothesis}\n\n{line}")
    # Wall clock comes from the summary when the runner recorded it, and from the units when it
    # did not: a report of a run from before that field existed should still say how long it took.
    wall = summary.get("wall_seconds") or sum(float(r.get("seconds") or 0) for r in runs) or None
    art.add("summary",
            Tiles(items=[(f"{len(runs) - len(missing)}/{len(runs) + len(missing)}", "units"),
                         (fmt_duration(wall), "unit time"),
                         (str(len(spec.seeds)), "seeds"),
                         (str(summary.get("budget", {}).get("max_steps", "?")), "step budget")]),
            MetricsTable(title="Headline", arms=arms,
                         rows=[r for r in metric_rows(spec, summary, arms)
                               if spec.metric(r.name) and (spec.metric(r.name).bar is not None
                                                           or spec.metric(r.name).compare_to)]))

    art.add("metrics", MetricsTable(title="Every registered metric", arms=arms,
                                    rows=metric_rows(spec, summary, arms), show_why=True),
            Scorecard(title="Scorecard", lanes=lanes(spec, summary)))

    rows, tones = unit_rows(summary)
    art.add("units", UnitsTable(title="Units", headers=("unit", "steps", "wall", "status", "note"),
                                rows=rows, tones=tones))
    failed = [(("crit"), f"{r.get('arm', r.get('variant'))}/seed{r.get('seed')}",
               str(r.get("error", "did not finish"))) for r in runs if r.get("status") == "failed"]
    if failed:
        art.add("units", Notices(title="Failed", items=failed))

    figs = summary.get("figures") or {}
    if figures_note:
        art.say("figures", figures_note)
    art.add("figures", *[Figure(src=str(src), caption=_caption(kind, name), alt=name)
                         for name, src in sorted(figs.items())])

    art.add("evidence", Gallery(title="Per unit", items=_evidence(runs),
                                note="Linked here; shown in the page beside this file."))

    art.add("provenance", KV(title="Provenance", pairs=_provenance(spec, summary)),
            Footer(text=f"Generated from {out.name}/results.json.", command=command))

    rows_written = []
    if ledger is not None:
        for f in ledger.extend(findings_from_summary(
                summary, spec, artefact=str(out.name), data=_data(summary), kind=kind)):
            rows_written.append((f.id, f"{f.unit} {f.metric}", fmt_number(f.value)))
    art.add("ledger", Claims(rows=rows_written))

    art.stub("reading", "_(write here: what the numbers say, what the pictures say, where they "
                        "disagree. Cite every registered number as `[F####]`.)_")
    art.stub("decision", Stub(options=DECISION_OPTIONS).md())
    return art


def _caption(kind: Any, name: str) -> str:
    captions = getattr(kind, "figure_captions", None) or {}
    return str(captions.get(name, ""))


def _evidence(runs: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    out = []
    for r in runs:
        label = f"{r.get('arm', r.get('variant'))} seed {r.get('seed')}"
        for what, src in sorted((r.get("evidence") or {}).items()):
            out.append((f"{label} · {what}", str(src)))
    return out


def _posix(path: Any) -> str:
    """A repo path written the way every other path in these documents is written."""
    return str(path).replace("\\", "/") if path else ""


def _fingerprint(spec: Any, summary: Dict[str, Any]) -> str:
    """The registration's identity. Recorded in the summary by the current runner; recomputed
    from the spec for a run that finished before the runner wrote it, which is better than
    printing a question mark for a value the spec on disk can still supply."""
    from rl_researcher.spec import spec_fingerprint

    return str(summary.get("fingerprint") or spec_fingerprint(spec))


def _data(summary: Dict[str, Any]) -> str:
    snap = summary.get("snapshot") or {}
    name = str(snap.get("dir", "")).rstrip("/").rsplit("/", 1)[-1]
    digest = str(snap.get("sha256", ""))[:8]
    return f"{name}@{digest}" if name and digest else name


def _provenance(spec: Any, summary: Dict[str, Any]) -> List[Tuple[str, str]]:
    budget = summary.get("budget") or {}
    cad = getattr(spec, "cadence", None)
    pairs = [
        ("Spec", f"{_posix(spec.source_path) or spec.name} "
                 f"(fingerprint {_fingerprint(spec, summary)})"),
        ("Commit", str(summary.get("git_sha", "unknown"))[:12]),
        ("Data", _data(summary) or "—"),
        ("Device", str(summary.get("device_name") or summary.get("device") or "unknown")),
        ("Budget", f"{budget.get('max_steps', '?')} steps or "
                   f"{fmt_duration(budget.get('max_seconds'))} per unit"),
        ("Seeds", ", ".join(str(s) for s in spec.seeds)),
    ]
    if cad is not None:
        pairs.append(("Cadence", f"heartbeat {cad.heartbeat_seconds:.0f}s, checkpoint every "
                                 f"{cad.checkpoint_every_steps} steps or "
                                 f"{cad.checkpoint_seconds:.0f}s"))
    if spec.decision_touches:
        pairs.append(("Touches", "; ".join(spec.decision_touches)))
    return pairs


def write_report(spec: Any, summary: Dict[str, Any], out: Path, *, kind: Any = None,
                 ledger: Optional[Ledger] = None, command: str = "") -> Path:
    """Write ``<out>/README.md`` and ``report.html``; return the markdown path."""
    out = Path(out)
    art = build(spec, summary, out, kind=kind, ledger=ledger,
                command=command or f"python -m rl_researcher.report {spec.name}")
    path = write(art, out / "README.md", subtitle=f"{spec.name} · {_data(summary)}")
    html = path.with_suffix(".html")
    if html.name != "report.html":
        import shutil

        shutil.move(str(html), str(out / "report.html"))
    return path
