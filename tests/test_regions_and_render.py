"""Regions and rendering: what regeneration is allowed to touch, and what a page may contain.

The property under test is the one that makes an artefact safe to regenerate: a machine may
rewrite what it wrote, and may not touch a word a person typed.
"""

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher import regions  # noqa: E402
from rl_researcher.render import md_to_html, strip_regions  # noqa: E402

DOC = """<!-- rl: kind=report run=r1 commit=abc -->
# A run

## Summary

<!-- generated: headline -->
| metric | ctrl |
|---|---|
| r2 | 0.91 ✓ |
<!-- /generated -->

## Reading

<!-- authored: reading -->
The hinge is doing the work.   Two spaces above are deliberate.
<!-- /authored -->
"""


def test_regions_round_trip_byte_for_byte():
    assert regions.join(regions.split(DOC)) == DOC


def test_an_unclosed_region_is_an_error_with_its_line_number():
    with pytest.raises(regions.RegionError) as exc:
        regions.find("intro\n\n<!-- generated: x -->\nbody, and no close\n")
    assert "line 3" in str(exc.value) and "never closed" in str(exc.value)


def test_regenerating_rewrites_generated_and_keeps_authored_exactly():
    """The whole safety property: a re-plot must not touch the reading."""
    fresh = regions.set_region(DOC, "generated", "headline", "| metric | ctrl |\n|---|---|\n| r2 | 0.95 |")
    fresh = regions.set_region(fresh, "authored", "reading", "_(write here)_")   # a fresh stub
    merged = regions.preserve_authored(DOC, fresh)
    assert "0.95" in merged                                     # the numbers moved
    assert "The hinge is doing the work.   Two spaces" in merged  # the words did not, spaces included
    assert "_(write here)_" not in merged


def test_an_authored_region_that_is_new_keeps_the_fresh_stub():
    old = "<!-- authored: reading -->\nkept\n<!-- /authored -->\n"
    new = old + "\n<!-- authored: decision -->\n- [ ] go\n<!-- /authored -->\n"
    merged = regions.preserve_authored(old, new)
    assert "kept" in merged and "- [ ] go" in merged


def test_setting_a_region_that_does_not_exist_is_refused_by_default():
    with pytest.raises(regions.RegionError):
        regions.set_region(DOC, "generated", "nope", "x")
    assert "nope" in regions.set_region(DOC, "generated", "nope", "x", append_if_missing=True)


def test_attributes_and_names_are_read_off_the_marker():
    [r] = regions.find("<!-- ledger: touches=D4 metric=r2 -->\nx\n<!-- /ledger -->")
    assert r.attrs == {"touches": "D4", "metric": "r2"}
    [n] = regions.find("<!-- generated: headline -->\nx\n<!-- /generated -->")
    assert n.name == "headline"


def test_the_header_stamp_and_the_section_order_are_readable():
    assert regions.header_of(DOC)["run"] == "r1"
    assert regions.headings(DOC) == ["Summary", "Reading"]


# --------------------------------------------------------------------------- rendering


def test_a_rendered_page_fetches_nothing():
    """It has to open from a file:// path on a laptop with no network."""
    html = md_to_html("# T\n\ntext with a [link](https://example.com/x)\n", kind="report", title="T")
    assert "src=\"http" not in html and "@import" not in html
    assert "<link" not in html and "<script src" not in html


def test_raw_html_in_the_markdown_is_escaped_not_passed_through():
    html = md_to_html("# T\n\n<script>alert(1)</script>\n", kind="report", title="T")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_a_wide_table_scrolls_inside_itself():
    html = md_to_html("| a | b |\n|---|---|\n| 1 | 2 |\n", kind="report", title="T")
    assert 'class="table-wrap"' in html and "<table>" in html


def test_an_image_is_inlined_and_a_missing_one_says_so(tmp_path):
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    (tmp_path / "fig.png").write_bytes(png)
    html = md_to_html("![a figure](fig.png)\n\n![gone](nope.png)\n", kind="report", title="T",
                      embed_images_from=tmp_path)
    assert "data:image/png;base64," in html
    assert "missing figure" in html


def test_the_markers_never_reach_the_page():
    assert "<!--" not in strip_regions(DOC)
    assert "The hinge is doing the work." in strip_regions(DOC)


def test_the_page_carries_its_kind_so_a_layout_can_be_per_kind():
    assert 'class="doc kind-state"' in md_to_html("# S\n", kind="state", title="S")
