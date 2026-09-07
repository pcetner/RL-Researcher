"""The three properties the whole design is for, each as a test.

They were stated at the start and are easy to lose one commit at a time, so they are checked
here rather than inferred from the parts that implement them.

**Hands-off** — a run that finishes with nobody watching becomes a report, ledger rows and a
state entry with no other call. **Transparent** — every artefact regenerates from disk, byte
for byte but for the stamp saying when. **Interactable** — a person's inputs are files, and the
next command reads them: a ticked box, an edited queue, a deleted lock.
"""

import json
import time

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]


from rl_researcher import notify, watcher  # noqa: E402
from rl_researcher.config import kind_for, load_config, out_dir_for  # noqa: E402
from rl_researcher.units import unit_dir  # noqa: E402


@pytest.fixture(autouse=True)
def no_toast(monkeypatch):
    monkeypatch.setattr(notify, "toast", lambda *a, **k: "stubbed")


def _project(project):
    config = load_config()
    path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(path, config)
    spec = kind.load(path)
    return config, kind, spec, out_dir_for(spec, config)


def _finished(project):
    """What a run that ended looks like on disk, with nobody having watched it."""
    config, kind, spec, out = _project(project)
    runs = []
    for unit in kind.units(spec):
        arm, seed = unit.split("/seed")
        d = unit_dir(out, unit)
        d.mkdir(parents=True, exist_ok=True)
        row = {"arm": arm, "seed": int(seed), "status": "complete", "steps": 200,
               "max_steps": 200, "seconds": 4.0,
               "metrics": {"slope_error": 0.02 if arm == "ols" else 0.9,
                           "r2": 0.99 if arm == "ols" else 0.4},
               "history": {"loss": [1.0, 0.5, 0.2]}}
        (d / "results.json").write_text(json.dumps(row), encoding="utf-8")
        runs.append(row)
    (out / "results.json").write_text(json.dumps(
        {"run": spec.name, "kind": kind.name, "git_sha": "0" * 40, "wall_seconds": 24.0,
         "seeds": list(spec.seeds), "budget": {"max_steps": 200, "max_seconds": 60},
         "device": "cpu", "runs": runs}), encoding="utf-8")
    return config, kind, spec, out


def _stamped(text: str) -> str:
    """The document with the two lines that are about *when* removed, so what is left is what
    it says."""
    import re

    text = re.sub(r"generated=\S+", "generated=X", text)
    text = re.sub(r"\d\d:\d\d:\d\d", "HH:MM:SS", text)
    return re.sub(r"Rendered [^<]*", "Rendered X", text)


# ── hands-off ─────────────────────────────────────────────────────────────────────────────

def test_a_run_that_finished_unattended_becomes_a_report_and_a_state_entry(project):
    """One command, and it is one nobody typed: the watcher's tick. The whole design is for
    the run that ends at three in the morning."""
    config, kind, spec, out = _finished(project)
    assert not (out / "README.md").exists()

    changed = watcher.tick(config)

    assert [c.what for c in changed] == ["finished"]
    assert (out / "README.md").is_file() and (out / "report.html").is_file()
    rows = (config.path("ledger") / "findings.jsonl").read_text(encoding="utf-8").strip()
    assert rows and "slope_error" in rows
    state = json.loads((config.path("state") / "state.json").read_text(encoding="utf-8"))
    assert [w["run"] for w in state["waiting"]] == [spec.name]
    assert (config.path("state") / "index.html").is_file()


# ── transparent ───────────────────────────────────────────────────────────────────────────

def test_every_artefact_regenerates_from_disk_byte_for_byte(project):
    """A page only the process that produced it can produce cannot be restyled, regenerated
    after a crash, or checked by anyone else."""
    from rl_researcher.artefacts.dashboard import collect, render, write_dashboard
    from rl_researcher.artefacts.run_report import write_report
    from rl_researcher.artefacts.state import write_state
    from rl_researcher.report import main as report_main

    config, kind, spec, out = _finished(project)
    summary = json.loads((out / "results.json").read_text(encoding="utf-8"))

    first = write_report(spec, summary, out, kind=kind, ledger=None).read_text(encoding="utf-8")
    page1 = (out / "report.html").read_text(encoding="utf-8")
    dash1 = write_dashboard(spec, kind, out).read_text(encoding="utf-8")
    state1 = write_state(config).read_text(encoding="utf-8")

    time.sleep(1.05)                       # so a stamp that moves has a chance to move

    assert report_main([spec.name, "--no-ledger"]) == 0
    again = (out / "README.md").read_text(encoding="utf-8")
    page2 = (out / "report.html").read_text(encoding="utf-8")
    dash2 = write_dashboard(spec, kind, out).read_text(encoding="utf-8")
    state2 = write_state(config).read_text(encoding="utf-8")

    assert _stamped(first) == _stamped(again)
    assert _stamped(page1) == _stamped(page2)
    assert _stamped(dash1) == _stamped(dash2)
    assert _stamped(state1) == _stamped(state2)
    # ... and the check is not vacuous: something did change, and it was only the stamp.
    assert dash1 != dash2 or "HH:MM:SS" not in _stamped(dash1)
    assert render(spec, kind, collect(spec, kind, out)) != ""


# ── interactable ──────────────────────────────────────────────────────────────────────────

def test_a_ticked_box_changes_what_the_next_state_says(project):
    """A person's inputs are files. Nothing is typed into a page, because an input that lives
    only in a page is an input that goes stale."""
    from rl_researcher.artefacts.state import build_state
    from rl_researcher.decide import main as decide_main

    config, kind, spec, out = _finished(project)
    watcher.tick(config)
    assert [w.run for w in build_state(config).waiting] == [spec.name]

    readme = out / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "- [ ] **iterate**" in text
    readme.write_text(text.replace("- [ ] **iterate**", "- [x] **iterate**")
                      .replace("_(write here)_", "The noisy arm missed both bars, as predicted."),
                      encoding="utf-8")

    assert decide_main([spec.name]) == 0
    view = build_state(config)
    assert [w.run for w in view.waiting] == []            # no longer awaiting a person
    assert [d["run"] for d in view.decided] == [spec.name]


def test_an_edited_queue_and_a_deleted_lock_each_change_the_next_state(project):
    from rl_researcher.artefacts.state import build_state
    from rl_researcher.lock import acquire_lock, release_lock
    from rl_researcher.status import format_status, run_status

    config, kind, spec, out = _project(project)
    queue = config.path("queue")
    queue.parent.mkdir(parents=True, exist_ok=True)
    assert build_state(config).queued == []

    # `[[entry]]` is what `_queue` reads; a queue file with the wrong table name is an empty
    # queue, which is what this test found on its first run.
    queue.write_text('[[entry]]\nrun = "something-next"\nwhy = "after the canary"\n',
                     encoding="utf-8")
    assert [q.get("run") for q in build_state(config).queued] == ["something-next"]

    # A unit that is talking, and a lock over it: that is what running looks like from outside.
    d = unit_dir(out, kind.units(spec)[0])
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.json").write_text(json.dumps(
        {"status": "running", "step": 100, "max_steps": 200, "elapsed_seconds": 4.0,
         "updated": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}), encoding="utf-8")
    lock = acquire_lock(out, spec.name, lambda _s: None)
    held = run_status(spec, kind, out)
    assert held.lock is not None
    assert [r["run"] for r in build_state(config).running] == [spec.name]
    assert any("a run is in progress" in line or "no longer alive" in line for line in format_status(held))

    # Deleting the lock is how a person says "that process is gone", and the next `status`
    # reads it. The run itself stays on the state page: its heartbeat is still on disk, and
    # something has to say what became of it.
    release_lock(lock)
    after = run_status(spec, kind, out)
    assert after.lock is None
    assert not any("a run is in progress" in line or "no longer alive" in line for line in format_status(after))
    assert [r["run"] for r in build_state(config).running] == [spec.name]


def test_a_decision_nobody_has_made_is_not_recorded_as_one(project):
    """`decide` exits 1 with no box ticked, which is the correct outcome for a decision that
    has not been made — not a default, and not an empty row in the ledger."""
    from rl_researcher.decide import main as decide_main
    from rl_researcher.ledger import open_ledger

    config, kind, spec, out = _finished(project)
    watcher.tick(config)
    before = len(open_ledger(config).query(kind="decision"))
    assert decide_main([spec.name]) == 1
    assert len(open_ledger(config).query(kind="decision")) == before
