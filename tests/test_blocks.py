"""Every block, held to the same four rules.

The rules are not style. Each is a way a page has actually gone wrong: a class that no shipped
stylesheet defines renders as unstyled text; raw HTML in a markdown file breaks the diff that
makes a report reviewable; a fetched asset makes a page blank exactly when the network is what
failed; and a number that survives into the markdown but not the HTML means the file and the
page disagree about the result.
"""

import re

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher.blocks import (ALL_BLOCKS, ArmCell, ArmTable, Banner, Claims,  # noqa: E402
                                  Curve, Curves, DoneUnit, DoneUnits, Failure, Failures, Figure,
                                  Footer, Gallery, Grid, Header, IndexEntry, IndexTable, KV,
                                  Ladder, Lane, Lead, LiveUnit, LiveUnits, LogTail, Mark,
                                  LogLine, MetricLane, MetricLanes, MetricRow, MetricsTable, Notices,
                                  Page, PageFoot, Progress, Prose, QueuedUnits, RunLog,
                                  Scorecard, Stub, Tiles, UnitsTable, numbers_in)

#: One populated instance of every block. Empty blocks pass every rule trivially, so the
#: parametrised checks below run against blocks that actually have something to say.
SAMPLES = [
    Banner("post-hoc", "Run after seeing which result.", tone="crit"),
    Claims(rows=[("F0142", "var5 participation_ratio_norm", "0.108 ± 0.006")]),
    Curves(title="Live", curves=[Curve("repr_loss", "Representation loss", [3.0, 2.0, 1.5], floor=1.0)]),
    Figure(src="scorecard.png", caption="Every registered metric, one dot per unit."),
    Footer(text="Written by the study runner.", command="python -m rl_researcher.report study5"),
    Gallery(title="Evidence", items=[("var5 seed 0", "var5/seed0/strip_0.png")]),
    Grid(title="Ladder", columns=["seed0", "seed1"], rows=["var5", "cov5"],
         cells={("var5", "seed0"): ("0.108 ✓", "ok"), ("cov5", "seed1"): ("0.02 ✗", "crit")}),
    Header(title="study5", state="FINISHED", tone="ok", heartbeat="12 min"),
    KV(title="Provenance", pairs=[("Commit", "db974dc"), ("Data", "castle-k20-1@bf3c40e4")]),
    LogTail(title="Log", lines=[("crit", "FAILED: CUDA out of memory"), ("", "step 500/10000")]),
    MetricsTable(title="Registered metrics", arms=["ctrl", "var5"],
                 rows=[MetricRow("participation_ratio_norm", ["0.019 ✗", "0.108 ✓"],
                                 ["crit", "ok"], "≥ 0.100", "collapse floor")], show_why=True),
    Notices(title="Failed", items=[("crit", "var5/seed1", "CUDA out of memory at step 4200")]),
    Progress(fraction=0.42, label="budget"),
    Prose("Two paragraphs.\n\nThe second one."),
    Scorecard(title="Scorecard", lanes=[
        Lane("participation_ratio_norm", "higher", 0.1,
             [Mark(0.108, "var5 s0", True), Mark(0.019, "ctrl s0", False), Mark(float("inf"), "x", False)]),
        Lane("loss", "report", None, [Mark(1.25, "var5 s0")]),
    ]),
    Stub(options=[("go", "build Phase 4"), ("iterate", "what changes")], ticked=["iterate"],
         notes="The hinge is doing the work."),
    Tiles(items=[("18/18", "units done"), ("5.4 h", "elapsed")]),
    UnitsTable(title="Units", rows=[("var5/seed0", 10000, 1007.2, "complete")]),
    # ── the live page ──────────────────────────────────────────────────────────────────────
    ArmTable(title="By arm", noun="arm", order=["ctrl", "var5"],
             heads=[("Participation ratio", "> 0.100")],
             rows=[("var5", 3, [ArmCell("0.108", "ok", "✓", "± 0.006", "", True)])]),
    DoneUnits(units=[DoneUnit(arm="var5", seed=0, seconds=1007.2, step=10000,
                              cells=[("0.108", "ok", "✓")],
                              curves=[("Representation loss", [3.0, 1.5], 1.0)],
                              notes=["resumed at 4,200"])],
              order=["ctrl", "var5"], heads=[("Participation ratio", "> 0.100")],
              curve_titles=["Representation loss"]),
    Failures(units=[Failure(arm="var5", seed=1, error="CUDA out of memory",
                            where=["stopped at 4,200 of 10,000"])], order=["var5"]),
    IndexTable(entries=[IndexEntry(name="study5", kind="study", state="running", tone="accent",
                                   href="studies/study5/dashboard.html", done="2/6",
                                   total_pct=33.0, eta="4m")]),
    Ladder(noun="arm", seeds=[0, 1], order=["var5"],
           cells={("var5", 0): ("done", "ok", "Participation ratio 0.108"),
                  ("var5", 1): ("queued", "muted", "10,000 steps")}),
    Lead(arm="var5", seed=0, order=["ctrl", "var5"],
         stats=[("Participation ratio", 0.108, "> 0.100", True)]),
    LiveUnits(units=[LiveUnit(arm="var5", seed=2, step=4200, max_steps=10000, rate=3.7,
                              eta_seconds=1560.0,
                              curves=[("Representation loss", [3.0, 2.0, 1.5], 1.5, 1.0)],
                              notes=["updated 3s ago"])],
              order=["ctrl", "var5"], curve_titles=["Representation loss"]),
    MetricLanes(title="Registered metrics", order=["ctrl", "var5"],
                lanes=[MetricLane(name="participation_ratio_norm", title="Participation ratio",
                                  target="> 0.100", bar=0.1, direction="higher",
                                  definition="Effective dimensions over latent width.",
                                  why="The collapse floor.", mean="0.108",
                                  values=[("var5", 0.108, "var5 seed 0: 0.108")], seeds=[0])]),
    QueuedUnits(units=[("ctrl", 0), ("ctrl", 1)], order=["ctrl", "var5"]),
    PageFoot(run="study5", device="NVIDIA GeForce GTX 1650", note="snapshot castle-k20-1",
             when="14:02:11", tail="refreshing every 15s"),
    RunLog(title="log", lines=[
        LogLine(text="FAILED: CUDA out of memory", tone="crit", stamp="14:02:09"),
        LogLine(stamp="14:02:10", unit="var5 seed 0", colour="#4c8", step="4,200",
                max_steps="10,000", rest="loss 1.502")]),
]

IDS = [type(b).__name__ for b in SAMPLES]


def test_every_block_in_the_inventory_has_a_sample():
    """A block with no sample is a block none of the rules below are actually checking."""
    assert {type(b).__name__ for b in SAMPLES} == {b.__name__ for b in ALL_BLOCKS}


@pytest.mark.parametrize("block", SAMPLES, ids=IDS)
def test_every_class_it_emits_is_one_it_declares(block):
    assert block.undeclared() == []


@pytest.mark.parametrize("block", SAMPLES, ids=IDS)
def test_markdown_carries_no_html(block):
    md = block.md()
    assert "<" not in md.replace("<!--", ""), md
    assert "&nbsp;" not in md


@pytest.mark.parametrize("block", SAMPLES, ids=IDS)
def test_nothing_is_fetched(block):
    html = block.html()
    assert "http://" not in html and "https://" not in html
    assert "@import" not in html and "<script" not in html


@pytest.mark.parametrize("block", SAMPLES, ids=IDS)
def test_the_html_states_every_number_the_markdown_states(block):
    """The file and the page must not disagree about a result."""
    md_numbers = set(numbers_in(block.md()))
    html_numbers = set(numbers_in(re.sub(r"<style>.*?</style>", "", block.html(), flags=re.S)))
    # The HTML may hold more (SVG geometry); it may never hold less.
    missing = md_numbers - html_numbers
    assert not missing, f"{type(block).__name__} drops {sorted(missing)} when rendered"


@pytest.mark.parametrize("block", SAMPLES, ids=IDS)
def test_both_renderings_are_stable(block):
    assert block.md() == block.md() and block.html() == block.html()


# --------------------------------------------------------------------------- specific behaviour


def test_a_page_ships_the_css_of_exactly_the_blocks_it_used():
    page = Page(kind="report", title="T", blocks=[Tiles(items=[("1", "a")])])
    css = page.css()
    assert ".tiles" in css
    assert ".lanes" not in css, "a page must not carry the stylesheet of a block it does not use"
    assert page.undeclared() == []


def test_a_finished_page_does_not_reload_itself():
    """A run that will not change again is a record, not a dashboard."""
    assert "http-equiv=\"refresh\"" in Page(kind="d", title="T", refresh=15).html()
    assert "refresh" not in Page(kind="d", title="T", refresh=None).html()


def test_a_diverged_unit_never_sets_the_scale_or_clears_a_bar():
    """One runaway rollout used to decide the axis for every other mark on the row."""
    lane = Lane("r", "higher", 0.1, [Mark(0.108, "a", True), Mark(float("inf"), "b", False)])
    svg = Scorecard(lanes=[lane]).html()
    assert "inf" not in svg.lower() and "nan" not in svg.lower()
    assert svg.count("lane-mk") == 1, "the diverged unit must not be plotted"
    assert "0.108" in svg, "the finite unit still is, and the axis is scaled to it"


def test_an_empty_table_says_so_instead_of_rendering_a_headerless_table():
    assert "nothing to show" in UnitsTable(rows=[]).md()
    assert "nothing to show" in UnitsTable(rows=[]).html()


def test_a_ticked_stub_shows_which_box_and_markdown_round_trips_it():
    stub = Stub(options=[("go", ""), ("stop", "")], ticked=["stop"])
    assert "- [x] **stop**" in stub.md() and "- [ ] **go**" in stub.md()
    assert 'class="box on"' in stub.html()


def test_a_lane_with_no_bar_reads_as_report_not_as_a_failure():
    assert "report" in Scorecard(lanes=[Lane("loss", "report", None, [Mark(1.0, "a")])]).md()


def test_a_gallery_links_in_markdown_and_embeds_in_html():
    """Forty inlined images is not a markdown file anyone opens twice."""
    g = Gallery(items=[("var5 seed 0", "var5/seed0/strip_0.png")])
    assert "](var5/seed0/strip_0.png)" in g.md() and "<img" not in g.md()
    assert "<img" in g.html()


def test_the_footer_names_the_command_that_rebuilds_the_page():
    f = Footer(text="x", command="python -m rl_researcher.report study5")
    assert "python -m rl_researcher.report study5" in f.md()
    assert "<code>" in f.html()
