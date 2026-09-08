"""The page shell: the parts of a rendered page that are not any one block's business.

These are cheap tests for expensive failures. A page whose script tag is malformed still looks
right in a screenshot, still passes every test about its content, and has a dead theme toggle
and no scroll restore — which is exactly what happened: `THEME_SCRIPT` carried its own
``<script>`` tags while both renderers wrapped it anyway, so every page this package produced
shipped three opens and two closes and the browser read the literal text ``<script>`` as the
first token of the program.
"""

import re

import pytest

pytestmark = pytest.mark.tier1

from pathlib import Path  # noqa: E402

from rl_researcher.blocks import Page, Tiles  # noqa: E402
from rl_researcher.config import Config  # noqa: E402
from rl_researcher.render import md_to_html  # noqa: E402
from rl_researcher.serve import page as served_page  # noqa: E402
from rl_researcher.style import BASE_CSS, THEME_BUTTONS, THEME_SCRIPT  # noqa: E402

# Three renderers build a whole document: the markdown artefacts, the block pages, and the
# dashboard. All three are held to the same shell rules here rather than each being trusted,
# which is how a malformed script tag reached every page in the package the first time.
PAGES = {
    "md_to_html": md_to_html("# t\n\ntext\n", kind="report", title="t"),
    "Page.html": Page(kind="dashboard", title="t", blocks=[Tiles(items=[("1", "a")])]).html(),
    "serve.page": served_page(Config(root=Path("."), name="t")),
}
IDS = sorted(PAGES)


@pytest.mark.parametrize("name", IDS)
def test_a_page_has_exactly_one_script_and_it_starts_with_the_program(name):
    html = PAGES[name]
    assert html.count("<script") == 1 and html.count("</script>") == 1
    body = re.search(r"<script>(.*?)</script>", html, re.S).group(1).strip()
    assert body.startswith("(function"), body[:60]
    assert "<script" not in body


def test_the_theme_script_is_a_body_not_a_tag():
    """Every caller wraps it, so it must not wrap itself."""
    assert "<script" not in THEME_SCRIPT and "</script>" not in THEME_SCRIPT
    assert THEME_SCRIPT.strip().startswith("(function")


@pytest.mark.parametrize("name", IDS)
def test_a_page_is_a_whole_document(name):
    html = PAGES[name]
    assert html.startswith("<!doctype html>")
    assert html.count("<html") == 1 and html.rstrip().endswith("</html>")
    assert '<meta charset="utf-8">' in html
    assert html.count("<style>") == 1 and html.count("</style>") == 1


@pytest.mark.parametrize("name", IDS)
def test_a_page_fetches_nothing(name):
    html = PAGES[name]
    assert "src=\"http" not in html and "href=\"http" not in html
    assert "@import" not in html and "<link" not in html


@pytest.mark.parametrize("name", IDS)
def test_a_page_carries_the_theme_control_and_the_tokens_it_switches(name):
    html = PAGES[name]
    assert 'data-mode="dark"' in html and 'data-mode="light"' in html
    # All three ways a reader can be in dark mode: the media query, the explicit attribute, and
    # the "system" default that sets no attribute at all.
    assert "prefers-color-scheme: dark" in html
    assert ':root[data-theme="dark"]' in html
    assert ':root:not([data-theme="light"])' in html


def test_the_theme_buttons_name_the_three_states():
    assert THEME_BUTTONS.count("<button") == 3
    for mode in ("system", "light", "dark"):
        assert f'data-mode="{mode}"' in THEME_BUTTONS


def test_no_colour_is_baked_where_a_theme_cannot_reach_it():
    """A hex literal outside the token blocks is a colour that stays light in dark mode."""
    without_tokens = re.sub(r":root[^{]*\{[^}]*\}", "", BASE_CSS, flags=re.S)
    without_tokens = re.sub(r"@media[^{]*\{.*?\n  \}", "", without_tokens, flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", without_tokens), \
        "a baked hex outside the token definitions cannot follow the theme"


# ── the stylesheet the charts are drawn against ───────────────────────────────────────────
# Moved from Auto-SM64's dashboard tests, where they asserted against that project's own
# stylesheet. The rules are the package's, and they live beside the code that draws them:
# `charts.CHART_CSS` for what `charts.py` puts on a page, `BASE_CSS` for the tokens both use.
# A rule in the wrong sheet is a chart with markup and no ink, and a page ships the stylesheet
# of exactly the blocks it used -- so a drawing block that does not carry CHART_CSS renders
# unstyled, which is what `Block.undeclared` holds every block to.


def _rule(css: str, selector: str) -> str:
    return css.split(selector)[1].split("}")[0]


def test_the_curve_axis_lines_are_drawn_and_not_just_present():
    """They shipped with no stroke rule, so they were in the markup and invisible on the page."""
    from rl_researcher.charts import CHART_CSS

    rule = _rule(CHART_CSS, ".spark .cax")
    assert "stroke:currentColor" in rule and "opacity:" in rule


def test_the_y_labels_are_centred_on_the_ends_they_mark():
    """Stacked flush, each sat about a tenth of the plot away from its own extreme."""
    from rl_researcher.charts import CHART_CSS

    for sel in (".curvebox .yax .hi", ".curvebox .yax .lo"):
        assert "translateY(" in _rule(CHART_CSS, sel), sel


def test_no_chart_colour_is_a_baked_hex_in_any_stylesheet():
    """A hex here is the light-mode hex, and dark mode then carries brick red on near-black.

    The tokens are declared once, in BASE_CSS's `:root`; every other reference in every sheet
    the package ships has to go through `var()` or dark mode cannot move it.
    """
    from rl_researcher import plotstyle as ps
    from rl_researcher.blocks import ALL_BLOCKS
    from rl_researcher.charts import CHART_CSS
    from rl_researcher.style import BASE_CSS

    sheets = BASE_CSS + CHART_CSS + "".join(dict.fromkeys(b.css for b in ALL_BLOCKS))
    for name in ("CRIT", "OK", "WARN"):
        assert sheets.count(getattr(ps, name)) == 1, name
    for token in ("--crit", "--ok", "--warn"):
        # Referenced through `var()` wherever it is used, and declared exactly once, in the
        # `:root` block every page carries.
        assert f"var({token})" in sheets, token
        assert token in BASE_CSS, token
