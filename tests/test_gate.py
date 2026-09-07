"""The cost gate: what is ungated, what is refused, and what an approval binds to.

The gate exists so a human is in the loop before a large amount of time, money or effort is
committed, and only then. Two failures matter: refusing small work (which would make the
system useless) and letting a night of compute start unasked.
"""


import pytest

pytestmark = pytest.mark.tier1

from rl_researcher.cost import append_throughput, estimate, probe_device  # noqa: E402
from rl_researcher.gate import (GateRefused, assess, decide, enforce, find_approval,  # noqa: E402
                                write_approval)
from rl_researcher.kinds import DeviceInfo  # noqa: E402
from rl_researcher.spec import spec_fingerprint  # noqa: E402


def _throughput(config, kind, spec, seconds_per_step, device="local/cpu", n=3):
    for i in range(n):
        append_throughput(config, {"kind": kind.name, "unit_class": kind.unit_class(spec, "ols/seed0"),
                                   "device": device, "seconds_per_step": seconds_per_step,
                                   "seconds_per_unit": seconds_per_step * spec.budget.max_steps,
                                   "steps": spec.budget.max_steps, "run": "earlier", "unit": f"ols/seed{i}"})


def test_with_no_record_the_estimate_is_the_budget_cap_and_says_so(toy):
    config, kind, spec, out = toy
    c = estimate(spec, kind, config, out=out)
    assert c.basis == "budget-cap" and c.units == 6
    # six units x the 60 s cap is six minutes: over the project's five-minute line
    assert assess(c, config).gated


def test_a_measured_device_gives_a_measured_estimate(toy):
    config, kind, spec, out = toy
    device = probe_device(kind, config)
    _throughput(config, kind, spec, 0.001, device=device.fingerprint)
    c = estimate(spec, kind, config, out=out, device=device)
    assert c.basis == "measured" and c.samples == 3
    assert c.wall_seconds == pytest.approx(6 * 0.001 * spec.budget.max_steps, rel=0.01)
    assert not assess(c, config).gated          # well under the line: the LLM just runs it


def test_finished_units_are_not_charged_for_again(toy):
    config, kind, spec, out = toy
    device = probe_device(kind, config)
    _throughput(config, kind, spec, 0.001, device=device.fingerprint)
    before = estimate(spec, kind, config, out=out, device=device).units
    from rl_researcher import atomic
    from rl_researcher.units import unit_dir

    cell = unit_dir(out, "ols/seed0")
    cell.mkdir(parents=True, exist_ok=True)
    atomic.write_json(cell / "results.json", {"steps": 1, "seconds": 1.0})
    assert estimate(spec, kind, config, out=out, device=device).units == before - 1


def test_a_shortened_budget_is_ungated_even_on_a_gated_spec(toy):
    """A smoke run of an expensive spec must not need an approval; that is exactly the
    'modest extra compute' the gate is not for."""
    config, kind, spec, out = toy
    assert assess(estimate(spec, kind, config, out=out), config).gated
    small = assess(estimate(spec, kind, config, out=out, max_seconds=5), config)
    assert not small.gated and small.wall_seconds == pytest.approx(30)


def test_money_over_the_line_gates_even_when_it_is_fast(toy):
    config, kind, spec, out = toy
    device = DeviceInfo(name="Tesla T4", kind="cloud", fingerprint="colab/T4", hourly_usd=0.35, known=True)
    _throughput(config, kind, spec, 0.05, device=device.fingerprint)
    c = assess(estimate(spec, kind, config, out=out, device=device), config)
    assert c.gated and any("over the $" in r for r in c.reasons)


def test_an_always_gated_device_kind_is_gated_however_cheap(toy):
    config, kind, spec, out = toy
    device = DeviceInfo(name="free-tier", kind="cloud", fingerprint="x/free", hourly_usd=0.0, known=True)
    _throughput(config, kind, spec, 0.0001, device=device.fingerprint)
    c = assess(estimate(spec, kind, config, out=out, device=device), config)
    assert c.gated and any("always gated" in r for r in c.reasons)


def test_a_gated_run_is_refused_until_the_human_says_yes(toy):
    config, kind, spec, out = toy
    with pytest.raises(GateRefused) as exc:
        enforce(spec, kind, out, max_steps=None, max_seconds=None, config=config, device=None)
    assert "over the gate line" in str(exc.value) and "rl_researcher.approve" in str(exc.value)

    d = decide(spec, kind, config, out=out)
    path = write_approval(config, spec, d.cost, quote="yes, run it overnight", session="s1")
    assert "yes, run it overnight" in path.read_text(encoding="utf-8")
    enforce(spec, kind, out, max_steps=None, max_seconds=None, config=config, device=None)


def test_editing_the_spec_voids_the_approval(toy, project):
    """The approval is bound to the fingerprint, so a bar loosened after approval needs a new
    yes without any further rule."""
    config, kind, spec, out = toy
    write_approval(config, spec, decide(spec, kind, config, out=out).cost, quote="yes")
    spec_path = project / "studies" / "toy-line-fit.toml"
    spec_path.write_text(spec_path.read_text(encoding="utf-8").replace("bar = 0.1", "bar = 0.9"),
                         encoding="utf-8")
    edited = kind.load(spec_path)
    assert spec_fingerprint(edited) != spec_fingerprint(spec)
    assert find_approval(config, edited) is None
    with pytest.raises(GateRefused):
        enforce(edited, kind, out, max_steps=None, max_seconds=None, config=config, device=None)


def test_an_approval_records_what_the_human_said_and_the_estimate(toy):
    config, kind, spec, out = toy
    d = decide(spec, kind, config, out=out)
    text = write_approval(config, spec, d.cost, quote='he said "go ahead"', session="abc",
                          note="overnight").read_text(encoding="utf-8")
    for expect in ("approved_by", 'go ahead', "abc", "[estimate]", "wall_seconds", "git_sha"):
        assert expect in text


def test_nothing_left_to_run_is_never_gated(toy):
    config, kind, spec, out = toy
    from rl_researcher import atomic
    from rl_researcher.units import unit_dir

    for u in kind.units(spec):
        cell = unit_dir(out, u)
        cell.mkdir(parents=True, exist_ok=True)
        atomic.write_json(cell / "results.json", {"steps": 1, "seconds": 1.0})
    c = assess(estimate(spec, kind, config, out=out), config)
    assert c.basis == "nothing-to-run" and not c.gated
