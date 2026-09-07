"""The watcher: what it notices, what it does about it, and what it refuses to do.

Its whole reason to exist is a run that ended at three in the morning with nobody watching, so
the properties that matter are the ones that hold when no session is open: the report and the
ledger rows exist without anyone asking, the log line is written whether or not a toast
appears, and a kill and a restart pick up from disk rather than from memory.

The one thing it must not do is start anything. That is a decision, and a decision needs a
person -- so there is a test that no code path here runs a unit.
"""

import json

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

import time  # noqa: E402

from rl_researcher import notify, watcher  # noqa: E402
from rl_researcher.config import load_config  # noqa: E402
from rl_researcher.units import unit_dir  # noqa: E402


#: The real one, captured before the autouse fixture replaces it, for the one test that is
#: about how a toast fails.
_real_toast = notify.toast


@pytest.fixture(autouse=True)
def no_toast(monkeypatch):
    """Never raise a real toast from a test. The log line is the channel under test."""
    calls = []
    monkeypatch.setattr(notify, "toast", lambda *a, **k: calls.append(a) or "stubbed")
    return calls


def _kind_and_spec(project):
    from rl_researcher.config import kind_for, out_dir_for

    config = load_config()
    path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(path, config)
    spec = kind.load(path)
    return config, kind, spec, out_dir_for(spec, config)


def _finish(out, unit, **over):
    arm, seed = unit.split("/seed")
    d = unit_dir(out, unit)
    d.mkdir(parents=True, exist_ok=True)
    payload = {"arm": arm, "seed": int(seed), "status": "complete", "steps": 200,
               "max_steps": 200, "seconds": 4.0,
               "metrics": {"slope_error": 0.02, "r2": 0.99}}
    payload.update(over)
    (d / "results.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def _beat(out, unit, **over):
    d = unit_dir(out, unit)
    d.mkdir(parents=True, exist_ok=True)
    payload = {"status": "running", "step": 100, "max_steps": 200, "rate": 25.0,
               "elapsed_seconds": 4.0,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}
    payload.update(over)
    (d / "progress.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def _finish_run(project):
    """Every unit done and a summary written: what a finished run looks like on disk."""
    config, kind, spec, out = _kind_and_spec(project)
    for unit in kind.units(spec):
        _finish(out, unit)
    (out / "results.json").write_text(json.dumps(
        {"run": spec.name, "kind": kind.name, "git_sha": "0" * 40, "wall_seconds": 24.0,
         "runs": [json.loads((unit_dir(out, u) / "results.json").read_text(encoding="utf-8"))
                  for u in kind.units(spec)]}), encoding="utf-8")
    return config, kind, spec, out


def test_a_run_that_finished_with_nobody_watching_gets_its_report_and_its_rows(project):
    """The point of the whole thing. Nothing here is called by hand."""
    config, kind, spec, out = _finish_run(project)
    found = watcher.tick(config)
    assert [c.what for c in found] == ["finished"]
    assert (out / "README.md").is_file() and (out / "report.html").is_file()
    rows = (config.path("ledger") / "findings.jsonl")
    assert rows.is_file() and rows.read_text(encoding="utf-8").strip()
    assert (config.path("state") / "STATE.md").is_file()
    assert (config.path("state") / "index.html").is_file()
    assert any("report ->" in d for d in found[0].did)


def test_it_reports_a_finish_once_and_not_on_every_tick_after(project):
    config, kind, spec, out = _finish_run(project)
    assert len(watcher.tick(config)) == 1
    assert watcher.tick(config) == []
    assert watcher.tick(config) == []


def test_a_failure_is_reported_with_its_reason_and_nothing_is_written(project):
    config, kind, spec, out = _kind_and_spec(project)
    _beat(out, "ols/seed0", status="failed", error="RuntimeError: CUDA out of memory\nat line 9")
    found = watcher.tick(config)
    assert [c.what for c in found] == ["failed"]
    assert "CUDA out of memory" in found[0].detail
    assert "at line 9" not in found[0].detail          # the first line, not the traceback
    assert found[0].did == []                          # a failure is not something to act on
    assert not (out / "README.md").exists()


def test_a_run_that_stopped_talking_is_reported_once(project):
    """A stale heartbeat is a process that is probably gone, and nothing else will say so."""
    config, kind, spec, out = _kind_and_spec(project)
    _beat(out, "ols/seed0", updated="2020-01-01T00:00:00+00:00")
    found = watcher.tick(config)
    assert [c.what for c in found] == ["stale"]
    assert "no heartbeat for" in found[0].detail
    assert watcher.tick(config) == []                  # told once, not once a minute


def test_a_run_past_its_own_projection_is_reported_against_the_estimate_it_made(project):
    """A run that keeps revising its estimate upward is exactly the run that never trips a
    rolling threshold, so the projection is the one it made when it started."""
    config, kind, spec, out = _kind_and_spec(project)
    _finish(out, "ols/seed0")
    _beat(out, "ols/seed1")
    now = {"t": 1_000_000.0}
    clock = lambda: now["t"]                                            # noqa: E731

    first = watcher.changes(config, {}, watcher.statuses(config, print), clock=clock)
    seen = first[1]["toy-line-fit"]
    assert seen.projected_end and seen.projected_end > now["t"]
    span = seen.projected_end - now["t"]

    now["t"] = seen.projected_end + 0.4 * span                          # within tolerance
    assert not [c for c in watcher.changes(config, first[1], watcher.statuses(config, print),
                                           clock=clock)[0] if c.what == "overrun"]
    now["t"] = seen.projected_end + 0.6 * span                          # past it
    found, after = watcher.changes(config, first[1], watcher.statuses(config, print), clock=clock)
    assert [c.what for c in found] == ["overrun"]
    assert "past its own projection" in found[0].detail
    assert after["toy-line-fit"].overrun_told
    assert watcher.changes(config, after, watcher.statuses(config, print), clock=clock)[0] == []


def test_a_killed_watcher_picks_up_from_disk_rather_than_from_memory(project):
    """The machine sleeps, the task restarts. What it knew has to be on disk, or every finished
    run in the project is announced again."""
    config, kind, spec, out = _finish_run(project)
    watcher.tick(config)
    assert watcher.tick_path(config).is_file()
    reread = watcher.read_last(config)                 # a "new process"
    assert reread["toy-line-fit"].state == "finished"
    assert watcher.changes(config, reread, watcher.statuses(config, print))[0] == []


def test_the_log_line_is_written_even_when_the_toast_does_not_happen(project, monkeypatch):
    """Windows toast varies by build, by focus assist and by whether the session is
    interactive. A channel that silently does nothing on some machines must never be the only
    record that a run finished."""
    monkeypatch.setattr(notify, "toast", lambda *a, **k: "no WinRT on this machine")
    _finish_run(project)
    config = load_config()
    watcher.tick(config)
    text = notify.log_path(config).read_text(encoding="utf-8")
    assert "toy-line-fit: finished" in text
    assert "no toast: no WinRT on this machine" in text


def test_a_toast_that_raises_does_not_take_the_watcher_down(project, monkeypatch):
    """Every way a toast can fail -- no PowerShell, no WinRT, an execution policy, a
    non-interactive session -- is a reason to have a quieter machine, not to stop watching."""
    def explode(*a, **k):
        raise OSError("the notification host is not registered")

    monkeypatch.setattr(notify, "toast", _real_toast)      # undo the autouse stub
    monkeypatch.setattr(notify.subprocess, "run", explode)
    monkeypatch.setattr(notify, "_toast_broken", False)
    monkeypatch.setattr(notify.sys, "platform", "win32")
    _finish_run(project)
    config = load_config()
    assert [c.what for c in watcher.tick(config)] == ["finished"]
    text = notify.log_path(config).read_text(encoding="utf-8")
    assert "no toast: OSError: the notification host is not registered" in text


def test_a_spec_that_stopped_parsing_does_not_stop_the_watch(project):
    (project / "studies" / "broken.toml").write_text("name = \nkind =", encoding="utf-8")
    config, kind, spec, out = _finish_run(project)
    said = []
    found = watcher.tick(config, log=said.append)
    assert [c.what for c in found] == ["finished"]
    assert any("skipped broken.toml" in s for s in said)


def test_a_report_that_fails_is_written_down_and_the_tick_survives(project, monkeypatch):
    config, kind, spec, out = _finish_run(project)
    monkeypatch.setattr("rl_researcher.artefacts.run_report.write_report",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad summary")))
    said = []
    found = watcher.tick(config, log=said.append)
    assert found and "action failed: ValueError: bad summary" in found[0].did[0]
    assert any("bad summary" in s for s in said)
    assert watcher.tick_path(config).is_file()         # the tick was still written


def test_a_dry_run_says_what_changed_and_writes_no_artefact(project):
    config, kind, spec, out = _finish_run(project)
    found = watcher.tick(config, dry_run=True)
    assert [c.what for c in found] == ["finished"] and found[0].did == []
    assert not (out / "README.md").exists()
    assert not (config.path("state") / "STATE.md").exists()


def test_the_watcher_starts_nothing(project):
    """Starting queued work is a decision, and a decision needs a person. This is the property
    the whole design turns on, so it is checked against the source rather than the behaviour."""
    import inspect

    src = inspect.getsource(watcher)
    for forbidden in ("runner.run", "from rl_researcher.runner import", "subprocess",
                      "Popen", "run_unit"):
        assert forbidden not in src, f"the watcher reaches for {forbidden}"
    assert "launch" in src                              # ... and says out loud that it does not


def test_one_tick_from_the_command_line(project, capsys):
    _finish_run(project)
    assert watcher.main(["--once", "--quiet"]) == 0
    assert "1 change(s)" in capsys.readouterr().out
    assert watcher.main(["--once", "--quiet"]) == 0
