"""Inline SVG for the live dashboard — no library, no requests, no build step.

The study's own figures (`figures.py`, matplotlib) are written once, when the study finishes.
A dashboard has to draw a study that is still running, from whatever has landed so far, in a
page that refreshes itself every few seconds.

**Layout is HTML; only the track is SVG.** An all-SVG chart scales its text with its container,
which made the labels shrink to nothing on a narrow screen, and it can only offer the browser's
native tooltip. So a chart here is a grid of HTML rows — the metric name, its target and its
value are real text — each holding one small fixed-height SVG that draws just the axis, the
threshold and the markers.

Identity is carried by **shape as well as colour**, and markers **dodge** when they collide:
two cells at the same value would otherwise hide one another entirely.
"""

from __future__ import annotations

import html
import math
from typing import Dict, List, Optional, Sequence, Tuple

from rl_researcher import plotstyle as ps

LOG_DECADES = 2.0        # a metric spanning more than this many decades gets a log axis
SHAPES = ("circle", "square", "triangle", "diamond", "plus", "cross")
TRACK_H = 36             # height of one lane; tall enough that a dodged marker stays inside it
DODGE = 7.0              # vertical step between markers that would otherwise overlap
DODGE_MAX = 11.0         # ... and the furthest one may ever sit from the axis
SEED_HATCH = ("solid", "diag", "outline")   # which seed of a variant a marker is
SEED_KEY_COLOUR = "#7A8582"      # neutral grey for the legend's seed key, legible on both
                                 # grounds; a variant colour there would read as being
                                 # about that variant rather than about the fill.


def _finite(values: Sequence[float]) -> List[float]:
    """The values that can be placed on an axis: no NaN (not computed), no inf (diverged).

    An inf used to reach the scale, where it decided the axis for every other mark on the row.
    """
    return [float(v) for v in values if v is not None and math.isfinite(float(v))]


def _scale(values: Sequence[float], bar: Optional[float]) -> Tuple[float, float, bool]:
    """(lo, hi, log) covering the data and the target, with a little padding."""
    pts = _finite(values) + ([bar] if bar is not None else [])
    if not pts:
        return 0.0, 1.0, False
    lo, hi = min(pts), max(pts)
    log = bool(lo > 0 and hi / lo > 10 ** LOG_DECADES)
    if log:
        return lo / 1.6, hi * 1.6, True
    span = hi - lo
    pad = span * 0.12 if span > 0 else (abs(hi) * 0.2 or 0.5)
    # Data that is never negative gets an axis that starts at zero, not at -0.09: a ratio
    # cannot be negative and an axis saying it can is a lie about the quantity.
    low = max(0.0, lo - pad) if lo >= 0 else lo - pad
    return low, hi + pad, False


def _pos(value: float, lo: float, hi: float, log: bool, width: float) -> float:
    if log:
        lv, llo, lhi = (math.log10(max(v, 1e-12)) for v in (value, lo, hi))
        frac = (lv - llo) / max(lhi - llo, 1e-9)
    else:
        frac = (value - lo) / max(hi - lo, 1e-9)
    return max(0.0, min(1.0, frac)) * width


def fmt(v: float) -> str:
    if math.isnan(v):
        return "n/a"
    if math.isinf(v):
        return "diverged"
    if v == 0:
        return "0"
    if abs(v) >= 100 or abs(v) < 0.01:
        return f"{v:.3g}"
    return f"{v:.3f}".rstrip("0").rstrip(".")


def is_log(values: Sequence[float], bar: Optional[float]) -> bool:
    """Whether this metric's axis is logarithmic — the reader has to be told."""
    return _scale(list(values), bar)[2]


def target_label(bar: Optional[float], direction: str) -> str:
    """How a threshold is written on the page: one word, and the direction always shown."""
    if bar is None:
        return ""
    return f"Target {'≤' if direction == 'lower' else '≥'} {bar:g}"


def shape_of(variant: str, order: Sequence[str]) -> str:
    """The marker shape for a variant — stable for a given study's variant order."""
    order = list(order)
    idx = order.index(variant) if variant in order else len(order)
    return SHAPES[idx % len(SHAPES)]


def _pattern_id(colour: str, kind: str) -> str:
    return f"hx-{kind}-{colour.lstrip('#')}"


def seed_defs(pairs: Sequence[Tuple[str, int]]) -> str:
    """``<defs>`` for the one hatched seed, per colour that actually uses it.

    Hatching rather than opacity. A faded marker reads as *less* — less certain, less
    important — when all it means is "a different seed of the same cell". A hatch reads as
    different without implying weaker, and it survives printing and colour-blindness.
    """
    seen, defs = set(), []
    for colour, seed in pairs:
        kind = SEED_HATCH[seed % len(SEED_HATCH)]
        if kind != "diag":          # solid and outline need no pattern
            continue
        pid = _pattern_id(colour, kind)
        if pid in seen:
            continue
        seen.add(pid)
        lines = '<path d="M0,4 l4,-4" stroke-width="1.6"/>'
        defs.append(f'<pattern id="{pid}" width="4" height="4" patternUnits="userSpaceOnUse">'
                    f'<rect width="4" height="4" fill="{colour}" fill-opacity="0.18"/>'
                    f'<g stroke="{colour}" fill="none">{lines}</g></pattern>')
    return f'<svg width="0" height="0" aria-hidden="true"><defs>{"".join(defs)}</defs></svg>' if defs else ""


def seed_style(colour: str, seed: Optional[int]) -> str:
    """Fill attributes that say which seed this is: solid, hatched, outlined.

    Shape stays the variant's and colour stays the variant's; only the fill carries the seed,
    so an outlying seed is identifiable without hovering. Three fills that differ in how much
    ink they carry rather than three hatch angles, which at a seven-pixel marker all resolve
    to the same smudge.
    """
    if seed is None:
        return f'fill="{colour}"'
    kind = SEED_HATCH[seed % len(SEED_HATCH)]
    if kind == "solid":
        return f'fill="{colour}"'
    if kind == "outline":
        return f'fill="none" stroke="{colour}" stroke-width="1.6"'
    return f'fill="url(#{_pattern_id(colour, kind)})" stroke="{colour}" stroke-width="1"'


def marker(shape: str, cx: float, cy: float, r: float, *, fill: str, extra: str = "",
           seed: Optional[int] = None) -> str:
    """One marker of the given shape, centred on (cx, cy). ``seed`` varies the fill."""
    colour, fill = fill, seed_style(fill, seed)
    if shape == "square":
        return (f'<rect x="{cx - r:.1f}" y="{cy - r:.1f}" width="{2 * r:.1f}" height="{2 * r:.1f}" '
                f'{fill} rx="1">{extra}</rect>')
    if shape == "triangle":
        pts = f"{cx:.1f},{cy - r - 0.6:.1f} {cx - r - 0.5:.1f},{cy + r:.1f} {cx + r + 0.5:.1f},{cy + r:.1f}"
        return f'<polygon points="{pts}" {fill}>{extra}</polygon>'
    if shape == "diamond":
        pts = (f"{cx:.1f},{cy - r - 1:.1f} {cx + r + 1:.1f},{cy:.1f} "
               f"{cx:.1f},{cy + r + 1:.1f} {cx - r - 1:.1f},{cy:.1f}")
        return f'<polygon points="{pts}" {fill}>{extra}</polygon>'
    if shape in ("plus", "cross"):
        a = r + 1
        rot = ' transform="rotate(45 %.1f %.1f)"' % (cx, cy) if shape == "cross" else ""
        # These shapes are strokes, so they have no fill to vary. The seed rides on the stroke
        # instead: solid, dashed, then thin. (Reading the colour back out of seed_style's
        # attributes gave the outlined seed stroke="none", an invisible marker.)
        kind = "solid" if seed is None else SEED_HATCH[seed % len(SEED_HATCH)]
        weight = {"solid": 2.2, "diag": 2.2, "outline": 1.2}[kind]
        dash = ' stroke-dasharray="3 1.4"' if kind == "diag" else ""
        return (f'<g{rot} stroke="{colour}" stroke-width="{weight}" stroke-linecap="round"{dash}>'
                f'<line x1="{cx - a:.1f}" y1="{cy:.1f}" x2="{cx + a:.1f}" y2="{cy:.1f}"/>'
                f'<line x1="{cx:.1f}" y1="{cy - a:.1f}" x2="{cx:.1f}" y2="{cy + a:.1f}"/>{extra}</g>')
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" {fill}>{extra}</circle>'


def glyph(variant: str, order: Sequence[str], *, size: int = 13,
          seed: Optional[int] = None) -> str:
    """The variant's marker on its own, for a legend, a table cell or the summary box."""
    return (f'<svg class="glyph" width="{size}" height="{size + 1}" aria-hidden="true">'
            f'{marker(shape_of(variant, order), size / 2, (size + 1) / 2, 4.5, fill=ps.variant_color(variant, order), seed=seed)}'
            f'</svg>')


def _dodge(positions: List[float], radius: float = 1.6) -> List[float]:
    """Vertical offsets so markers that land together stay countable — and stay in the lane.

    The offset alternates above and below the axis and is **clamped**: an unbounded ladder put
    a third colliding marker outside its own row, which is worse than the overlap it was
    solving.
    """
    offsets = [0.0] * len(positions)
    seen: List[Tuple[float, int]] = []
    for i, x in enumerate(positions):
        clash = [k for px, k in seen if abs(px - x) < radius * 2]
        if clash:
            step = len(clash)
            raw = DODGE * (1 if step % 2 else -1) * ((step + 1) // 2)
            offsets[i] = max(-DODGE_MAX, min(DODGE_MAX, raw))
        seen.append((x, i))
    return offsets


def track(values: List[Tuple[str, float, str]], bar: Optional[float], direction: str,
          order: Sequence[str], *, width: int = 100, label: str = "",
          seeds: Optional[Sequence[Optional[int]]] = None) -> str:
    """One metric's lane: a stretched SVG behind, HTML markers on top, its scale at the ends.

    The background (axis, shaded passing side, threshold rule) is drawn in a viewBox stretched
    to the column's width, which is fine for rectangles and, with ``non-scaling-stroke``, for
    lines. Markers are *not* in it: non-uniform scaling would squash a circle into an ellipse
    and a triangle into a wedge, so they are positioned as their own small SVGs at a percentage
    along the lane.

    The endpoints come from the same :func:`_scale` that places the markers, so a label can
    never disagree with a position. Without them a marker's position means nothing, which is
    what "the plots need axes" is really asking for on a one-dimensional lane — the metric's
    name is already beside it, so a title would only repeat itself.
    """
    lo, hi, log = _scale([v for _, v, _ in values], bar)
    cy = TRACK_H / 2
    bg = [f'<line class="axis" x1="0" y1="{cy}" x2="{width}" y2="{cy}" '
          f'vector-effect="non-scaling-stroke"/>']
    if bar is not None:
        bx = _pos(bar, lo, hi, log, width)
        x0, w = (0.0, bx) if direction == "lower" else (bx, width - bx)
        if w > 0:
            bg.append(f'<rect class="pass" x="{x0:.2f}" y="4" width="{w:.2f}" height="{TRACK_H - 8}"/>')
        bg.append(f'<line class="bar" x1="{bx:.2f}" y1="2" x2="{bx:.2f}" y2="{TRACK_H - 2}" '
                  f'vector-effect="non-scaling-stroke"/>')

    seeds = list(seeds) if seeds else [None] * len(values)
    finite = [(n, v, t, sd) for (n, v, t), sd in zip(values, seeds) if math.isfinite(v)]
    xs = [_pos(v, lo, hi, log, width) for _, v, _, _ in finite]
    marks = []
    for (name, _v, tip, sd), x, dy in zip(finite, xs, _dodge(xs)):
        marks.append(
            f'<span class="mk" style="left:{x:.2f}%;margin-top:{dy:.0f}px" title="{html.escape(tip)}">'
            f'<svg width="13" height="13" aria-hidden="true">'
            f'{marker(shape_of(name, order), 6.5, 6.5, 4.5, fill=ps.variant_color(name, order), seed=sd)}'
            f'</svg></span>')
    for name, v, tip in values:
        if math.isnan(v):
            marks.append(f'<span class="mk nan" style="left:1%" title="{html.escape(tip)}">'
                         f'<svg width="13" height="13" aria-hidden="true">'
                         f'<circle class="nanpt" cx="6.5" cy="6.5" r="3.5"/></svg></span>')
        elif math.isinf(v):
            # Parked past the right edge rather than plotted: a diverged rollout has no place
            # on the axis, but dropping it would show a variant as having fewer seeds than it
            # ran. Its own mark, so it is never mistaken for the largest measured value.
            marks.append(f'<span class="mk div" style="left:99%" title="{html.escape(tip)}">'
                         f'<svg width="13" height="13" aria-hidden="true">'
                         f'<path class="divpt" d="M3 3 L10 10 M10 3 L3 10"/></svg></span>')

    spoken = "; ".join(t for _, _, t in values) or "no cell has finished"
    aria = html.escape(f"{label or 'metric'}: "
                       f"{target_label(bar, direction) or 'reported, no target'}. "
                       f"Axis {fmt(lo)} to {fmt(hi)}{' (log scale)' if log else ''}. {spoken}")
    return (f'<div class="tr" role="img" aria-label="{aria}">'
            f'<svg class="trbg" viewBox="0 0 {width} {TRACK_H}" preserveAspectRatio="none" '
            f'aria-hidden="true">{"".join(bg)}</svg>{"".join(marks)}'
            f'<span class="end lo">{fmt(lo)}</span><span class="end hi">{fmt(hi)}</span></div>')


def legend(order: Sequence[str], counts: Dict[str, Tuple[int, int]]) -> str:
    """Variant → shape, colour, and how many of its cells have finished.

    The counts answer "what is done and what is still training": these charts plot finished
    cells only, so a variant reading ``0/3`` has nothing on them yet.
    """
    order = list(order)
    if not order:
        return ""
    keys = []
    for name in order:
        done, total = counts.get(name, (0, 0))
        keys.append(f'<span class="key">{glyph(name, order)}{html.escape(name)} '
                    f'<i>{done}/{total}</i></span>')
    return (f'<div class="legend">{"".join(keys)}'
            f'<span class="key barkey"><svg width="15" height="14" aria-hidden="true">'
            f'<rect class="pass" x="7" y="1" width="8" height="12"/>'
            f'<line class="bar" x1="7" y1="0" x2="7" y2="14"/></svg>'
            f'target — shaded side passes</span>'
            f'<span class="key barkey"><svg width="13" height="14" aria-hidden="true">'
            f'<circle class="nanpt" cx="6.5" cy="7" r="3.5"/></svg>'
            f'not computed</span>'
            f'<span class="key barkey"><svg width="13" height="14" aria-hidden="true">'
            f'<path class="divpt" d="M3 3.5 L10 10.5 M10 3.5 L3 10.5"/></svg>'
            f'rollout diverged</span>'
            f'{seed_key()}</div>')


def seed_key() -> str:
    """The legend entry saying that a marker's fill is which seed it is.

    Shape and colour were both explained; the third channel was not, so a hollow marker looked
    like a different kind of thing rather than the same cell's third run.
    """
    dots = "".join(
        f'<svg width="13" height="14" aria-hidden="true">'
        f'{marker("circle", 6.5, 7, 4, fill=SEED_KEY_COLOUR, seed=s)}</svg>' for s in (0, 1, 2))
    return f'<span class="key barkey seedkey">{dots}fill \u2014 seed 0, 1, 2</span>'


def curve(series: Sequence[float], *, colour: str, floor: Optional[float] = None,
          width: int = 124, height: int = 38, label: str = "", steps: Optional[int] = None,
          log_every: Optional[int] = None, xname: str = "step") -> str:
    """One training curve, with both axes drawn and the x axis named.

    Labelled ends rather than ticks: at this size ticks crowd out the curve itself. Each y label
    is pinned to the value it names and centred on it — stacking the two flush against the top
    and bottom, as this first did, leaves each about a tenth of the plot away from the extreme
    it is supposed to mark, which reads as wrong before a reader can say why.

    The x axis carries its name, because nothing else on the page says the horizontal direction
    is training steps. The y axis carries none: the column header above it already names the
    quantity, and a second label would repeat it in less room. ``xname`` renames that axis for
    a caller whose x is not training steps -- the engine loop counts decisions -- so the label
    is never silently wrong on a page this was not written for.

    Faint rules along the left and bottom give the plot an origin to read against. A ``floor`` draws the
    collapse target, and its label is **placed** rather than fixed — above the line normally,
    below when the line is near the top, and clamped inside the box either way, because a label
    half outside its plot is worse than no label.
    """
    pts = _finite(series)
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    if floor is not None:
        lo, hi = min(lo, floor), max(hi, floor)
    span = max(hi - lo, 1e-12)
    step = width / (len(pts) - 1)
    path = " ".join(f"{i * step:.1f},{height - (v - lo) / span * height:.1f}"
                    for i, v in enumerate(pts))

    axes = (f'<path class="cax" d="M0,0 V{height} H{width}" fill="none" '
            f'vector-effect="non-scaling-stroke"/>')
    extra = ""
    if floor is not None:
        fy = height - (floor - lo) / span * height
        above = fy > 12                      # room for the label above the rule?
        ly = (fy - 3) if above else (fy + 9)
        ly = max(8.0, min(height - 2.0, ly))  # ... and never outside the box
        extra = (f'<line class="floor" x1="0" y1="{fy:.1f}" x2="{width}" y2="{fy:.1f}" '
                 f'vector-effect="non-scaling-stroke"/>'
                 f'<text class="floorlbl" x="{width - 2}" y="{ly:.1f}" text-anchor="end">'
                 f'target {fmt(floor)}</text>')
    last_x = int(steps) if steps else (len(pts) - 1) * int(log_every or 1)
    aria = html.escape(f"{label}: {fmt(pts[0])} at {xname} 0 to {fmt(pts[-1])} at {xname} "
                       f"{last_x}" + (f", target {fmt(floor)}" if floor is not None else ""))
    svg = (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'role="img" aria-label="{aria}">{axes}{extra}'
           f'<polyline points="{path}" fill="none" stroke="{colour}" stroke-width="1.6" '
           f'stroke-linejoin="round"/>'
           f'<circle cx="{width:.1f}" cy="{height - (pts[-1] - lo) / span * height:.1f}" r="2.2" '
           f'fill="{colour}"/></svg>')
    return (f'<span class="curvebox">{svg}'
            f'<span class="yax"><i class="hi">{fmt(hi)}</i><i class="lo">{fmt(lo)}</i></span>'
            f'<span class="xax"><i>0</i><i class="xname">{html.escape(xname)}</i>'
            f'<i>{last_x:,}</i></span></span>')


sparkline = curve  # the name v2/v3 used


def multi_curve(series: Sequence[Tuple[str, Sequence[Tuple[float, float]], str]], *,
                width: int = 360, height: int = 120, floor: Optional[float] = None,
                xname: str = "decision", label: str = "") -> str:
    """Several curves on one pair of axes, each with its own explicit x.

    :func:`curve` plots one series against its index, which is right for a training curve
    sampled at a fixed interval. Comparing arms needs the opposite: several series, each
    carrying its own x, on **one shared scale** -- an arm that stopped early has to end early
    on the page rather than being stretched to the full width, because a curve rescaled to fit
    is a different claim about how far it got.

    Each entry is ``(name, [(x, y), ...], colour)``. Empty series are skipped rather than
    drawn as a flat line at zero, which would read as a measured result.
    """
    drawn = [(name, [(float(x), float(y)) for x, y in pts
                     if math.isfinite(float(x)) and math.isfinite(float(y))], colour)
             for name, pts, colour in series]
    drawn = [(n, p, c) for n, p, c in drawn if len(p) >= 2]
    if not drawn:
        return ""
    xs = [x for _n, pts, _c in drawn for x, _y in pts]
    ys = [y for _n, pts, _c in drawn for _x, y in pts]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    if floor is not None:
        y_lo, y_hi = min(y_lo, floor), max(y_hi, floor)
    x_span, y_span = max(x_hi - x_lo, 1e-12), max(y_hi - y_lo, 1e-12)

    def _xy(x: float, y: float) -> str:
        return (f"{(x - x_lo) / x_span * width:.1f},"
                f"{height - (y - y_lo) / y_span * height:.1f}")

    axes = (f'<path class="cax" d="M0,0 V{height} H{width}" fill="none" '
            f'vector-effect="non-scaling-stroke"/>')
    extra = ""
    if floor is not None:
        fy = height - (floor - y_lo) / y_span * height
        ly = max(8.0, min(height - 2.0, (fy - 3) if fy > 12 else (fy + 9)))
        extra = (f'<line class="floor" x1="0" y1="{fy:.1f}" x2="{width}" y2="{fy:.1f}" '
                 f'vector-effect="non-scaling-stroke"/>'
                 f'<text class="floorlbl" x="{width - 2}" y="{ly:.1f}" text-anchor="end">'
                 f'target {fmt(floor)}</text>')
    parts = []
    for _name, pts, colour in drawn:
        end_x, end_y = _xy(*pts[-1]).split(",")
        parts.append(
            f'<polyline points="{" ".join(_xy(x, y) for x, y in pts)}" fill="none" '
            f'stroke="{colour}" stroke-width="1.6" stroke-linejoin="round"/>'
            f'<circle cx="{end_x}" cy="{end_y}" r="2.2" fill="{colour}"/>')
    lines = "".join(parts)
    names = ", ".join(name for name, _p, _c in drawn)
    aria = html.escape(f"{label}: {names} against {xname}, {fmt(y_lo)} to {fmt(y_hi)}")
    svg = (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" '
           f'height="{height}" role="img" aria-label="{aria}">{axes}{extra}{lines}</svg>')
    return (f'<span class="curvebox">{svg}'
            f'<span class="yax"><i class="hi">{fmt(y_hi)}</i><i class="lo">{fmt(y_lo)}</i></span>'
            f'<span class="xax"><i>{fmt(x_lo)}</i><i class="xname">{html.escape(xname)}</i>'
            f'<i>{fmt(x_hi)}</i></span></span>')


#: The stylesheet for what this module draws.
#:
#: It lived in the dashboard's one big sheet, which is why a block that called `track` or
#: `glyph` emitted classes nothing had declared. A drawing and the rules that make it legible
#: are one thing; a block that draws includes this, and a page that uses no such block does not
#: carry it.
CHART_CSS = """
  .glyph { vertical-align:-2px; margin-right:6px }
  /* one metric's axis: stretched SVG behind, markers positioned as HTML on top */
  .tr { position:relative; height:36px }
  .trbg { position:absolute; inset:0; width:100%; height:100% }
  /* the scale: without it a marker's position means nothing */
  .tr .end { position:absolute; bottom:-1px; font-size:9px; color:var(--muted); opacity:0.85;
    font-variant-numeric:tabular-nums; pointer-events:none }
  .tr .end.lo { left:0 }
  .tr .end.hi { right:0 }
  .mk { position:absolute; top:50%; transform:translate(-50%,-50%); line-height:0;
    pointer-events:auto }
  .axis { stroke:var(--line); stroke-width:1 }
  .bar { stroke:var(--crit); stroke-width:1.4; stroke-dasharray:3 3; fill:none }
  .pass { fill:var(--ok); fill-opacity:0.09 }
  /* Dashed, because an outlined seed-2 marker is hollow too. Grey and broken is what
     keeps "there is no number here" apart from "this is the third seed". */
  .nanpt { fill:none; stroke:var(--muted); stroke-width:1.2; stroke-dasharray:2 1.6 }
  /* Solid and in the critical colour: a diverged rollout is a result, not a missing one. */
  .divpt { fill:none; stroke:var(--crit); stroke-width:1.6; stroke-linecap:round }
  /* curve() draws these; with no stroke they were present and invisible. */
  .spark .cax { stroke:currentColor; stroke-width:1; fill:none; opacity:0.28 }
  .spark .floor { stroke:var(--crit); stroke-width:1; stroke-dasharray:2 2; opacity:0.55 }
  .spark .floorlbl { font-size:7.5px; fill:var(--crit); opacity:0.85 }
  /* a curve names both of its axes: y at the ends, x underneath. One colour for the whole
     plot, so the numbers read as part of the graph rather than as loose digits beside it. */
  .curvebox { position:relative; display:inline-block; color:var(--muted);
    padding:4px 0 12px 32px }
  .curvebox i { font-style:normal; font-size:8.5px; line-height:1;
    font-variant-numeric:tabular-nums }
  .curvebox .yax { position:absolute; left:0; top:4px; bottom:12px; width:28px }
  .curvebox .yax i { position:absolute; right:0; white-space:nowrap }
  .curvebox .yax .hi { top:0; transform:translateY(-50%) }
  .curvebox .yax .lo { bottom:0; transform:translateY(50%) }
  .curvebox .xax { position:absolute; left:32px; right:0; bottom:0;
    display:flex; justify-content:space-between; align-items:baseline }
  .curvebox .xax .xname { letter-spacing:0.04em; opacity:0.8 }
  .legend { display:flex; flex-wrap:wrap; gap:6px 16px; padding:10px 14px 2px }
  .legend .barkey { color:var(--muted) }
  .legend .key { display:inline-flex; align-items:center; gap:6px; font-size:11.5px }
  .legend .key i { font-style:normal; color:var(--muted); font-variant-numeric:tabular-nums }
  .legend .seedkey svg { margin-right:-3px }
"""
