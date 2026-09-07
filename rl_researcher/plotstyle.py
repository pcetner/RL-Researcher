"""House style for figures.

The figures are what the human reviewer judges, so they share one palette with the report
pages (ink, muted, one fixed colour per variant, bars in red with the pass side shaded),
carry their reference lines, and label their end points instead of relying on legends.
matplotlib only; the web fonts are not available to it, so DejaVu Sans at matching sizes.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterator, List, Optional, Sequence, cast

INK = "#1A211F"
MUTED = "#5C6663"
LINE = "#D6DDDA"
ACCENT = "#2B4FBF"
STUDY = "#7A3E8F"
CRIT = "#A63A2E"
WARN = "#8F5E06"
OK = "#2E7A4C"
CODE_BG = "#EBEFED"
SURFACE = "#FFFFFF"

# One colour per *variant*, assigned by its position in the study, from a scale whose
# neighbours are far apart in hue.
#
# This used to key on the arch family and give a derived cell a shade of its parent, so that a
# reader could see what was being compared with what. In practice it did the opposite: a study
# comparing ``conv_ae`` against ``conv_ae_inv`` — which is exactly the comparison a two-by-two
# exists to make — drew them as two blues, and ``jepa`` against ``jepa_inv`` as two purples.
# The pairs the eye most needed to separate were the pairs it could least tell apart.
VARIANT_CYCLE = (
    "#2B4FBF",  # blue
    "#C2410C",  # burnt orange
    "#0F766E",  # teal
    "#7A3E8F",  # purple
    "#8F5E06",  # ochre
    "#9D174D",  # crimson
    "#0E7490",  # cyan
    "#4D7C0F",  # olive
)
FALLBACK_CYCLE = VARIANT_CYCLE
_SHADES = (0.0, 0.42, -0.28, 0.65, -0.5)  # base, lighter, darker, lighter still, darkest


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02X}" for c in rgb)


def shade(color: str, amount: float) -> str:
    """Mix toward white (amount > 0) or black (amount < 0)."""
    r, g, b = _hex_to_rgb(color)
    target = 255.0 if amount > 0 else 0.0
    a = abs(amount)
    return _rgb_to_hex((r + (target - r) * a, g + (target - g) * a, b + (target - b) * a))


def variant_color(name: str, order: Sequence[str]) -> str:
    """Stable colour for a variant, by its position in the study's variant order.

    Position rather than architecture, so that neighbours in a study are neighbours in the
    cycle and therefore far apart in hue. A study with more variants than the cycle wraps and
    then shades, which keeps every colour distinct from its immediate neighbours even at
    sixteen cells.
    """
    order = list(order)
    i = order.index(name) if name in order else len(order)
    colour = VARIANT_CYCLE[i % len(VARIANT_CYCLE)]
    wrap = i // len(VARIANT_CYCLE)
    return colour if wrap == 0 else shade(colour, _SHADES[wrap % len(_SHADES)])


_RC = {
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 10.5,
    "axes.titleweight": "semibold",
    "axes.titlelocation": "left",
    "axes.titlecolor": INK,
    "axes.labelsize": 8.5,
    "axes.labelcolor": MUTED,
    "axes.edgecolor": LINE,
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": LINE,
    "grid.linestyle": "--",
    "grid.linewidth": 0.6,
    "grid.alpha": 0.8,
    "axes.axisbelow": True,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "xtick.major.size": 3,
    "ytick.major.size": 3,
    "text.color": INK,
    "legend.frameon": False,
    "legend.fontsize": 8,
    "lines.linewidth": 1.6,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "figure.dpi": 100,
}


@contextlib.contextmanager
def style() -> Iterator[None]:
    import matplotlib

    matplotlib.use("Agg", force=False)
    # matplotlib >= 3.11 types the rc dict with literal keys; ours is a plain dict.
    with matplotlib.rc_context(cast(Any, _RC)):
        yield


def bar_line(ax, value: float, label: str, direction: str, *, axis: str = "y") -> None:
    """The registered bar as a dashed red line, the pass side shaded green."""
    if direction not in ("lower", "higher"):
        return
    if axis == "y":
        ax.axhline(value, color=CRIT, linestyle="--", linewidth=1.0, zorder=1)
        lo, hi = ax.get_ylim()
        if direction == "lower":
            ax.axhspan(min(lo, value), value, color=OK, alpha=0.07, linewidth=0, zorder=0)
        else:
            ax.axhspan(value, max(hi, value), color=OK, alpha=0.07, linewidth=0, zorder=0)
        ax.set_ylim(lo, hi)
        ax.text(1.0, value, f" {label}", transform=ax.get_yaxis_transform(), ha="left", va="center",
                fontsize=7.5, color=CRIT, clip_on=False)
    else:
        ax.axvline(value, color=CRIT, linestyle="--", linewidth=1.0, zorder=1)
        lo, hi = ax.get_xlim()
        if direction == "lower":
            ax.axvspan(min(lo, value), value, color=OK, alpha=0.07, linewidth=0, zorder=0)
        else:
            ax.axvspan(value, max(hi, value), color=OK, alpha=0.07, linewidth=0, zorder=0)
        ax.set_xlim(lo, hi)
        if label:
            ax.text(value, 1.0, f"{label}", transform=ax.get_xaxis_transform(), ha="center", va="bottom",
                    fontsize=7.5, color=CRIT, clip_on=False)


def end_label(ax, x: float, y: float, text: str, color: str, *, dx: float = 4.0, dy: float = 0.0) -> None:
    ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", va="center", ha="left",
                fontsize=8, color=color, annotation_clip=False)


def spread(ys: Sequence[float], min_gap: float) -> List[float]:
    """Nudge label positions apart (keeping their order) so end labels never overlap."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    last: Optional[float] = None
    for i in order:
        y = out[i]
        if last is not None and y - last < min_gap:
            y = last + min_gap
        out[i] = y
        last = y
    return out


def smooth(ys: Sequence[float], frac: float = 0.04, min_window: int = 3) -> List[float]:
    """Centered rolling mean over ~``frac`` of the series (no lag, unlike an EMA); nan-aware."""
    n = len(ys)
    if n == 0:
        return []
    w = max(min_window, int(round(n * frac)))
    half = w // 2
    out: List[float] = []
    for i in range(n):
        seg = [y for y in ys[max(0, i - half): i + half + 1] if y == y]
        out.append(sum(seg) / len(seg) if seg else float("nan"))
    return out


def ema(ys: Sequence[float], alpha: float = 0.1) -> List[float]:
    out: List[float] = []
    m: Optional[float] = None
    for y in ys:
        if y != y:  # nan: carry
            out.append(m if m is not None else float("nan"))
            continue
        m = y if m is None else (1 - alpha) * m + alpha * y
        out.append(m)
    return out


def savefig(fig, path: Path, dpi: int = 150) -> Path:
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return path


def fmt(v: float) -> str:
    if v != v:
        return "n/a"
    a = abs(v)
    if a >= 100:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.1f}"
    if a >= 0.01 or a == 0:
        return f"{v:.3f}"
    return f"{v:.1e}"

