"""What a spec must refuse, and what its fingerprint must notice.

The fingerprint is what binds a checkpoint and an approval to a registration. If it moved when
an unrelated default changed, every approval would expire for no reason; if it did not move
when a bar changed, a run could be approved cheaply and then quietly re-registered.
"""

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher.spec import (CadenceSpec, MetricRegistry, MetricSpec, RunSpec, SpecError,  # noqa: E402
                                run_spec_from_dict, spec_fingerprint)

REGISTRY = MetricRegistry(known={"slope_error": "how far off the slope is", "r2": "variance explained",
                                 "loss": "training loss"})


def _d(**over):
    d = {"name": "toy", "kind": "toy", "hypothesis": "a line is a line", "seeds": [0, 1, 2],
         "metrics": [{"name": "slope_error", "direction": "lower", "bar": 0.1},
                     {"name": "r2", "direction": "higher", "bar": 0.9}],
         "budget": {"max_steps": 100, "max_seconds": 60},
         "arms": [{"name": "ols"}]}
    d.update(over)
    return d


def test_a_metric_the_project_cannot_measure_is_refused():
    with pytest.raises(SpecError, match="unknown metric"):
        run_spec_from_dict(_d(metrics=[{"name": "vibes", "direction": "higher", "bar": 1.0}]), registry=REGISTRY)


def test_a_run_without_a_hypothesis_is_refused():
    """Pre-registration is the point; a spec with no claim is not a run."""
    with pytest.raises(SpecError, match="hypothesis"):
        run_spec_from_dict(_d(hypothesis="  "), registry=REGISTRY)


def test_a_report_metric_may_not_carry_a_bar():
    with pytest.raises(SpecError, match="no bar"):
        run_spec_from_dict(_d(metrics=[{"name": "loss", "direction": "report", "bar": 0.5}]), registry=REGISTRY)


def test_a_bar_and_a_comparison_are_two_different_tests():
    with pytest.raises(SpecError, match="register one of them"):
        run_spec_from_dict(_d(metrics=[{"name": "r2", "direction": "higher", "bar": 0.9,
                                        "compare_to": "ols"}]), registry=REGISTRY)


def test_a_conjunction_must_name_registered_metrics_with_bars():
    with pytest.raises(SpecError, match="conjunction"):
        run_spec_from_dict(_d(conjunction=["loss"], metrics=[
            {"name": "loss", "direction": "report"}]), registry=REGISTRY)


def test_duplicate_metric_names_are_refused():
    with pytest.raises(SpecError, match="unique"):
        run_spec_from_dict(_d(metrics=[{"name": "r2", "direction": "higher", "bar": 0.9},
                                       {"name": "r2", "direction": "report"}]), registry=REGISTRY)


@pytest.mark.parametrize("cadence,match", [
    ({"heartbeat_seconds": 600}, "at least every five minutes"),
    ({"heartbeat_seconds": 0}, "at least every five minutes"),
    ({"checkpoint_every_steps": 0}, "resumable after a shutdown"),
])
def test_a_run_cannot_be_silent_or_unresumable(cadence, match):
    """Both cadences are hard rules: a run that is quiet for more than five minutes cannot be
    told from a hung one, and a run that cannot resume loses a night to a shutdown."""
    with pytest.raises(SpecError, match=match):
        run_spec_from_dict(_d(cadence=cadence), registry=REGISTRY)


def test_the_kinds_own_tables_survive_into_extra():
    spec = run_spec_from_dict(_d(model={"cell": "a/b.pt"}), registry=REGISTRY)
    assert spec.extra["arms"] == [{"name": "ols"}] and spec.extra["model"]["cell"] == "a/b.pt"


def test_the_first_metric_with_a_bar_is_the_primary():
    spec = run_spec_from_dict(_d(metrics=[{"name": "loss", "direction": "report"},
                                          {"name": "r2", "direction": "higher", "bar": 0.9}]), registry=REGISTRY)
    assert spec.primary is not None and spec.primary.name == "r2"


def test_a_changed_bar_is_a_different_registration():
    """An approval and a checkpoint both carry the fingerprint, so this is what stops a run
    being approved cheaply and then re-registered against an easier bar."""
    a = run_spec_from_dict(_d(), registry=REGISTRY)
    b = run_spec_from_dict(_d(metrics=[{"name": "slope_error", "direction": "lower", "bar": 0.5},
                                       {"name": "r2", "direction": "higher", "bar": 0.9}]), registry=REGISTRY)
    assert spec_fingerprint(a) != spec_fingerprint(b)


def test_a_knob_left_at_its_default_does_not_change_the_fingerprint():
    """A default added to the code later must not expire every existing approval."""
    a = run_spec_from_dict(_d(), registry=REGISTRY)
    b = run_spec_from_dict(_d(screening=False, cadence={"heartbeat_seconds": 60.0}), registry=REGISTRY)
    assert spec_fingerprint(a) == spec_fingerprint(b)


def test_where_the_spec_was_loaded_from_is_not_part_of_its_identity():
    a = RunSpec(name="x", kind="toy", hypothesis="h", seeds=[0],
                metrics=[MetricSpec("r2", direction="higher", bar=0.9)], cadence=CadenceSpec(),
                source_path="one/place.toml")
    b = RunSpec(name="x", kind="toy", hypothesis="h", seeds=[0],
                metrics=[MetricSpec("r2", direction="higher", bar=0.9)], cadence=CadenceSpec(),
                source_path="another/place.toml")
    assert spec_fingerprint(a) == spec_fingerprint(b)
