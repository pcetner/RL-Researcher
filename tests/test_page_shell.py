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

from rl_researcher.blocks import Page, Tiles  # noqa: E402
from rl_researcher.render import md_to_html  # noqa: E402
from rl_researcher.style import BASE_CSS, THEME_BUTTONS, THEME_SCRIPT  # noqa: E402

PAGES = {
    "md_to_html": md_to_html("# t\n\ntext\n", kind="report", title="t"),
    "Page.html": Page(kind="dashboard", title="t", blocks=[Tiles(items=[("1", "a")])]).html(),
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
