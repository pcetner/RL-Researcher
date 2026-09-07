"""The blocks that draw: tiles, the progress bar, the scorecard lanes and the live sparklines.

Everything here is inline SVG built by hand. No library, no request, no build step: these pages
open from a file:// path on a laptop with no network, and a dashboard that needs a CDN is a
dashboard that is blank exactly when the network is the thing that broke.

Layout is HTML and only the track is SVG. An all-SVG chart scales its text with its container,
so labels shrink to nothing on a narrow screen, and it can offer only the browser's native
tooltip. So a lane here is an HTML row — the metric name, its target and its value are real
text — holding one small fixed-height SVG that draws the axis, the bar and the markers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar, Optional, Sequence, Tuple

from rl_researcher.blocks.base import (PANEL_CSS, Block, esc, fmt_number, panel, rows_to_md)

TRACK_H = 34            # one lane, tall enough that a dodged marker stays inside it
DODGE = 6.5             # vertical step between markers that would otherwise sit on top of each other
DODGE_MAX = 10.5
LOG_DECADES = 2.0       # a metric spanning more than this many decades gets a log axis
SHAPES = ("circle", "square", "triangle", "diamond", "plus", "cross")


TILES_CSS = """
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:1px;
    background:var(--line); border:1px solid var(--line); border-radius:10px; overflow:hidden;
    margin:0 0 16px }
  .tile { background:var(--surface); padding:10px 14px }
  .tile b { display:block; font-size:19px; font-variant-numeric:tabular-nums; letter-spacing:-0.01em }
  .tile span { font-size:10.5px; text-transform:uppercase; letter-spacing:0.06em; color:var(--muted) }
"""


@dataclass(frozen=True)
class Tiles(Block):
    """The four or five numbers a glance is for: units done, budget spent, time left, elapsed."""

    items: Sequence[Tuple[str, str]] = field(default_factory=tuple)      # (value, label)
    css: ClassVar[str] = TILES_CSS

    def md(self) -> str:
        return " · ".join(f"**{v}** {label}" for v, label in self.items)

    def html(self) -> str:
        cells = "".join(f'<div class="tile"><b>{esc(v)}</b><span>{esc(label)}</span></div>'
                        for v, label in self.items)
        return f'<div class="tiles">{cells}</div>'


PROGRESS_CSS = """
  .progwrap { display:flex; align-items:center; gap:10px; margin:0 0 16px }
  .prog { flex:1; height:6px; background:var(--line); border-radius:999px; overflow:hidden }
  .prog i { display:block; height:100%; background:var(--accent); border-radius:999px }
  .proglbl { color:var(--muted); font-size:11.5px; white-space:nowrap;
    font-variant-numeric:tabular-nums }
"""


@dataclass(frozen=True)
class Progress(Block):
    """How much of the registered budget has been spent, as one bar."""

    fraction: float = 0.0
    label: str = ""
    css: ClassVar[str] = PROGRESS_CSS

    def md(self) -> str:
        pct = max(0.0, min(1.0, float(self.fraction))) * 100
        return f"{self.label + ': ' if self.label else ''}{pct:.0f}% of the budget"

    def html(self) -> str:
        pct = max(0.0, min(1.0, float(self.fraction))) * 100
        # The percentage in words as well as in width: a bar alone says "some" and a reader
        # deciding whether to wait needs the number.
        label = f"{self.label + ': ' if self.label else ''}{pct:.0f}% of the budget"
        return (f'<div class="progwrap"><div class="prog"><i style="width:{pct:.1f}%"></i></div>'
                f'<span class="proglbl">{esc(label)}</span></div>')


# --------------------------------------------------------------------------- scorecard


@dataclass(frozen=True)
class Mark:
    """One finished unit's value on a lane."""

    value: float
    label: str = ""
    passed: Optional[bool] = None
    shape: str = "circle"
    seed: int = 0


@dataclass(frozen=True)
class Lane:
    """One registered metric: its bar, its direction, and a marker per finished unit."""

    metric: str
    direction: str = "report"
    bar: Optional[float] = None
    marks: Sequence[Mark] = ()
    target_text: str = ""


SCORE_CSS = PANEL_CSS + """
  .lanes { display:grid; grid-template-columns:auto auto 1fr; gap:2px 12px; align-items:center }
  .lanes .mname { font-size:12px; font-weight:600; white-space:nowrap }
  .lanes .mtarget { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums;
    white-space:nowrap }
  .lanes .mtrack { width:100% }
  .lane-axis { stroke:var(--line); stroke-width:1 }
  .lane-bar { stroke:var(--crit); stroke-width:1.4; stroke-dasharray:4 3 }
  .lane-pass { fill:color-mix(in srgb, var(--ok) 12%, transparent) }
  .lane-mk { stroke:var(--surface); stroke-width:1 }
  .lane-mk.ok { fill:var(--ok) }
  .lane-mk.no { fill:var(--crit) }
  .lane-mk.na { fill:var(--muted) }
  .lane-lo, .lane-hi { fill:var(--muted); font-size:9px }
  .lanes .empty { color:var(--muted); font-size:12px }
"""


def _scale(values: Sequence[float], bar: Optional[float]) -> Tuple[float, float, bool]:
    """``(low, high, log)`` for a lane, from the finite values and the bar.

    An infinite value is not allowed to set the scale: one diverged rollout used to decide the
    axis for every other mark on the row and squash them all into the left edge.
    """
    pts = [v for v in values if math.isfinite(v)]
    if bar is not None and math.isfinite(bar):
        pts = [*pts, bar]
    if not pts:
        return 0.0, 1.0, False
    lo, hi = min(pts), max(pts)
    log = lo > 0 and hi / max(lo, 1e-12) > 10 ** LOG_DECADES
    if log:
        return lo, hi, True
    if hi == lo:
        pad = abs(hi) * 0.25 or 0.5
        return lo - pad, hi + pad, False
    pad = (hi - lo) * 0.12
    return lo - pad, hi + pad, False


def _x(v: float, lo: float, hi: float, log: bool, width: float) -> float:
    if log:
        v, lo, hi = math.log10(max(v, 1e-12)), math.log10(max(lo, 1e-12)), math.log10(max(hi, 1e-12))
    if hi <= lo:
        return width / 2
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) * width


def _marker(shape: str, x: float, y: float, r: float, cls: str) -> str:
    if shape == "square":
        return f'<rect class="{cls}" x="{x - r:.1f}" y="{y - r:.1f}" width="{2 * r:.1f}" height="{2 * r:.1f}"/>'
    if shape == "triangle":
        return (f'<polygon class="{cls}" points="{x:.1f},{y - r:.1f} {x + r:.1f},{y + r:.1f} '
                f'{x - r:.1f},{y + r:.1f}"/>')
    if shape == "diamond":
        return (f'<polygon class="{cls}" points="{x:.1f},{y - r:.1f} {x + r:.1f},{y:.1f} '
                f'{x:.1f},{y + r:.1f} {x - r:.1f},{y:.1f}"/>')
    return f'<circle class="{cls}" cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}"/>'


@dataclass(frozen=True)
class Scorecard(Block):
    """Every registered metric as a lane, one marker per finished unit against its bar.

    Read down: an arm should sit on the pass side of every lane, and every seed of an arm should
    sit on the same side of each. Two seeds passing and one failing is a different result from
    three seeds scraping past, and a mean hides which one happened.
    """

    title: str = ""
    lanes: Sequence[Lane] = field(default_factory=tuple)
    width: int = 460
    css: ClassVar[str] = SCORE_CSS

    def md(self) -> str:
        rows = []
        for lane in self.lanes:
            vals = ", ".join(f"{m.label}={fmt_number(m.value)}" for m in lane.marks) or "—"
            rows.append([lane.metric, lane.target_text or _target_text(lane), vals])
        return rows_to_md(["metric", "target", "per unit"], rows)

    def _lane_svg(self, lane: Lane) -> str:
        w, h = float(self.width), float(TRACK_H)
        mid = h / 2
        lo, hi, log = _scale([m.value for m in lane.marks], lane.bar)
        out = [f'<svg class="mtrack" viewBox="0 0 {w:.0f} {h:.0f}" preserveAspectRatio="none" '
               f'height="{h:.0f}" role="img" aria-label="{esc(lane.metric)}">']
        if lane.bar is not None and math.isfinite(lane.bar) and lane.direction in ("higher", "lower"):
            bx = _x(lane.bar, lo, hi, log, w)
            if lane.direction == "higher":
                out.append(f'<rect class="lane-pass" x="{bx:.1f}" y="2" width="{max(w - bx, 0):.1f}" height="{h - 4:.0f}"/>')
            else:
                out.append(f'<rect class="lane-pass" x="0" y="2" width="{bx:.1f}" height="{h - 4:.0f}"/>')
        out.append(f'<line class="lane-axis" x1="0" y1="{mid:.1f}" x2="{w:.0f}" y2="{mid:.1f}"/>')
        if lane.bar is not None and math.isfinite(lane.bar):
            bx = _x(lane.bar, lo, hi, log, w)
            out.append(f'<line class="lane-bar" x1="{bx:.1f}" y1="3" x2="{bx:.1f}" y2="{h - 3:.0f}"/>')
        used: dict = {}
        for m in lane.marks:
            if not math.isfinite(m.value):
                continue
            x = _x(m.value, lo, hi, log, w)
            slot = used.get(round(x / 6), 0)
            used[round(x / 6)] = slot + 1
            dy = min(DODGE * ((slot + 1) // 2) * (1 if slot % 2 else -1), DODGE_MAX)
            cls = "lane-mk " + ("ok" if m.passed else "no" if m.passed is False else "na")
            # The value in the marker's own title, so the page states every number the markdown
            # states and hovering a dot answers "which unit is that" without a legend.
            label = f"{m.label} = {fmt_number(m.value)}" if m.label else fmt_number(m.value)
            out.append(f"<g><title>{esc(label)}</title>"
                       + _marker(m.shape, x, mid + dy, 3.4, cls) + "</g>")
        out.append(f'<text class="lane-lo" x="1" y="{h - 3:.0f}">{esc(fmt_number(lo, 2))}</text>')
        out.append(f'<text class="lane-hi" x="{w - 1:.0f}" y="{h - 3:.0f}" text-anchor="end">'
                   f"{esc(fmt_number(hi, 2))}</text>")
        out.append("</svg>")
        return "".join(out)

    def html(self) -> str:
        if not self.lanes:
            return panel(self.title, '<div class="empty">no finished units yet</div>')
        cells = []
        for lane in self.lanes:
            cells.append(f'<div class="mname">{esc(lane.metric)}</div>')
            cells.append(f'<div class="mtarget">{esc(lane.target_text or _target_text(lane))}</div>')
            cells.append(self._lane_svg(lane))
        return panel(self.title, f'<div class="lanes">{"".join(cells)}</div>')


def _target_text(lane: Lane) -> str:
    if lane.bar is None:
        return "report"
    arrow = "≥" if lane.direction == "higher" else "≤"
    return f"{arrow} {fmt_number(lane.bar)}"


# --------------------------------------------------------------------------- sparklines


CURVE_CSS = PANEL_CSS + """
  .curves { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px }
  .curvebox { border:1px solid var(--line); border-radius:8px; padding:8px 10px }
  .curvebox .ct { font-size:11px; color:var(--muted); text-transform:uppercase;
    letter-spacing:0.05em; display:flex; justify-content:space-between; gap:8px }
  .curvebox .cv { font-variant-numeric:tabular-nums; color:var(--ink); font-weight:600 }
  .spark { display:block; width:100%; height:44px; margin-top:4px }
  .spark .ln { fill:none; stroke:var(--accent); stroke-width:1.5 }
  .spark .floor { stroke:var(--crit); stroke-width:1; stroke-dasharray:3 3 }
  .curvebox .cf { color:var(--muted); font-size:10.5px; margin-top:3px;
    font-variant-numeric:tabular-nums }
"""


@dataclass(frozen=True)
class Curve:
    key: str
    title: str
    values: Sequence[float] = ()
    floor: Optional[float] = None


@dataclass(frozen=True)
class Curves(Block):
    """The training curves of a unit that is still running, one sparkline each.

    The floor is drawn where a registered bar applies, so a curve that has plateaued under its
    bar reads as a decision to make rather than as a line going along.
    """

    title: str = ""
    curves: Sequence[Curve] = field(default_factory=tuple)
    css: ClassVar[str] = CURVE_CSS

    def md(self) -> str:
        rows = []
        for c in self.curves:
            vals = [v for v in c.values if v == v]
            last = fmt_number(vals[-1]) if vals else "n/a"
            floor = fmt_number(c.floor) if c.floor is not None else "—"
            rows.append([c.title, last, floor, str(len(vals))])
        return rows_to_md(["curve", "last", "floor", "points"], rows)

    def _spark(self, c: Curve) -> str:
        vals = [float(v) for v in c.values if v == v and math.isfinite(float(v))]
        if len(vals) < 2:
            return '<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none"></svg>'
        lo, hi = min(vals), max(vals)
        if c.floor is not None and math.isfinite(c.floor):
            lo, hi = min(lo, c.floor), max(hi, c.floor)
        span = (hi - lo) or 1.0
        pts = " ".join(f"{i / (len(vals) - 1) * 100:.2f},{28 - (v - lo) / span * 26:.2f}"
                       for i, v in enumerate(vals))
        floor = ""
        if c.floor is not None and math.isfinite(c.floor):
            y = 28 - (c.floor - lo) / span * 26
            floor = f'<line class="floor" x1="0" y1="{y:.2f}" x2="100" y2="{y:.2f}"/>'
        return ('<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">'
                f'{floor}<polyline class="ln" points="{pts}"/></svg>')

    def html(self) -> str:
        if not self.curves:
            return ""
        boxes = []
        for c in self.curves:
            vals = [v for v in c.values if v == v]
            last = fmt_number(vals[-1]) if vals else "n/a"
            foot = f"{len(vals)} points"
            if c.floor is not None:
                foot += f" · floor {fmt_number(c.floor)}"
            boxes.append(f'<div class="curvebox"><div class="ct"><span>{esc(c.title)}</span>'
                         f'<span class="cv">{esc(last)}</span></div>{self._spark(c)}'
                         f'<div class="cf">{esc(foot)}</div></div>')
        return panel(self.title, f'<div class="curves">{"".join(boxes)}</div>')
