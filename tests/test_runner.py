"""The run loop, on the toy kind: idempotence, resume, failure, split, status.

Everything here is a property the plan calls load-bearing: a run is never silent, survives a
shutdown, refuses to interleave with itself, and reports a failure where a monitor looks.
"""


import pytest

pytestmark = pytest.mark.tier1

from rl_researcher.runner import (CheckFailed, GuardBlocked,  # noqa: E402
                                  missing_units, run)
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

    # One seed, and `screening` says so — C05 refuses a single seed that does not
    # declare itself, because one seed read as a difference is how a study comes to
    # say nothing.
    make_project(project, max_steps=200, seeds=(0,),
                 extra="screening = true\nstop_at_step = 60\n")
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


def test_a_units_last_heartbeat_says_how_it_actually_ended(project):
    """It always said "done", so a unit that hit the time cap before its step budget was
    recorded as having finished and only the result file said otherwise. A dashboard and a
    watcher both read the heartbeat."""
    import json

    from rl_researcher.run import main as run_main
    from rl_researcher.units import unit_dir

    assert run_main(["studies/toy-line-fit.toml", "--max-seconds", "0"]) == 0
    out = project / "docs" / "toy" / "toy-line-fit"
    for unit in ("ols/seed0", "noisy/seed0"):
        cell = unit_dir(out, unit)
        beat = json.loads((cell / "progress.json").read_text(encoding="utf-8"))
        result = json.loads((cell / "results.json").read_text(encoding="utf-8"))
        assert result["status"] == "incomplete", "a zero time cap stops before the step budget"
        assert beat["status"] == "incomplete", beat["status"]


def _finding(level, message="the snapshot digest is not the one the spec registers"):
    from rl_researcher.kinds import Finding

    return lambda self, s, c=None: [Finding("manifest", level, message)]


def test_an_error_finding_refuses_the_run_before_anything_is_prepared(toy, monkeypatch):
    """A check that only fires when someone remembers to run the check command is not a check.

    The cost of getting this wrong is the whole point: a loop scored against a model trained on
    differently-shaped observations produces numbers that look fine and mean nothing, and it
    costs an engine window to produce them.
    """
    config, kind, spec, out = toy
    monkeypatch.setattr(type(kind), "check", _finding("error"))
    prepared = []
    monkeypatch.setattr(type(kind), "prepare", lambda self, s, ctx: prepared.append(1))
    with pytest.raises(CheckFailed, match="the snapshot digest"):
        run(spec, kind, out, config=config)
    assert not prepared, "prepare ran after a check said the registration was wrong"
    assert missing_units(kind, spec, out) == kind.units(spec), "a refused run left a result behind"
    assert not (out / ".study-lock.json").exists()


def test_a_refused_run_is_logged_as_a_refusal_and_not_as_a_failure(toy, monkeypatch):
    config, kind, spec, out = toy
    monkeypatch.setattr(type(kind), "check", _finding("error"))
    lines = []
    with pytest.raises(CheckFailed):
        run(spec, kind, out, config=config, log=lines.append)
    text = "\n".join(lines)
    assert "check [manifest] error:" in text and "the snapshot digest" in text
    assert "FAILED" not in text and "Traceback" not in text


def test_a_warning_is_printed_and_the_run_goes_ahead(toy, monkeypatch):
    config, kind, spec, out = toy
    monkeypatch.setattr(type(kind), "check", _finding("warn", "no reference line is set"))
    lines = []
    summary = run(spec, kind, out, config=config, log=lines.append)
    assert len(summary["runs"]) == 6
    assert "check [manifest] warn: no reference line is set" in "\n".join(lines)


def test_a_waived_check_says_so_in_the_run_s_own_log(toy, monkeypatch):
    """The numbers this run produces stand on a registration something objected to, so the
    objection has to live where the numbers do, not only in the terminal that launched it."""
    config, kind, spec, out = toy
    monkeypatch.setattr(type(kind), "check", _finding("error"))
    summary = run(spec, kind, out, config=config, skip_checks=True)
    assert len(summary["runs"]) == 6
    log = (out / "run.log").read_text(encoding="utf-8")
    assert "waived 1 check error(s) with --no-check" in log
    assert "check [manifest] error: the snapshot digest" in log


def test_run_and_check_ask_the_kind_the_same_questions(toy, monkeypatch):
    """Two callers, one collector. A check present in the report and absent from the launch is
    how an engine boots against a snapshot the check command would have refused."""
    from rl_researcher.findings import collect

    config, kind, spec, out = toy
    asked = []
    monkeypatch.setattr(type(kind), "check",
                        lambda self, s, c=None: asked.append("check") or [])
    collect(kind, spec, config)          # what `python -m rl_researcher.check` calls
    run(spec, kind, out, config=config)  # and what the run path calls
    assert asked == ["check", "check"]


def test_a_finished_run_rebuilds_its_summary_without_preparing_again(toy, monkeypatch):
    """Re-running a finished run is a request to rebuild the summary from what is on disk.

    Preparing is what costs — a snapshot read into memory, a model built, for Auto-SM64's engine
    loop an engine binary demanded. Doing it for zero units means a summary cannot be rebuilt on
    a machine that could not have produced the results in the first place.
    """
    config, kind, spec, out = toy
    run(spec, kind, out, config=config)
    calls = []
    real = type(kind).prepare
    monkeypatch.setattr(type(kind), "prepare",
                        lambda self, s, ctx: calls.append(1) or real(self, s, ctx))
    lines = []
    summary = run(spec, kind, out, config=config, log=lines.append)
    assert not calls, "prepared a run with nothing left to run"
    assert len(summary["runs"]) == 6 and not summary["missing_units"]
    assert "rebuilding the summary only" in "\n".join(lines)

    # ...and one unit short of finished still prepares.
    (unit_dir(out, "ols/seed0") / "results.json").unlink()
    run(spec, kind, out, config=config)
    assert calls == [1]


def test_a_run_stage_check_is_asked_at_the_launch_it_is_named_for(toy, monkeypatch):
    """`run` asked only the `check` stage, so the two checks registered at `run` -- C06 and
    C09, the one that says the canary still passes before an expensive run -- were asked by
    `lint` and by the git hook and never at a launch, which is the moment they are named for.
    """
    from rl_researcher.checks import REGISTRY, Check
    from rl_researcher.kinds import Finding

    seen = {}

    def ask(ctx):
        seen["out"] = ctx.out
        seen["root"] = ctx.root
        return [Finding("CXX", "warn", "asked at the launch")]

    monkeypatch.setitem(REGISTRY, "CXX",
                        Check(id="CXX", stage="run", lesson="L000", what="a test", fn=ask))
    config, kind, spec, out = toy
    lines = []
    run(spec, kind, out, config=config, log=lines.append, max_seconds=0)

    assert seen, "a run-stage check was never asked by the runner"
    assert seen["out"] == out, "asked without the run's own output directory"
    assert seen["root"] is not None, "asked without the project root"
    assert any("asked at the launch" in ln for ln in lines)


def test_every_way_past_the_human_is_written_into_the_run_log(toy, monkeypatch):
    """`--no-check` logged its waiver; the two flags that skip the *human* did not.

    A gate walked past and a guard waived leave the same numbers on disk as a run that cleared
    both. The log is the only place that can say which happened.
    """
    from rl_researcher.kinds import Guard

    config, kind, spec, out = toy
    monkeypatch.setattr(type(kind), "guards", lambda self, s: [
        Guard(name="engine", is_blocked=lambda: True, message="the engine holds the GPU")])

    lines = []
    run(spec, kind, out, config=config, log=lines.append, allow_guards=True, max_seconds=0)
    text = "\n".join(lines)
    assert "no cost gate" in text, "a run with no gate said nothing about it"
    assert "engine" in text and "the engine holds the GPU" in text, \
        "a blocked guard was waived silently"
