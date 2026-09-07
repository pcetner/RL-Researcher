"""The palette and the matplotlib helpers.

Moved here from `Auto-SM64/python/tests/study/test_dashboard.py`. Like `charts.py`, this module
had no test in its own repository at all.
"""

import math

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from rl_researcher import plotstyle as ps  # noqa: E402


def _rgb(h):
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def test_neighbouring_variants_are_far_apart_in_colour():
    """The palette used to key on architecture, so conv_ae and conv_ae_inv -- the pair a
    two-by-two exists to compare -- came out as two shades of one blue."""
    order = ["conv_ae", "conv_ae_inv", "jepa", "jepa_inv"]
    colours = [ps.variant_color(v, order) for v in order]
    assert len(set(colours)) == len(order)
    assert colours == [ps.variant_color(v, order) for v in order]      # stable

    for a, b in zip(colours, colours[1:]):                            # adjacent pairs especially
        assert sum(abs(x - y) for x, y in zip(_rgb(a), _rgb(b))) > 150, (a, b)


def test_more_arms_than_the_cycle_still_get_distinct_colours():
    """A run with more arms than the palette has entries must not put two of them in the same
    colour: the second time round is shaded, not repeated."""
    order = [f"v{i}" for i in range(len(ps.VARIANT_CYCLE) + 3)]
    colours = [ps.variant_color(v, order) for v in order]
    assert len(set(colours)) == len(order)


def test_shade_moves_toward_white_and_black_and_stays_a_colour():
    base = "#2B4FBF"
    lighter, darker = ps.shade(base, 0.4), ps.shade(base, -0.4)
    assert sum(_rgb(lighter)) > sum(_rgb(base)) > sum(_rgb(darker))
    for out in (lighter, darker):
        assert out.startswith("#") and len(out) == 7
        assert all(0 <= c <= 255 for c in _rgb(out))
    assert ps.shade(base, 0.0) == base


def test_a_smoothed_series_keeps_its_length_and_carries_nan_through():
    """A smoother that silently drops points shortens the x axis under the line."""
    ys = [1.0, 2.0, 3.0, 4.0, 5.0] * 8
    out = ps.smooth(ys)
    assert len(out) == len(ys)
    assert min(ys) <= min(out) and max(out) <= max(ys)   # a mean cannot leave the range
    with_gap = ps.smooth([1.0, float("nan"), 3.0] * 10)
    assert len(with_gap) == 30

    e = ps.ema([1.0, float("nan"), 3.0], alpha=0.5)
    assert len(e) == 3 and math.isfinite(e[0])


def test_labels_are_nudged_apart_without_being_reordered():
    """Two series ending at almost the same value would otherwise print one label over the
    other; reordering them would put each series' name against the other's line."""
    ys = [0.10, 0.101, 0.102]
    out = ps.spread(ys, 0.02)
    assert out == sorted(out), "the labels swapped places"
    for a, b in zip(out, out[1:]):
        assert b - a >= 0.02 - 1e-9
    assert ps.spread([0.1], 0.02) == [0.1]


def test_the_two_formatters_in_this_package_agree_about_a_value_that_is_not_one():
    """`plotstyle.fmt` labels a figure's axis and `charts.fmt` labels a track's; a reader sees
    both on one page. `plotstyle` printed a diverged value as `inf` while `charts` called it
    `diverged`, which is two names for one event and leaves the reader to work out that they
    are the same one."""
    from rl_researcher import charts

    for bad in (float("nan"), float("inf"), float("-inf")):
        assert ps.fmt(bad) == charts.fmt(bad), bad
    assert ps.fmt(float("nan")) == "n/a" and ps.fmt(float("inf")) == "diverged"


def test_numbers_are_formatted_by_how_big_they_are():
    """Three decimals up close, one in the tens, none past a hundred, scientific below 0.01 --
    so a column of them lines up and none of them is all zeroes."""
    assert ps.fmt(0.0) == "0.000"
    assert ps.fmt(1.5) == "1.500"
    assert ps.fmt(12.345) == "12.3"
    assert ps.fmt(1234.0) == "1234"
    assert ps.fmt(0.00123) == "1.2e-03", "three decimals would have printed this as 0.001"
