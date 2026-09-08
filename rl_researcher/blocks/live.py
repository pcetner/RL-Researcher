"""The blocks a live page is made of.

A report says what a run found; these say where it is. They are richer than the report's blocks
in one way that matters: a cell here can carry a drawing — a lane with one marker per unit, a
sparkline of a curve so far, a progress track — because the question "is this alive and is it
going anywhere" is answered by a shape faster than by a column of numbers.

That richness is why the dashboard was written as raw HTML and stayed that way while everything
else moved onto blocks. It did not have to be: a block may draw, as long as it obeys the same
four rules. Its markdown states the numbers in words, its HTML states at least those numbers,
every class it emits is one it declares, and it fetches nothing. The drawing lives in
:mod:`rl_researcher.charts`, which these call, so the page looks exactly as it did.

Each block holds plain values and knows nothing about a spec, a kind or a summary. What to put
in one is decided upstream in :mod:`rl_researcher.artefacts.dashboard`, where it is tested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Optional, Sequence, Tuple

from rl_researcher import charts
from rl_researcher.charts import CHART_CSS
from rl_researcher.blocks.base import PANEL_CSS, Block, esc, panel, rows_to_md
from rl_researcher.style import tint


def _fmt(v: Any) -> str:
    """A number the way the page says it, so markdown and HTML cannot differ about one."""
    try:
        return charts.fmt(float(v))
    except (TypeError, ValueError):
        return "—"


def duration(seconds: Optional[float]) -> str:
    """Short enough for a table cell. Kept here so both renderings use the one function."""
    if seconds is None or (isinstance(seconds, float) and seconds != seconds):
        return "—"
    s = int(max(seconds, 0))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


# --------------------------------------------------------------------------- the lead

LEAD_CSS = f"""
  .lead {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap;
    background:var(--surface); border:1px solid var(--line); border-radius:10px;
    padding:10px 14px; margin-bottom:18px }}
  .lead .who {{ font-weight:600; font-size:13px; display:inline-flex; align-items:center; gap:7px }}
  .lead .who i {{ font-style:normal; color:var(--muted); font-weight:400; font-size:12px }}
  .lead .k {{ font-size:10.5px; text-transform:uppercase; letter-spacing:0.07em;
    color:var(--muted); font-weight:600 }}
  .stat {{ display:inline-flex; align-items:baseline; gap:7px; padding:3px 10px; border-radius:8px;
    border:1px solid var(--line); font-size:12px }}
  .stat b {{ font-size:15px; font-variant-numeric:tabular-nums }}
  .stat .n {{ color:var(--muted); font-size:10.5px; text-transform:uppercase; letter-spacing:0.05em }}
  .stat.ok {{ border-color:{tint("--ok", 27)}; background:{tint("--ok", 6)} }}
  .stat.ok b {{ color:var(--ok) }}
  .stat.no {{ border-color:{tint("--crit", 27)}; background:{tint("--crit", 6)} }}
  .stat.no b {{ color:var(--crit) }}
"""


@dataclass(frozen=True)
class Lead(Block):
    """The best finished unit so far, as chips rather than as a sentence.

    Four numbers and two pass marks do not belong in prose. The floor travels with the win: a
    unit over the primary bar but under a collapse floor is not a winner, and the top of the
    page must not read like it is.
    """

    arm: str = ""
    seed: int = 0
    order: Sequence[str] = field(default_factory=tuple)
    #: (metric title, value, target text, cleared)
    stats: Sequence[Tuple[str, float, str, bool]] = field(default_factory=tuple)
    css: ClassVar[str] = LEAD_CSS + CHART_CSS

    def md(self) -> str:
        if not self.arm:
            return ""
        bits = [f"**Current best.** {self.arm} seed {self.seed}"]
        for title, value, target, ok in self.stats:
            bits.append(f"{title} {value:.3f} {target} {'✓' if ok else '✗'}".strip())
        return " · ".join(bits)

    def html(self) -> str:
        if not self.arm:
            return ""
        chips = "".join(
            f'<span class="stat {"ok" if ok else "no"}">'
            f'<span class="n">{esc(title)}</span><b>{value:.3f}</b>'
            f'<span class="n">{esc(target)}</span></span>'
            for title, value, target, ok in self.stats)
        return (f'<div class="lead"><span class="k">Current best</span>'
                f'<span class="who">{charts.glyph(self.arm, list(self.order))}'
                f'{esc(self.arm)}<i>seed {self.seed}</i></span>{chips}</div>')


# --------------------------------------------------------------------------- metric lanes

LANES_CSS = """
  .mrows { padding:8px 14px 14px }
  .mrow { display:grid; grid-template-columns:210px 108px 1fr 78px; align-items:center;
    gap:12px; padding:3px 0 }
  .mname { font-size:12.5px; position:relative; cursor:help; display:flex; align-items:center;
    gap:6px; border-bottom:1px dotted var(--line); width:fit-content; max-width:100% }
  .mtarget { font-size:10.5px; color:var(--muted); font-variant-numeric:tabular-nums;
    white-space:nowrap }
  .mtarget .logs { display:block; font-size:9.5px; opacity:0.8; letter-spacing:0.04em }
  .mtrack { min-width:0 }
  .mval { font-size:12px; font-variant-numeric:tabular-nums; text-align:right;
    white-space:nowrap }
  .mval .sp { color:var(--muted); font-size:10.5px }
  svg.track { width:100%; height:36px; display:block }
  .bubble { position:absolute; left:0; top:100%; z-index:20; width:290px;
    background:var(--surface); border:1px solid var(--line); border-radius:9px;
    padding:9px 11px; box-shadow:0 8px 22px rgba(0,0,0,0.16); display:none; cursor:auto }
  .bubble.up { top:auto; bottom:calc(100% + 7px) }
  .mname:hover .bubble { display:block }
  .metric-help { position:relative }
  .metric-help > summary { list-style:none; cursor:pointer }
  .metric-help > summary:focus-visible { outline:2px solid var(--accent); outline-offset:3px }
  .metric-help[open] .bubble, .metric-help:hover .bubble, .metric-help:focus-within .bubble { display:block }
  .bubble b { display:block; font-size:12px; margin-bottom:4px }
  .bubble .bmath { display:block; margin:5px 0 7px; overflow-x:auto; overflow-y:hidden }
  .bubble .bmath .tex { display:block }
  .bubble .t { display:block; font-size:10.5px; color:var(--muted);
    font-variant-numeric:tabular-nums; margin-bottom:5px }
  .bubble .w { display:block; font-size:11.5px; line-height:1.45 }
  .bubble .y { display:block; font-size:11px; color:var(--muted); line-height:1.4;
    margin-top:6px; border-top:1px solid var(--line); padding-top:6px }
  @media(max-width:620px) {
    .mrow { grid-template-columns:minmax(0,1fr) auto; gap:6px; padding:8px 0 }
    .mtrack { grid-column:1 / -1; grid-row:2 }
    .mval { grid-column:2; grid-row:3 }
    .bubble { width:min(260px, 70vw) }
  }
"""


@dataclass(frozen=True)
class MetricLane:
    """One registered metric across every unit that has reported it."""

    name: str
    title: str
    target: str = ""
    formula: str = ""          # mathtext, typeset into the tooltip
    definition: str = ""       # what the registry says it means
    why: str = ""              # what the spec says it is registered for
    bar: Optional[float] = None
    direction: str = "report"
    log: bool = False
    up: bool = False           # open the tooltip upward, so it is not clipped by the panel
    #: (arm, value, hover label) for every unit, diverged ones included
    values: Sequence[Tuple[str, float, str]] = field(default_factory=tuple)
    seeds: Sequence[int] = field(default_factory=tuple)
    mean: str = "—"
    diverged: int = 0


@dataclass(frozen=True)
class MetricLanes(Block):
    """One row per registered metric, its lane drawn as a small SVG.

    Drawn before anything finishes — names, targets and empty lanes — because a panel that does
    not exist yet cannot tell a reader what is being measured.
    """

    title: str = ""
    lanes: Sequence[MetricLane] = field(default_factory=tuple)
    order: Sequence[str] = field(default_factory=tuple)
    #: arm -> (units finished, units registered), for the key above the lanes
    counts: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    css: ClassVar[str] = LANES_CSS + CHART_CSS + PANEL_CSS

    def md(self) -> str:
        if not self.lanes:
            return ""
        rows = [[la.title, la.target or "Reported",
                 la.mean + (f" ({la.diverged} diverged)" if la.diverged else "")]
                for la in self.lanes]
        return rows_to_md(("metric", "target", "mean"), rows)

    def _bubble(self, lane: MetricLane) -> str:
        from rl_researcher.mathtex import formula_svg

        parts = [f"<b>{esc(lane.title)}</b>"]
        tex = formula_svg(lane.formula, scale=1.15) if lane.formula else ""
        if tex:
            parts.append(f'<span class="bmath">{tex}</span>')
        if lane.target:
            parts.append(f'<span class="t">{esc(lane.target)}</span>')
        if lane.definition:
            parts.append(f'<span class="w">{esc(lane.definition)}</span>')
        if lane.why:
            parts.append(f'<span class="y">{esc(lane.why)}</span>')
        return f'<span class="bubble{" up" if lane.up else ""}">{"".join(parts)}</span>'

    def html(self) -> str:
        if not self.lanes:
            return ""
        rows = []
        for la in self.lanes:
            mean = esc(la.mean)
            if la.diverged:
                mean += f'<span class="sp">{la.diverged} diverged</span>'
            note = '<span class="logs">log scale</span>' if la.log else ""
            track = charts.track(list(la.values), la.bar, la.direction, list(self.order),
                                 label=la.title, seeds=list(la.seeds))
            rows.append(
                f'<div class="mrow">'
                f'<details class="metric-help"><summary class="mname">{esc(la.title)}</summary>'
                f'{self._bubble(la)}</details>'
                f'<div class="mtarget">{esc(la.target) or "Reported"}{note}</div>'
                f'<div class="mtrack">{track}</div>'
                f'<div class="mval">{mean}</div>'
                f'</div>')
        legend = charts.legend(list(self.order), dict(self.counts)) if self.counts else ""
        return panel(self.title, f'{legend}<div class="mrows">{"".join(rows)}</div>')


# --------------------------------------------------------------------------- unit tables

#: Shared by every table on a live page. Scoped to ``.ltable``, because the dashboard's rules
#: were written against bare ``table``, ``th`` and ``td`` -- and a page ships the stylesheet of
#: exactly the blocks it used, so an unscoped rule from one block reaches into every other
#: block's tables.
LTABLE_CSS = f"""
  .ltable {{ width:100%; min-width:880px; border-collapse:collapse; font-size:13px }}
  .ltable th {{ text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em;
    color:var(--muted); font-weight:600; padding:9px 12px; border-bottom:1px solid var(--line) }}
  .ltable td {{ padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:middle;
    white-space:nowrap }}
  .ltable td.metrics, .ltable td.note {{ white-space:normal }}
  .ltable tr:last-child td {{ border-bottom:0 }}
  .ltable .num {{ font-variant-numeric:tabular-nums }}
  .ltable .of {{ color:var(--muted); font-size:11.5px }}
  .ltable .cell {{ font-weight:600 }}
  .ltable .seed {{ color:var(--muted); font-weight:400; margin-left:6px; font-size:12px }}
  .ltable .metrics {{ font-variant-numeric:tabular-nums; font-size:12.5px }}
  .ltable .note {{ color:var(--muted); font-size:11.5px }}
  .ltable .mid {{ text-align:center }}
  .ltable .th2 {{ display:block; font-weight:400; text-transform:none; letter-spacing:0;
    color:var(--muted); font-size:10px; opacity:0.8 }}
  .ltable td.ok {{ color:var(--ok) }}
  .ltable td.no {{ color:var(--crit) }}
  .ltable td.ok .mark, .ltable td.no .mark {{ margin-left:4px; font-size:11px }}
  .ltrack {{ height:4px; background:var(--line); border-radius:3px; overflow:hidden; margin-top:5px }}
  .ltrack i {{ display:block; height:100% }}
  .bestrow {{ background:{tint("--ok", 5)} }}
  .best {{ margin-left:7px; padding:0 6px; border-radius:999px; font-size:9.5px; font-weight:700;
    letter-spacing:0.05em; text-transform:uppercase; color:var(--ok);
    border:1px solid {tint("--ok", 27)}; background:{tint("--ok", 8)} }}
  .curve {{ text-align:center; line-height:1 }}
  .curve .range {{ display:block; font-size:10px; color:var(--muted);
    font-variant-numeric:tabular-nums; margin-top:2px }}
  .curve .range b {{ color:var(--ink); font-weight:600 }}
  .lscroll {{ overflow-x:auto }}
"""


def _identity_html(arm: str, seed: int, order: Sequence[str], *, best: bool = False) -> str:
    star = '<span class="best">best</span>' if best else ""
    return (f'<td class="cell">{charts.glyph(arm, list(order), seed=int(seed))}'
            f'{esc(arm)}<span class="seed">seed {seed}</span>{star}</td>')


def _chip_html(state: str, tone: str) -> str:
    return f'<td><span class="chip t-{esc(tone)}">{esc(state)}</span></td>'


@dataclass(frozen=True)
class LiveUnit:
    """One unit still going."""

    arm: str
    seed: int
    state: str = "running"
    tone: str = "accent"
    step: int = 0
    max_steps: int = 1
    rate: Optional[float] = None
    eta_seconds: Optional[float] = None
    colour: str = "#888888"
    #: (title, series so far, last value, floor) for each curve the kind declares
    curves: Sequence[Tuple[str, Sequence[float], Optional[float], Optional[float]]] = field(
        default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class LiveUnits(Block):
    """Units still going: progress, rate, and each curve under its own name.

    Failed units are not here -- they get :class:`Failures`. One sat in this table reading
    ``0.0 steps/s`` under a heading that said it was in progress, with the reason for the
    failure off past the horizontal scroll at any normal width.
    """

    title: str = "in progress"
    units: Sequence[LiveUnit] = field(default_factory=tuple)
    order: Sequence[str] = field(default_factory=tuple)
    curve_titles: Sequence[str] = field(default_factory=tuple)
    css: ClassVar[str] = LTABLE_CSS + CHART_CSS + PANEL_CSS

    def md(self) -> str:
        if not self.units:
            return ""
        # The same thousands separator the HTML uses: the rule is that the page states every
        # number the file states, and `10000` against `10,000` is two different numbers to a
        # reader comparing them and to the test that checks they agree.
        rows = [[f"{u.arm}/seed{u.seed}", u.state, f"{u.step:,} of {u.max_steps:,}",
                 f"{u.rate:.1f} steps/s" if u.rate is not None else "—",
                 duration(u.eta_seconds), "; ".join(u.notes)]
                for u in self.units]
        return rows_to_md(("unit", "state", "step", "rate", "time left", "note"), rows)

    def html(self) -> str:
        if not self.units:
            return ""
        heads = "".join(f'<th class="mid">{esc(t)}</th>' for t in self.curve_titles)
        rows = []
        for u in self.units:
            pct = 100.0 * int(u.step) / max(int(u.max_steps), 1)
            cols = []
            for title, series, last, floor in u.curves:
                svg = charts.curve(list(series), colour=u.colour, label=title,
                                   steps=int(u.step), floor=floor)
                shown = "—" if last is None or float(last) != float(last) else _fmt(last)
                first = f"{_fmt(series[0])} → " if len(series) >= 2 else ""
                cols.append(f'<td class="curve">{svg}'
                            f'<span class="range">{first}<b>{shown}</b></span></td>')
            note = "<br>".join(esc(n) for n in u.notes)
            rate = f"{u.rate:.1f}" if u.rate is not None else "—"
            rows.append(
                f'<tr>{_identity_html(u.arm, u.seed, self.order)}{_chip_html(u.state, u.tone)}'
                f'<td class="num">{u.step:,}<span class="of"> / {u.max_steps:,}</span>'
                f'<div class="ltrack"><i style="width:{pct:.1f}%;background:{u.colour}"></i></div>'
                f'</td>'
                f'<td class="num">{rate}<span class="of"> steps/s</span></td>'
                f'<td class="num">{duration(u.eta_seconds)}</td>'
                f'{"".join(cols)}<td class="note">{note}</td></tr>')
        return panel(self.title,
                     f'<div class="lscroll"><table class="ltable">'
                     f'<tr><th>unit</th><th>state</th><th>step</th><th>rate</th>'
                     f'<th>time left</th>{heads}<th></th></tr>{"".join(rows)}</table></div>')


@dataclass(frozen=True)
class DoneUnit:
    """One unit that finished, and how it did on each registered bar."""

    arm: str
    seed: int
    state: str = "done"
    tone: str = "ok"
    seconds: Optional[float] = None
    colour: str = "#888888"
    step: int = 0
    #: (text, tone, mark) per bar metric, in the order the heads declare
    cells: Sequence[Tuple[str, str, str]] = field(default_factory=tuple)
    #: (title, series, floor) per declared curve
    curves: Sequence[Tuple[str, Sequence[float], Optional[float]]] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class DoneUnits(Block):
    """Units that finished, best first on the primary metric, with their pass marks."""

    title: str = "finished"
    units: Sequence[DoneUnit] = field(default_factory=tuple)
    order: Sequence[str] = field(default_factory=tuple)
    #: (metric title, target text) per column
    heads: Sequence[Tuple[str, str]] = field(default_factory=tuple)
    curve_titles: Sequence[str] = field(default_factory=tuple)
    css: ClassVar[str] = LTABLE_CSS + CHART_CSS + PANEL_CSS

    def md(self) -> str:
        if not self.units:
            return ""
        headers = ["unit", "state", "time"] + [t for t, _ in self.heads]
        rows = [[f"{u.arm}/seed{u.seed}", u.state, duration(u.seconds)]
                + [f"{text}{mark}" for text, _tone, mark in u.cells]
                for u in self.units]
        return rows_to_md(headers, rows)

    def html(self) -> str:
        if not self.units:
            return ""
        heads = "".join(f'<th class="mid">{esc(t)}<span class="th2">{esc(target)}</span></th>'
                        for t, target in self.heads)
        curve_heads = "".join(f'<th class="mid">{esc(t)}</th>' for t in self.curve_titles)
        rows = []
        for i, u in enumerate(self.units):
            top = i == 0 and len(self.units) > 1
            cols = "".join(f'<td class="num mid {esc(tone)}">{esc(text)}'
                           f'<span class="mark">{esc(mark)}</span></td>'
                           for text, tone, mark in u.cells)
            drawn = "".join(
                f'<td class="curve">'
                f'{charts.curve(list(series), colour=u.colour, label=title, floor=floor, steps=int(u.step))}'
                f'</td>' for title, series, floor in u.curves)
            note = "<br>".join(esc(n) for n in u.notes)
            opening = '<tr class="bestrow">' if top else "<tr>"
            rows.append(
                f'{opening}{_identity_html(u.arm, u.seed, self.order, best=top)}'
                f'{_chip_html(u.state, u.tone)}'
                f'<td class="num">{duration(u.seconds)}</td>'
                f'{cols}{drawn}<td class="note">{note}</td></tr>')
        return panel(self.title,
                     f'<div class="lscroll"><table class="ltable">'
                     f'<tr><th>unit</th><th>state</th><th>time</th>{heads}{curve_heads}'
                     f'<th></th></tr>{"".join(rows)}</table></div>')


# --------------------------------------------------------------------------- arms

ARMS_CSS = """
  .vtable { min-width:660px; width:100%; border-collapse:collapse; font-size:13px }
  .vtable th { text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em;
    color:var(--muted); font-weight:600; padding:9px 12px; border-bottom:1px solid var(--line) }
  .vtable td { padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:middle;
    white-space:nowrap }
  .vtable tr:last-child td { border-bottom:0 }
  .vtable .num { font-variant-numeric:tabular-nums; line-height:1.4 }
  .vtable .mid { text-align:center }
  .vtable .cell { font-weight:600 }
  .vtable .seed { color:var(--muted); font-weight:400; margin-left:6px; font-size:12px }
  .vtable .th2 { display:block; font-weight:400; text-transform:none; letter-spacing:0;
    color:var(--muted); font-size:10px; opacity:0.8 }
  .vtable td.ok { color:var(--ok) }
  .vtable td.no { color:var(--crit) }
  .vtable td.of { color:var(--muted) }
  .vtable td.ok .mark, .vtable td.no .mark { margin-left:4px; font-size:11px }
  .vtable .sp, .vtable .crown { display:block; font-weight:400 }
  .vtable .sp { color:var(--muted); font-size:10.5px }
  .vtable .crown { color:var(--ok); font-size:9.5px; text-transform:uppercase;
    letter-spacing:0.05em; font-weight:700 }
  .vscroll { overflow-x:auto }
"""


@dataclass(frozen=True)
class ArmCell:
    """One arm's number for one metric: the mean, its spread, and whether it cleared."""

    text: str = "not computed"
    tone: str = "of"
    mark: str = ""
    spread: str = ""
    diverged: str = ""
    best: bool = False


@dataclass(frozen=True)
class ArmTable(Block):
    """Arms down the side, metrics across the top: mean, spread, pass mark, best marked.

    Not a second set of lanes. :class:`MetricLanes` places every unit by position, so a
    positional chart of the same numbers aggregated would be the same picture twice. A table
    makes a different kind of statement -- exact values, side by side, comparable down a
    column -- which is the one thing the lanes cannot do.
    """

    title: str = ""
    noun: str = "arm"
    order: Sequence[str] = field(default_factory=tuple)
    #: (metric title, target text)
    heads: Sequence[Tuple[str, str]] = field(default_factory=tuple)
    #: (arm, seed count, cells)
    rows: Sequence[Tuple[str, int, Sequence[ArmCell]]] = field(default_factory=tuple)
    css: ClassVar[str] = ARMS_CSS + CHART_CSS + PANEL_CSS

    def md(self) -> str:
        if not self.rows:
            return ""
        headers = [self.noun] + [t for t, _ in self.heads]
        body = [[f"{arm} ({seeds} seed{'' if seeds == 1 else 's'})"]
                + [f"{c.text}{c.mark}" for c in cells]
                for arm, seeds, cells in self.rows]
        return rows_to_md(headers, body)

    def html(self) -> str:
        if not self.rows:
            return ""
        heads = "".join(f'<th class="mid">{esc(t)}<span class="th2">{esc(target)}</span></th>'
                        for t, target in self.heads)
        body = []
        for arm, seeds, cells in self.rows:
            cols = []
            for c in cells:
                extra = ""
                if c.spread:
                    extra += f'<span class="sp">{esc(c.spread)}</span>'
                if c.diverged:
                    extra += f'<span class="sp">{esc(c.diverged)}</span>'
                if c.best:
                    extra += '<span class="crown">best</span>'
                cols.append(f'<td class="num mid {esc(c.tone)}">{esc(c.text)}'
                            f'<span class="mark">{esc(c.mark)}</span>{extra}</td>')
            body.append(
                f'<tr><td class="cell">{charts.glyph(arm, list(self.order))}{esc(arm)}'
                f'<span class="seed">{seeds} seed{"" if seeds == 1 else "s"}</span></td>'
                f'{"".join(cols)}</tr>')
        return panel(self.title,
                     f'<div class="vscroll"><table class="vtable">'
                     f'<tr><th>{esc(self.noun)}</th>{heads}</tr>{"".join(body)}</table></div>')


# --------------------------------------------------------------------------- failures, queue

FAIL_CSS = """
  .fails { display:flex; flex-direction:column; gap:12px; padding:6px 14px 14px }
  .failrow { border-left:3px solid var(--crit); padding:1px 0 1px 11px }
  .failwho { font-weight:600; display:flex; align-items:center; gap:0 }
  .failwho .chip { margin-left:9px }
  .failwho .seed { color:var(--muted); font-weight:400; margin-left:6px; font-size:12px }
  .failmsg { color:var(--crit); font-size:11.5px; margin-top:4px; white-space:pre-wrap;
    word-break:break-word;
    font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace }
  .failnote { color:var(--muted); font-size:11px; margin-top:4px;
    font-variant-numeric:tabular-nums }
  .queued { display:flex; flex-wrap:wrap; gap:6px; padding:11px 14px }
  .qchip { display:inline-flex; align-items:center; gap:5px; padding:2px 9px; border-radius:7px;
    border:1px solid var(--line); font-size:11.5px; color:var(--muted) }
  .qchip i { font-style:normal; opacity:0.75 }
"""


@dataclass(frozen=True)
class Failure:
    """One unit that died, with the reason first."""

    arm: str
    seed: int
    state: str = "failed"
    tone: str = "crit"
    error: str = "no reason recorded"
    where: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class Failures(Block):
    """Units that died, in the reading order between what is running and what is queued.

    A failure has to be noticed without the page dropping everything else for it. The whole-run
    chip at the top already says FAILED; this says which unit, why, and where to resume from.
    """

    units: Sequence[Failure] = field(default_factory=tuple)
    order: Sequence[str] = field(default_factory=tuple)
    css: ClassVar[str] = FAIL_CSS + CHART_CSS + PANEL_CSS

    @property
    def heading(self) -> str:
        n = len(self.units)
        return f"failed · {n} unit{'' if n == 1 else 's'}"

    def md(self) -> str:
        if not self.units:
            return ""
        return "\n".join(f"- **{u.arm}/seed{u.seed}** — {u.error}"
                         + (f" ({'; '.join(u.where)})" if u.where else "")
                         for u in self.units)

    def html(self) -> str:
        if not self.units:
            return ""
        rows = "".join(
            f'<div class="failrow">'
            f'<div class="failwho">{charts.glyph(u.arm, list(self.order), seed=int(u.seed))}'
            f'{esc(u.arm)}<span class="seed">seed {u.seed}</span>'
            f'<span class="chip t-{esc(u.tone)}">{esc(u.state)}</span></div>'
            f'<div class="failmsg">{esc(u.error)}</div>'
            f'<div class="failnote">{esc(" · ".join(u.where))}</div></div>'
            for u in self.units)
        return panel(self.heading, f'<div class="fails">{rows}</div>')


@dataclass(frozen=True)
class QueuedUnits(Block):
    """Units not started yet -- their own box, so the in-progress table ends cleanly."""

    units: Sequence[Tuple[str, int]] = field(default_factory=tuple)   # (arm, seed)
    order: Sequence[str] = field(default_factory=tuple)
    css: ClassVar[str] = FAIL_CSS + CHART_CSS + PANEL_CSS

    def md(self) -> str:
        if not self.units:
            return ""
        return "Queued: " + ", ".join(f"{arm} seed {seed}" for arm, seed in self.units)

    def html(self) -> str:
        if not self.units:
            return ""
        chips = "".join(f'<span class="qchip">{charts.glyph(arm, list(self.order))}'
                        f'{esc(arm)}<i>seed {seed}</i></span>' for arm, seed in self.units)
        return panel(f"queued · {len(self.units)}", f'<div class="queued">{chips}</div>')


# --------------------------------------------------------------------------- the ladder

LADDER_CSS = """
  .ladder { padding:10px 14px 14px; overflow-x:auto }
  .lrow { display:grid; gap:8px; align-items:stretch; margin-bottom:8px }
  .lhead { font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted);
    font-weight:600; padding:0 2px }
  .larm { font-weight:600; font-size:12.5px; display:flex; align-items:center; gap:6px }
  .lcell { border:1px solid var(--line); border-radius:8px; padding:7px 9px;
    background:var(--surface); display:flex; flex-direction:column; gap:5px; min-width:0 }
  .lcell.q { opacity:0.55 }
  .lwhat { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;
    overflow:hidden; text-overflow:ellipsis }
"""


@dataclass(frozen=True)
class Ladder(Block):
    """Every unit as a grid: one row per arm, one column per seed.

    The shape of a run is the shape of the argument it makes, so a page can draw it that way
    instead of as twelve rows of a table. A reader sees at a glance whether a whole arm is
    missing -- which withholds a comparison -- or one seed of each, which does not.
    """

    noun: str = "arm"
    seeds: Sequence[int] = field(default_factory=tuple)
    order: Sequence[str] = field(default_factory=tuple)
    #: (arm, seed) -> (state, tone, what it says)
    cells: Dict[Tuple[str, int], Tuple[str, str, str]] = field(default_factory=dict)
    css: ClassVar[str] = LADDER_CSS + CHART_CSS + PANEL_CSS

    @property
    def heading(self) -> str:
        return f"ladder · {len(self.order)} {self.noun}s × {len(self.seeds)} seeds"

    def md(self) -> str:
        if not self.order:
            return ""
        headers = [self.noun] + [f"seed {s}" for s in self.seeds]
        rows = []
        for arm in self.order:
            row = [arm]
            for seed in self.seeds:
                state, _tone, what = self.cells.get((arm, int(seed)), ("", "", ""))
                row.append(f"{state} {what}".strip() or "·")
            rows.append(row)
        return rows_to_md(headers, rows)

    def html(self) -> str:
        if not self.order:
            return ""
        # Sized from the seed count rather than `auto-fit`, which let the columns fall below the
        # width their contents need and clipped them. Below `min-width` the panel scrolls
        # sideways, which is the honest failure: a number pushed off-screen is worse than one
        # you scroll to.
        cols = (f"grid-template-columns:minmax(112px,168px) "
                f"repeat({len(self.seeds)},minmax(124px,1fr));"
                f"min-width:{112 + 128 * len(self.seeds)}px")
        head = "".join(f'<div class="lhead">seed {s}</div>' for s in self.seeds)
        rows = [f'<div class="lrow" style="{cols}"><div class="lhead">{esc(self.noun)}</div>'
                f'{head}</div>']
        for arm in self.order:
            cells = []
            for seed in self.seeds:
                got = self.cells.get((arm, int(seed)))
                if got is None:
                    continue
                state, tone, what = got
                q = " q" if state == "queued" else ""
                cells.append(f'<div class="lcell{q}">'
                             f'<span class="chip t-{esc(tone)}">{esc(state)}</span>'
                             f'<span class="lwhat">{esc(what)}</span></div>')
            rows.append(f'<div class="lrow" style="{cols}"><div class="larm">'
                        f'{charts.glyph(arm, list(self.order))}{esc(arm)}</div>'
                        f'{"".join(cells)}</div>')
        return panel(self.heading, f'<div class="ladder">{"".join(rows)}</div>')


# --------------------------------------------------------------------------- the index

INDEX_CSS = """
  .itable { width:100%; border-collapse:collapse; font-size:13px }
  .itable th { text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em;
    color:var(--muted); font-weight:600; padding:9px 12px; border-bottom:1px solid var(--line) }
  .itable td { padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:middle;
    white-space:nowrap }
  .itable tr:last-child td { border-bottom:0 }
  .itable td.metrics { white-space:normal; font-variant-numeric:tabular-nums; font-size:12.5px }
  .itable .cell { font-weight:600 }
  .itable .cell a { color:inherit }
  .itable .seed { color:var(--muted); font-weight:400; margin-left:6px; font-size:12px }
  .itable .num { font-variant-numeric:tabular-nums }
  .itable .of { color:var(--muted); font-size:11.5px }
  .itable .note { color:var(--muted); font-size:11.5px; white-space:normal }
  .itrack { height:4px; background:var(--line); border-radius:3px; overflow:hidden; margin-top:5px }
  .itrack i { display:block; height:100% }
  .iscroll { overflow-x:auto }
"""


@dataclass(frozen=True)
class IndexEntry:
    """One run's line on the index over every run in the project."""

    name: str
    kind: str = ""
    state: str = "not started"
    tone: str = "muted"
    href: str = ""
    done: str = "—"
    total_pct: float = 0.0
    eta: str = "—"
    accent: str = "#888888"
    best: Optional[Lead] = None
    error: str = ""


@dataclass(frozen=True)
class IndexTable(Block):
    """Every registered run, worst news first.

    A finished run is the least urgent thing on the page; a failed one is the most, and a stale
    one is a failure that has not admitted it yet. A spec that stopped parsing is named rather
    than skipped -- dropping the row hides the only symptom.
    """

    entries: Sequence[IndexEntry] = field(default_factory=tuple)
    css: ClassVar[str] = INDEX_CSS + LEAD_CSS + CHART_CSS

    def md(self) -> str:
        if not self.entries:
            return "_(no runs registered)_"
        rows = [[e.name, e.kind or "?", e.state, e.done, e.eta,
                 e.error or (e.best.md() if e.best else "")]
                for e in self.entries]
        return rows_to_md(("run", "kind", "state", "units", "left", "best so far"), rows)

    def html(self) -> str:
        rows = []
        for e in self.entries:
            if e.error:
                rows.append(f'<tr><td class="cell">{esc(e.name)}</td>'
                            f'<td><span class="chip t-crit">{esc(e.state)}</span></td>'
                            f'<td colspan="3" class="note">{esc(e.error)}</td></tr>')
                continue
            link = (f'<a href="{esc(e.href)}">{esc(e.name)}</a>') if e.href else esc(e.name)
            best = e.best.html() if e.best else '<span class="of">no finished unit yet</span>'
            rows.append(
                f'<tr><td class="cell">{link}<span class="seed">{esc(e.kind)}</span></td>'
                f'<td><span class="chip t-{esc(e.tone)}">{esc(e.state)}</span></td>'
                f'<td class="num">{esc(e.done)}<span class="of"> units</span>'
                f'<div class="itrack"><i style="width:{e.total_pct:.1f}%;'
                f'background:{e.accent}"></i></div></td>'
                f'<td class="num">{esc(e.eta)}</td>'
                f'<td class="metrics">{best}</td></tr>')
        return (f'<div class="iscroll"><table class="itable">'
                f'<tr><th>run</th><th>state</th><th>units</th><th>left</th>'
                f'<th>best so far</th></tr>{"".join(rows)}</table></div>')


# --------------------------------------------------------------------------- the run log

RUNLOG_CSS = """
  .rlog { padding:10px 14px; background:var(--code); font-size:11.5px; line-height:1.6;
    overflow-x:auto; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace }
  .rlog .ln { white-space:pre; padding:0.5px 0 }
  .rlog .step { display:grid; grid-template-columns:66px 150px 118px 1fr; gap:8px;
    white-space:nowrap; color:var(--muted) }
  .rlog .cellchip { font-weight:600; overflow:hidden; text-overflow:ellipsis }
  .rlog .stepn { text-align:right; font-variant-numeric:tabular-nums; color:var(--ink) }
  .rlog .of { color:var(--muted) }
  .rlog .rest { overflow:hidden; text-overflow:ellipsis }
  .rlog .ts { color:var(--muted); opacity:0.7 }
  .rlog .crit { color:var(--crit); font-weight:600 }
  .rlog .ok { color:var(--ok) }
  .rlog .warn { color:var(--warn) }
  .rlog { scrollbar-width:thin; scrollbar-color:var(--line) transparent }
  .rlog::-webkit-scrollbar { height:9px }
  .rlog::-webkit-scrollbar-track { background:transparent }
  .rlog::-webkit-scrollbar-thumb { background:var(--line); border-radius:6px }
  .rlog::-webkit-scrollbar-thumb:hover { background:var(--muted) }
"""


@dataclass(frozen=True)
class LogLine:
    """One line of the tail.

    A step line is broken into its parts so the column of step counts lines up and each unit's
    name carries its own colour; anything else is one string with its tone. Which lines are
    worth the space, and what a step line looks like, are the kind's to say through
    ``log_vocab`` — decided upstream, because a block is handed what to draw.
    """

    text: str = ""
    tone: str = ""
    stamp: str = ""
    unit: str = ""
    colour: str = ""
    step: str = ""
    max_steps: str = ""
    rest: str = ""

    @property
    def is_step(self) -> bool:
        return bool(self.unit)


@dataclass(frozen=True)
class RunLog(Block):
    """The end of the run log: dim timestamps, lines toned by what they say, steps aligned."""

    title: str = "log"
    lines: Sequence[LogLine] = field(default_factory=tuple)
    css: ClassVar[str] = RUNLOG_CSS + PANEL_CSS

    def md(self) -> str:
        if not self.lines:
            return "```\nno log yet\n```"
        out = []
        for ln in self.lines:
            if ln.is_step:
                out.append(f"{ln.stamp} [{ln.unit}] step {ln.step}/{ln.max_steps} {ln.rest}".strip())
            else:
                out.append(ln.text)
        body = "\n".join(out)
        return f"```\n{body}\n```"

    def html(self) -> str:
        if not self.lines:
            return panel(self.title, '<div class="rlog"><div class="ln of">no log yet</div></div>')
        out = []
        for ln in self.lines:
            if ln.is_step:
                out.append(
                    f'<div class="ln step">'
                    f'<span class="ts">{esc(ln.stamp)}</span>'
                    f'<span class="cellchip" style="color:{ln.colour}">{esc(ln.unit)}</span>'
                    f'<span class="stepn">{esc(ln.step)}'
                    f'<span class="of">/{esc(ln.max_steps)}</span></span>'
                    f'<span class="rest">{esc(ln.rest)}</span></div>')
                continue
            text = esc(ln.text)
            stamp = f'<span class="ts">{esc(ln.stamp)}</span>' if ln.stamp else ""
            out.append(f'<div class="ln {esc(ln.tone)}">{stamp}{text}</div>')
        return panel(self.title, f'<div class="rlog">{"".join(out)}</div>')


# --------------------------------------------------------------------------- the foot

FOOT_CSS = """
  .foot { color:var(--muted); font-size:11.5px; margin-top:10px }
  .foot code { background:var(--code); padding:1px 5px; border-radius:4px }
"""


@dataclass(frozen=True)
class PageFoot(Block):
    """What the page is, when it was drawn, and what it is not the authority on.

    Not the report's :class:`Footer`, which names the command that rebuilds a document. This
    one exists to say the opposite: `status` is the authority on liveness, not this page. A
    page that refreshes itself cannot exit non-zero, and a reader who trusts it instead of the
    exit code waits out a hang.
    """

    run: str = ""
    device: str = ""
    note: str = ""
    when: str = ""
    tail: str = ""
    css: ClassVar[str] = FOOT_CSS

    def _parts(self) -> Sequence[str]:
        mid = f" · {self.note}" if self.note else ""
        return (f"{self.run} · {self.device}{mid}",
                "`status` is the authority on liveness, not this page",
                f"{self.when}, {self.tail}")

    def md(self) -> str:
        return " · ".join(p for p in self._parts() if p.strip(" ·"))

    def html(self) -> str:
        mid = f" · {self.note}" if self.note else ""
        return (f'<div class="foot">{esc(self.run)} · {esc(self.device)}{mid} · '
                f'<code>status</code> is the authority on liveness, not this page · '
                f'{esc(self.when)}, {esc(self.tail)}</div>')
