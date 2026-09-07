"""Typeset LaTeX to inline SVG, with no renderer shipped to the browser.

The dashboard explains its metrics, and a metric is a formula. Written as plain text they read
badly: ``|pred(a_true) - pred(a_other)| / one-step error`` is a sentence pretending to be maths.

matplotlib's mathtext engine typesets a useful subset of LaTeX, and its SVG backend emits the
glyphs as **paths** — so the result carries no font files, no script and no request, and the
page stays the single self-contained file it has always been. Roughly 6-10 KB per formula
against the ~1.3 MB a bundled KaTeX plus its fonts would add to a file rewritten every fifteen
seconds.

Four adjustments make the output usable inline:

* the glyphs carry no fill of their own and fall back to SVG's default black, so the root
  element is given ``fill="currentColor"`` and every glyph inherits the theme's ink colour.
  (The background rectangle sets ``fill: none`` explicitly and is unaffected.) A literal hex is
  also rewritten, for matplotlib versions that emit one.
* it sizes the ``<svg>`` in points. Stripped in favour of the ``viewBox`` plus a CSS height in
  ``em``, so a formula scales with the text around it instead of sitting at a fixed size.
* it embeds a ``*{stroke-linejoin: round; ...}`` stylesheet. An SVG ``<style>`` inline in an
  HTML document is **not** scoped to that SVG, so the rule would reach the whole page.
* it declares the SVG and xlink namespaces, the page's only ``http://`` URLs, which makes "this
  page fetches nothing" awkward to assert. Inline SVG needs neither, and ``xlink:href`` becomes
  the plain ``href`` every current browser reads.

Results are memoised: the formulas are constants, and re-typesetting ten of them on every
fifteen-second render would cost more than everything else on the page combined.
"""

from __future__ import annotations

import hashlib
import io
import re
from functools import lru_cache

_FILL = re.compile(r"fill:\s*#[0-9a-fA-F]{6}")
_SIZE = re.compile(r'(<svg[^>]*?)\s+width="[^"]*"\s+height="[^"]*"')
_XMLDECL = re.compile(r"<\?xml[^>]*\?>\s*|<!DOCTYPE[^>]*>\s*", re.I)
_METADATA = re.compile(r"<metadata>.*?</metadata>\s*", re.S)
_GLOBALCSS = re.compile(r"<style[^>]*>.*?</style>\s*", re.S)
_NS = re.compile(r'\s+(?:xmlns(?::\w+)?|version)="[^"]*"')
_VIEWBOX = re.compile(r'viewBox="0 0 [\d.]+ ([\d.]+)"')
PT_PER_EM = 11.0     # the point size the expressions are set at, below


@lru_cache(maxsize=256)
def formula_svg(latex: str, *, scale: float = 1.15) -> str:
    """``latex`` (without the surrounding ``$``) as an inline SVG that inherits its colour.

    ``scale`` is the size of the *type*, relative to the text around it -- not the size of the
    box. Fixing the box height instead, as this first did, sets a one-line expression's glyphs
    four times larger than a two-level fraction's, because the fraction has to divide the same
    height across four stacked rows. Sizing from the viewBox keeps the glyphs identical
    everywhere and lets a tall expression occupy the height it genuinely needs.

    Returns an empty string if the expression will not typeset, so a malformed formula costs a
    missing picture rather than a broken page.
    """
    if not latex:
        return ""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(0.01, 0.01))
        fig.text(0, 0, f"${latex}$", fontsize=PT_PER_EM)
        buf = io.StringIO()
        fig.savefig(buf, format="svg", bbox_inches="tight", pad_inches=0.02, transparent=True)
        plt.close(fig)
    except Exception:  # noqa: BLE001 - a formula that will not typeset must not take the page down
        return ""

    svg = buf.getvalue()
    svg = _XMLDECL.sub("", svg)
    svg = _METADATA.sub("", svg)
    # matplotlib ships a `*{stroke-linejoin: round; ...}` rule inside the SVG. Inside an HTML
    # document that stylesheet is not scoped to the SVG -- it applies to the whole page.
    svg = _GLOBALCSS.sub("", svg)
    # Namespace declarations are unnecessary for inline SVG and are the page's only http:// URLs,
    # which makes "nothing is fetched" harder to check than it should be. xlink:href goes with
    # them; SVG 2's plain href is what every current browser reads anyway.
    svg = _NS.sub("", svg)
    svg = svg.replace("xlink:href=", "href=")
    svg = _FILL.sub("fill: currentColor", svg)
    svg = _SIZE.sub(r"\1", svg, count=1)
    # Glyph path ids are per-character (`DejaVuSans-31`), so ten formulas on one page collide on
    # every shared digit. The shapes are identical and it renders correctly either way, but
    # duplicate ids in a document are invalid, so each formula gets its own suffix.
    tag = hashlib.sha1(latex.encode("utf-8")).hexdigest()[:6]
    svg = re.sub(r'id="([^"]+)"', rf'id="\1-{tag}"', svg)
    svg = re.sub(r'href="#([^"]+)"', rf'href="#\1-{tag}"', svg)
    # The viewBox is in points, and the text was set at PT_PER_EM of them, so dividing by it
    # converts the expression's own height into ems of its own type.
    found = _VIEWBOX.search(svg)
    height = (float(found.group(1)) / PT_PER_EM * scale) if found else scale
    svg = svg.replace(
        "<svg ", f'<svg class="tex" fill="currentColor" style="height:{height:.3f}em" ', 1)
    return svg.strip()


def cache_info() -> str:
    info = formula_svg.cache_info()
    return f"{info.hits} hits, {info.misses} rendered"
