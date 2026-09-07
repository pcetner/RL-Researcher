"""Typesetting a metric's definition into the page.

A metric a reader cannot look up is a metric they will guess at, so a report prints the formula
beside the number. The failure modes are quiet ones: a formula set four times larger than its
neighbour, an SVG that keeps its own black fill and so vanishes on a dark page, or one bad
expression taking the whole document down with it.

Moved here from `Auto-SM64/python/tests/study/test_dashboard.py`, which iterated the consuming
project's own formula table. The formulas below are literals for that reason: what is under
test is the typesetter, and a test that walks another repository's registry stops running the
moment that registry moves.
"""

import re

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from rl_researcher.mathtex import PT_PER_EM, formula_svg  # noqa: E402

#: One of each shape the typesetter has to keep at the same size: a two-level fraction, the
#: same with a qualifier, a single line, and a subscripted sum.
FORMULAS = {
    "one_step_ratio": r"\dfrac{\|\hat{z}_{t+1} - z_{t+1}\|}{\|z_t - z_{t+1}\|}",
    "drift_ratio_3": r"\dfrac{\|\hat{z}_{t+k} - z_{t+k}\|}{\|z_t - z_{t+k}\|},\; k=3",
    "action_entropy": r"-\sum_a p_a \log p_a / \log |A|",
    "coverage": r"|\{(\lfloor x/g \rfloor, \lfloor z/g \rfloor)\}|",
}


def test_every_formula_is_set_at_the_same_type_size():
    """Fixing the box height instead set a one-line expression four times larger than a
    two-level fraction, which had to divide the same height across four stacked rows."""
    sizes = []
    for latex in FORMULAS.values():
        svg = formula_svg(latex, scale=1.15)
        box = float(re.search(r"height:([\d.]+)em", svg).group(1))
        rows = float(re.search(r'viewBox="0 0 [\d.]+ ([\d.]+)"', svg).group(1))
        sizes.append(box / rows * PT_PER_EM)
    assert max(sizes) - min(sizes) < 0.01, (min(sizes), max(sizes))
    assert abs(sizes[0] - 1.15) < 0.01          # ... and it is the size that was asked for


def test_a_formula_renders_to_inline_svg_that_takes_the_pages_ink_colour():
    latex = FORMULAS["one_step_ratio"]
    svg = formula_svg(latex)
    assert svg.startswith("<svg") and "<path" in svg
    assert 'fill="currentColor"' in svg     # ... rather than a fixed black on a dark page
    assert "<?xml" not in svg and "http-equiv" not in svg
    assert formula_svg(latex) is svg        # memoised: the same object, not typeset again
    assert formula_svg("{") == ""           # a bad formula costs a picture, not the page


def test_two_formulas_on_one_page_do_not_share_glyph_ids():
    """Both SVGs go inline into the same document, so a glyph id defined twice means the second
    expression silently draws the first one's letters."""
    a = formula_svg(FORMULAS["one_step_ratio"])
    b = formula_svg(FORMULAS["action_entropy"])
    ids_a = set(re.findall(r'id="([^"]+)"', a))
    ids_b = set(re.findall(r'id="([^"]+)"', b))
    assert ids_a and ids_b
    assert not (ids_a & ids_b), sorted(ids_a & ids_b)


def test_nothing_a_formula_emits_reaches_outside_its_own_box():
    """A global `<style>` or a leaked namespace would style the page around it."""
    svg = formula_svg(FORMULAS["drift_ratio_3"])
    # A global `<style>` is the one that would reach outside: it applies to the document the
    # SVG is inlined into, not to the SVG. The `<metadata>` block and the comment carrying the
    # LaTeX source stay -- they render nothing and they say what the picture is of.
    assert "<style" not in svg
    assert "xmlns:xlink" not in svg
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
