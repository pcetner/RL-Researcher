"""Regions and rendering: what regeneration is allowed to touch, and what a page may contain.

The property under test is the one that makes an artefact safe to regenerate: a machine may
rewrite what it wrote, and may not touch a word a person typed.
"""

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher import regions  # noqa: E402
from rl_researcher.render import editable_article, md_to_html, strip_regions  # noqa: E402

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


# --------------------------------------------------------------------------- the served page
#
# `editable_article` is the one renderer that keeps an authored region addressable, and it is
# reachable only from `serve`. The pair of properties worth holding it to are that it says where
# the region is, and that nothing it does can leak into a page written to disk.

DECIDABLE = """<!-- rl: kind=report run=r1 -->
# A run

## Ledger

<!-- ledger: touches=D4 -->
2026-09-06 · r2 0.91 ✓ [F0110]
<!-- /ledger -->

## Decision (human)

<!-- authored: decision -->
- [ ] **go** — build on it
- [x] **iterate** — what changes
- [ ] **stop** — reopen the question

Reviewer notes:

_(write here)_
<!-- /authored -->
"""


def test_an_authored_region_survives_as_something_the_page_can_address():
    html = editable_article(DECIDABLE, kind="report", run="r1")
    assert 'data-region="decision"' in html and 'data-run="r1"' in html
    assert "<!--" not in html, "a marker reached the page"


def test_a_ticked_box_arrives_as_a_ticked_checkbox_not_as_text():
    """The whole point: `- [ ]` is inert text on an exported page."""
    html = editable_article(DECIDABLE, kind="report", run="r1")
    assert html.count('type="checkbox"') == 3
    assert html.count(" checked>") == 1
    assert '[x]' not in html.split("<textarea")[0]


def test_boxes_are_numbered_in_the_order_the_options_are_read_in():
    """The label is markup and may repeat a word; the ordinal is what addresses a box."""
    from rl_researcher.artefacts.state import options

    html = editable_article(DECIDABLE, kind="report", run="r1")
    body = regions.body_of(DECIDABLE, "authored", "decision")
    for i in range(len(options(body))):
        assert f'data-option="{i}"' in html
    assert 'data-option="3"' not in html


def test_the_source_of_the_region_is_carried_verbatim_for_writing_back():
    html = editable_article(DECIDABLE, kind="report", run="r1")
    src = html.split('class="authored-src" spellcheck="true">')[1].split("</textarea>")[0]
    assert src == regions.body_of(DECIDABLE, "authored", "decision").replace("&", "&amp;")


def test_a_generated_or_ledger_region_is_stripped_exactly_as_it_is_on_an_exported_page():
    html = editable_article(DECIDABLE, kind="report", run="r1")
    assert "[F0110]" in html and "0.91" in html
    assert html.count("<section") == 1, "only the authored region becomes a section"


def test_the_exported_page_has_no_editable_control_in_it():
    """`writer.write` does not know this renderer exists, and must not learn.

    The three buttons an exported page does carry are the theme switch, which every page has
    always had and which changes nothing on disk.
    """
    exported = md_to_html(strip_regions(DECIDABLE), kind="report", title="A run")
    assert "<input" not in exported
    assert "<textarea" not in exported
    assert "authored" not in exported and "data-region" not in exported
    assert exported.count("<button") == 3, "only the theme switch"


def test_a_region_a_kind_wrote_itself_edits_like_any_other():
    """A measurement's stub is the kind's own prose, with no boxes in it at all."""
    doc = DECIDABLE.replace("- [ ] **go** — build on it\n- [x] **iterate** — what changes\n"
                            "- [ ] **stop** — reopen the question\n\nReviewer notes:\n\n"
                            "_(write here)_",
                            "_(no decision is registered on a measurement.)_")
    html = editable_article(doc, kind="measurement", run="r1")
    assert 'data-region="decision"' in html
    assert 'type="checkbox"' not in html
    assert "no decision is registered" in html
