"""The inline SVG charts: tracks, markers, curves and the legend that explains them.

These are the pictures a reader decides from, and every one of them is a place a wrong number
can look right. A cell dropped instead of marked, three seeds drawn as one marker, an axis set
by a diverged rollout, a target line with no scale beside it -- each renders perfectly and each
misleads.

Moved here from `Auto-SM64/python/tests/study/test_dashboard.py`, where they had been the only
coverage `charts.py` had anywhere: a consuming project was testing the package's code, so the
package could be changed without anything in its own repository objecting. The assertions and
the names are unchanged, because every one of them was written against a real misreading.
"""

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from rl_researcher import charts  # noqa: E402


def test_a_track_draws_one_marker_per_cell_and_a_rule_at_the_target():
    values = [("conv_ae", 0.5 + s, f"conv_ae seed {s}") for s in (0, 1, 2)]
    out = charts.track(values, 1.0, "higher", ["conv_ae", "jepa"])
    assert out.count('class="mk"') == 3          # every cell placed
    assert out.count('class="bar"') == 1         # the target rule
    assert 'class="pass"' in out                 # and the side of it that passes


def test_a_metric_that_did_not_compute_is_marked_not_dropped():
    out = charts.track([("conv_ae", float("nan"), "conv_ae seed 0: not computed")],
                       1.0, "higher", ["conv_ae"])
    assert 'class="mk nan"' in out and "not computed" in out


def test_a_sparkline_needs_two_points_and_can_show_the_floor():
    assert charts.sparkline([], colour="#000") == ""
    assert charts.sparkline([0.1], colour="#000") == ""
    line = charts.sparkline([0.01, 0.03, 0.05], colour="#000", floor=0.1, label="rank")
    assert "<polyline" in line and 'class="floor"' in line and "rank" in line


def test_every_variant_gets_its_own_marker_shape():
    """Colour alone cannot carry identity: plotstyle gives conv_ae and conv_ae_inv two shades
    of one blue, and a reader without colour vision gets nothing from either."""
    order = ["conv_ae", "conv_ae_inv", "jepa", "jepa_inv"]
    shapes = [charts.shape_of(v, order) for v in order]
    assert len(set(shapes)) == len(order)
    assert charts.shape_of("conv_ae", order) == charts.shape_of("conv_ae", order)  # stable
    for shape in shapes:
        assert charts.marker(shape, 10, 10, 4, fill="#123456").startswith("<")


def test_the_legend_names_each_variant_and_how_many_of_it_have_finished():
    order = ["conv_ae", "jepa_inv"]
    html_ = charts.legend(order, {"conv_ae": (3, 3), "jepa_inv": (0, 3)})
    assert "conv_ae" in html_ and "<i>3/3</i>" in html_
    assert "jepa_inv" in html_ and "<i>0/3</i>" in html_   # nothing plotted for it yet, and it says so
    assert "shaded side passes" in html_                   # and the red rule is explained
    assert "not computed" in html_                         # ... and so is the hollow marker
    assert "rollout diverged" in html_                     # ... and so is the cross
    assert "seed 0, 1, 2" in html_                         # ... and what the fill means
    # a glyph per variant, the target key, the n/a key, the diverged key, and three seed dots
    assert html_.count("<svg") == 8
    assert charts.legend([], {}) == ""


def test_markers_that_land_together_are_dodged_apart():
    """Three seeds at the same value would otherwise be one marker."""
    same = [("conv_ae", 0.5, "a"), ("conv_ae", 0.5, "b"), ("conv_ae", 0.5, "c")]
    out = charts.track(same, 1.0, "higher", ["conv_ae"])
    assert out.count('class="mk"') == 3
    assert "margin-top:0px" in out and out.count("margin-top:0px") == 1   # only the first sits centred
    apart = charts.track([("conv_ae", 0.1, "a"), ("conv_ae", 0.9, "b")], None, "report", ["conv_ae"])
    assert apart.count("margin-top:0px") == 2                             # no clash, no dodge


def test_the_track_keeps_its_markers_undistorted():
    """The background stretches with the column; the markers must not, or a circle becomes an
    ellipse and a triangle a wedge."""
    out = charts.track([("conv_ae", 0.5, "x")], 1.0, "higher", ["conv_ae"])
    assert 'preserveAspectRatio="none"' in out          # the background does stretch
    assert 'class="mk"' in out and "left:" in out       # markers are positioned, not scaled
    assert "non-scaling-stroke" in out                  # and the rules stay hairlines


def test_targets_are_written_as_one_word_with_a_direction():
    assert charts.target_label(1.0, "higher") == "Target ≥ 1"
    assert charts.target_label(0.7, "lower") == "Target ≤ 0.7"
    assert charts.target_label(None, "report") == ""


def test_a_dodged_marker_never_leaves_its_lane():
    """An unbounded ladder put the third colliding marker outside its own row."""
    offsets = charts._dodge([1.0] * 8)
    assert max(abs(o) for o in offsets) <= charts.DODGE_MAX
    assert charts.DODGE_MAX < charts.TRACK_H / 2      # still inside the lane it belongs to
    assert offsets[0] == 0.0                          # the first sits on the axis
    assert len(set(offsets[:3])) == 3                 # and the ones after it separate


def test_a_track_states_its_scale():
    """Without endpoints a marker's position means nothing; they come from the same _scale
    that places the markers, so a label cannot disagree with a position."""
    out = charts.track([("conv_ae", 0.5, "conv_ae seed 0: 0.5")], 1.0, "higher", ["conv_ae"],
                       label="Action sensitivity")
    lo, hi, _ = charts._scale([0.5], 1.0)
    assert f'class="end lo">{charts.fmt(lo)}<' in out
    assert f'class="end hi">{charts.fmt(hi)}<' in out


def test_an_axis_over_non_negative_data_never_goes_below_zero():
    """A ratio cannot be negative, so an axis claiming -0.09 lies about the quantity. It is
    clamped rather than anchored at zero: forcing the origin would squash a cluster like
    0.79 to 0.85 into a point."""
    lo, _, _ = charts._scale([0.03, 0.5], None)       # padding would have gone negative
    assert lo == 0.0
    lo, hi, _ = charts._scale([0.79, 0.85], 0.7)      # a tight cluster keeps its room
    assert lo > 0.6 and hi < 1.0
    lo_neg, _, _ = charts._scale([-0.4, 0.2], None)   # genuinely negative data still gets room
    assert lo_neg < -0.4


def test_every_track_can_be_read_without_seeing_it():
    out = charts.track([("conv_ae", 0.5, "conv_ae seed 0: 0.5")], 1.0, "higher", ["conv_ae"],
                       label="Action sensitivity")
    aria = out.split('aria-label="')[1].split('"')[0]
    assert "Action sensitivity" in aria and "Target" in aria and "Axis" in aria
    assert "conv_ae seed 0: 0.5" in aria
    empty = charts.track([], 1.0, "higher", ["conv_ae"], label="Action sensitivity")
    assert "no cell has finished" in empty


def test_a_curve_names_both_of_its_axes():
    out = charts.curve([3.0, 2.0, 1.0], colour="#123456", floor=0.1,
                       label="Participation ratio", steps=10000)
    assert "<i>0</i>" in out and "<i>10,000</i>" in out and "step" in out   # x axis
    assert 'class="yax"' in out and "3" in out    # y axis, by its end values
    assert "target 0.1" in out                    # and the collapse target is named
    assert "Participation ratio" in out           # spoken form for a screen reader


def test_seeds_of_one_variant_differ_in_fill_but_not_in_shape_or_colour():
    a = charts.marker("square", 6, 6, 4, fill="#2B4FBF", seed=0)
    b = charts.marker("square", 6, 6, 4, fill="#2B4FBF", seed=1)
    c = charts.marker("square", 6, 6, 4, fill="#2B4FBF", seed=2)
    assert a != b != c and a != c
    assert a.startswith("<rect") and b.startswith("<rect")     # same shape
    assert "#2B4FBF" in a and "#2B4FBF" in b                   # same colour
    # A hatch, not a fade: a faded marker reads as "less", when it only means "another seed".
    assert "fill-opacity" not in b and "url(#hx-diag-2B4FBF)" in b
    assert 'fill="none"' in c and 'stroke="#2B4FBF"' in c      # the third seed is outlined
    defs = charts.seed_defs([("#2B4FBF", 0), ("#2B4FBF", 1), ("#2B4FBF", 2), ("#2B4FBF", 1)])
    assert defs.count("<pattern") == 1        # only the hatch needs one, and not once per use


def test_a_curve_labels_both_ends_of_both_axes_and_names_the_x_one():
    svg = charts.curve([0.02, 0.05, 0.09], colour="#000", steps=10000, label="participation")
    assert 'class="cax"' in svg
    # y: each end pinned to the value it names, so the CSS can centre it on that value.
    assert 'class="hi">0.09<' in svg and 'class="lo">0.02<' in svg
    # x: both ends, and the name -- nothing else on the page says this axis is steps.
    assert "<i>0</i>" in svg and "<i>10,000</i>" in svg and 'class="xname">step<' in svg


def test_a_curve_keeps_its_target_label_inside_the_box():
    svg = charts.curve([0.02, 0.05, 0.09], colour="#000", floor=0.1, label="participation")
    # The target sits at the very top here, so the label has to flip below its line.
    ys = [float(t.split('y="')[1].split('"')[0])
          for t in svg.split("<text") if "floorlbl" in t]
    assert ys and all(0.0 <= y <= 38.0 for y in ys), ys


def test_the_scale_ignores_a_diverged_value():
    """It used to set the axis: 9.2e19 in the set made every real mark share one pixel."""
    assert charts.is_log([0.64, float("inf"), 0.66], 0.7) is False
    lo, hi, log = charts._scale([0.64, float("inf"), 0.66], 0.7)
    assert hi < 1.0 and not log


def test_a_metric_spanning_decades_switches_to_a_log_axis():
    """A 30x range stays linear, which reads fine, and only past two decades does it switch.

    The half of this that lived with the dashboard asserted that the page said "log scale";
    what belongs here is the rule the page is reporting.
    """
    assert charts.is_log([0.05, 12.05], 0.7) is True
    assert charts.is_log([0.4, 12.05], 0.7) is False


def test_finished_coverage_curves_are_overlaid_and_a_curve_can_name_its_axis():
    """Comparing arms needs several series on one scale, each carrying its own x.

    Moved from `Auto-SM64/python/tests/study/test_loop_dashboard.py`, where it was the only
    coverage anywhere of `multi_curve` and of `curve(xname=...)`.
    """
    svg = charts.multi_curve([("planner", [(10, 3), (500, 60), (1000, 100)], "#2B4FBF"),
                              ("random", [(10, 2), (1000, 70)], "#888888")])
    assert svg.count("<polyline") == 2
    assert ">decision<" in svg
    # An arm with one point is not a curve; drawing it flat at zero would read as a result.
    assert charts.multi_curve([("planner", [(1, 1)], "#000")]) == ""
    assert charts.multi_curve([]) == ""
    assert ">decision<" in charts.curve([1.0, 2.0, 3.0], colour="#000", xname="decision")
    assert ">step<" in charts.curve([1.0, 2.0, 3.0], colour="#000")


def test_overlaid_series_share_one_scale_and_each_is_named():
    """Two arms drawn against different axes are not a comparison, and an unnamed line in a
    two-line picture is a line the reader has to guess at."""
    series = [("planner", [(0, 1.0), (1, 2.0), (2, 3.0)], "#123456"),
              ("random", [(0, 10.0), (1, 20.0), (2, 30.0)], "#654321")]
    out = charts.multi_curve(series, xname="decision", label="Distinct cells")
    assert "planner" in out and "random" in out
    assert "Distinct cells" in out
    assert "30" in out, "the shared axis does not reach the largest series"
