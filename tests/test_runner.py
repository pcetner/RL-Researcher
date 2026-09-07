"""The run loop, on the toy kind: idempotence, resume, failure, split, status.

Everything here is a property the plan calls load-bearing: a run is never silent, survives a
shutdown, refuses to interleave with itself, and reports a failure where a monitor looks.
"""


import pytest

pytestmark = pytest.mark.tier1

from rl_researcher.runner import GuardBlocked, missing_units, run  # noqa: E402
from rl_researcher.status import print_status, run_status  # noqa: E402
from rl_researcher.stop import HotStop  # noqa: E402
from rl_researcher.units import read_json, unit_dir  # noqa: E402


def _run(toy, **kw):
    config, kind, spec, out = toy
    return run(spec, kind, out, config=config, **kw)


def test_a_run_writes_a_result_a_summary_and_a_log(toy):
    config, kind, spec, out = toy
    summary = _run(toy)
    assert len(summary["runs"]) == 6 and not summary["missing_units"]
    assert summary["fingerprint"] and summary["kind"] == "toy"
    assert (out / "results.json").is_file() and (out / "run.log").is_file()
    r = read_json(unit_dir(out, "ols/seed0") / "results.json")
    assert r["status"] == "complete" and r["metrics"]["slope_error"] < 0.1
    # the log is flushed line by line, so a detached run is readable while it runs
    assert "complete after" in (out / "run.log").read_text(encoding="utf-8")


def test_the_registered_question_is_answered_the_way_the_spec_predicted(toy):
    """The toy exists to be a real study in miniature: the quiet arm clears the slope bar and
    the noisy one does not, so a report has something to say."""
    summary = _run(toy)
    by_arm = {}
    for r in summary["runs"]:
        by_arm.setdefault(r["arm"], []).append(r["metrics"]["slope_error"])
    assert max(by_arm["ols"]) < 0.1 < max(by_arm["noisy"])


def test_rerunning_skips_finished_units(toy):
    config, kind, spec, out = toy
    _run(toy)
    first = read_json(unit_dir(out, "ols/seed0") / "results.json")
    again = _run(toy)
    assert read_json(unit_dir(out, "ols/seed0") / "results.json") == first
    assert len(again["runs"]) == 6


def test_no_resume_starts_every_unit_over(toy):
    config, kind, spec, out = toy
    _run(toy)
    (unit_dir(out, "ols/seed0") / "results.json").unlink()
    _run(toy, resume=False)
    assert read_json(unit_dir(out, "ols/seed0") / "results.json")["resumed_from_step"] is None


def test_a_unit_that_raises_is_marked_where_the_monitor_looks(toy, monkeypatch):
    """A CUDA OOM once reached stderr only and a study looked alive for eight hours."""
    config, kind, spec, out = toy

    def boom(*a, **k):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(type(kind), "run_unit", boom)
    with pytest.raises(RuntimeError):
        _run(toy)
    st = run_status(spec, kind, out)
    assert st.state == "FAILED" and st.bad
    assert "CUDA out of memory" in (out / "run.log").read_text(encoding="utf-8")


def test_status_exits_2_on_a_failure_and_0_otherwise(toy, monkeypatch, capsys):
    config, kind, spec, out = toy
    assert print_status(run_status(spec, kind, out)) == 0        # not started is not a failure
    _run(toy)
    assert print_status(run_status(spec, kind, out)) == 0
    assert run_status(spec, kind, out).state == "finished"
    monkeypatch.setattr(type(kind), "run_unit",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    (unit_dir(out, "ols/seed0") / "results.json").unlink()
    with pytest.raises(RuntimeError):
        _run(toy)
    assert print_status(run_status(spec, kind, out)) == 2


def test_a_hot_stop_leaves_a_resumable_unit_that_continues(project, tmp_path):
    """'If I turn my laptop off, this shouldn't break an entire study.'"""
    from rl_researcher.config import kind_for, load_config, out_dir_for
    from tests.conftest import make_project

    make_project(project, max_steps=200, seeds=(0,), extra="stop_at_step = 60\n")
    config = load_config()
    spec_path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(spec_path, config)
    spec = kind.load(spec_path)
    out = out_dir_for(spec, config)
    with pytest.raises(HotStop):
        run(spec, kind, out, config=config, units=["ols"])
    st = run_status(spec, kind, out)
    stopped = next(u for u in st.units if u.arm == "ols")
    assert stopped.resumable and stopped.checkpoint_step is not None and not stopped.done

    run(spec, kind, out, config=config, units=["ols"])   # the identical command continues it
    r = read_json(unit_dir(out, "ols/seed0") / "results.json")
    assert r["resumed_from_step"] is not None and r["steps"] == 200
    assert not list(unit_dir(out, "ols/seed0").glob("checkpoint.*"))  # cleared once the result exists


def test_a_time_cap_reports_incomplete_rather_than_pretending(toy):
    config, kind, spec, out = toy
    summary = run(spec, kind, out, config=config, max_seconds=0.001)
    assert {r["status"] for r in summary["runs"]} == {"incomplete"}


def test_a_run_can_be_split_across_machines(toy):
    """Each machine writes only its own units; the summary says which are missing."""
    config, kind, spec, out = toy
    summary = run(spec, kind, out, config=config, units=["ols"])
    assert len(summary["runs"]) == 3 and len(summary["missing_units"]) == 3
    assert all(u.startswith("noisy/") for u in summary["missing_units"])
    summary = run(spec, kind, out, config=config, units=["noisy"])
    assert len(summary["runs"]) == 6 and not summary["missing_units"]
    assert not missing_units(kind, spec, out)


def test_an_unknown_unit_is_refused_rather_than_silently_running_nothing(toy):
    config, kind, spec, out = toy
    with pytest.raises(ValueError, match="unknown units"):
        run(spec, kind, out, config=config, units=["typo"])


def test_a_guard_blocks_a_run_until_it_is_overridden(toy, monkeypatch):
    from rl_researcher.kinds import Guard

    config, kind, spec, out = toy
    monkeypatch.setattr(type(kind), "guards",
                        lambda self, s: [Guard("engine", lambda: True, "an engine is running")])
    with pytest.raises(GuardBlocked, match="an engine is running"):
        run(spec, kind, out, config=config)
    run(spec, kind, out, config=config, allow_guards=True)


def test_the_gate_is_asked_before_the_lock_is_taken(toy):
    """A refused run must leave nothing behind, not even a lock."""
    from rl_researcher.gate import GateRefused, enforce

    config, kind, spec, out = toy
    with pytest.raises(GateRefused):
        run(spec, kind, out, config=config, gate=enforce)
    assert not (out / ".study-lock.json").exists()


def test_the_lock_is_released_even_when_a_run_fails(toy, monkeypatch):
    config, kind, spec, out = toy
    monkeypatch.setattr(type(kind), "run_unit",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    with pytest.raises(RuntimeError):
        run(spec, kind, out, config=config)
    assert not (out / ".study-lock.json").exists()


def test_throughput_is_recorded_so_the_next_estimate_is_measured(toy):
    from rl_researcher.cost import estimate, read_throughput

    config, kind, spec, out = toy
    _run(toy)
    rows = read_throughput(config)
    assert len(rows) == 6 and all(r["kind"] == "toy" and r["seconds_per_step"] for r in rows)
    # nothing left to run here, so ask about a fresh output directory
    c = estimate(spec, kind, config, out=out.parent / "elsewhere")
    assert c.basis == "measured" and c.units == 6


def test_a_dashboard_that_throws_never_takes_the_run_down(toy):
    """A page is for reading; a run must not die because one could not be drawn."""
    config, kind, spec, out = toy

    def bad_factory(spec, kind, out, log):
        raise RuntimeError("no renderer")

    summary = run(spec, kind, out, config=config, page_writer=bad_factory)
    assert len(summary["runs"]) == 6
    assert "dashboard disabled" in (out / "run.log").read_text(encoding="utf-8")


def test_the_heartbeat_carries_what_a_dashboard_needs(toy):
    config, kind, spec, out = toy
    seen = []
    real = type(kind).run_unit

    def watching(self, spec, unit, prepared, ctx):
        result = real(self, spec, unit, prepared, ctx)
        seen.append(read_json(ctx.progress))
        return result

    type(kind).run_unit = watching
    try:
        run(spec, kind, out, config=config)
    finally:
        type(kind).run_unit = real
    last = seen[0]
    for key in ("unit", "arm", "seed", "status", "step", "max_steps", "elapsed_seconds", "history", "updated"):
        assert key in last, key
    assert last["history"]["loss"] and len(last["history"]["loss"]) <= 120
