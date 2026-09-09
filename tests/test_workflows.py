"""Workflow contracts, including deterministic races and crash recovery."""

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from rl_researcher import atomic, report, run, serve
from rl_researcher.config import load_config
from rl_researcher.evidence import policy, read_evidence
from rl_researcher.ledger import Finding, open_ledger
from rl_researcher.regions import set_region
from rl_researcher.research_queue import mutate, snapshot
from rl_researcher.workflow import discovery, resolve, submit, view
from rl_researcher.workflow_store import WorkflowError, exclusive, launch_path


@pytest.fixture
def evidence_project(project):
    assert run.main(["toy-line-fit", "--max-steps", "3", "--max-seconds", "5"]) == 0
    assert report.main(["toy-line-fit"]) == 0
    config = load_config(project)
    kind, spec, out = resolve(config, "toy-line-fit")
    return config, kind, spec, out


def request(config, action, **extra):
    kind, spec, out = resolve(config, "toy-line-fit")
    return {
        "run": spec.name,
        "operation_id": str(uuid.uuid4()),
        "evidence_revision": read_evidence(spec, kind, out)["revision"],
        **extra,
    }


def queue_request(config, name="toy-line-fit", **extra):
    return {
        "run": name,
        "operation_id": str(uuid.uuid4()),
        "revision": snapshot(config)["revision"],
        **extra,
    }


def test_queue_roundtrip_preserves_context_and_retries(project):
    config = load_config(project)
    config.path("queue").write_text(
        '# My queue\n[[entry]]\nrun="missing"\nhold=true\nwhy="Human review"\ncustom=42\n',
        encoding="utf-8",
    )
    payload = queue_request(config, "missing", note="Reviewed the hold")
    result = mutate(config, "release", payload)
    assert mutate(config, "release", payload) == result
    text = config.path("queue").read_text(encoding="utf-8")
    assert "# My queue" in text and "custom=42" in text and "Human review" in text
    assert not snapshot(config)["entries"][0]["hold"]
    with pytest.raises(WorkflowError, match="different request"):
        mutate(config, "remove", payload)
    mutate(config, "remove", queue_request(config, "missing"))
    assert not snapshot(config)["entries"]
    assert len(snapshot(config)["history"]) == 2
    # A removed target never reappears through audit history.
    assert not serve.api(config, "GET", "/api/state")[1]["queued"]


def test_held_unknown_target_and_invalid_specs_remain_visible(project):
    config = load_config(project)
    config.path("queue").write_text(
        '[[entry]]\nrun="visitation-canary-v1"\nhold=true\nwhy="Review first"', encoding="utf-8"
    )
    (config.path("specs") / "broken.toml").write_text("name = [broken", encoding="utf-8")
    archive = config.path("specs") / "archive"
    archive.mkdir()
    for i in range(17):
        (archive / f"old{i}.toml").write_text('name="archived"', encoding="utf-8")
    data = serve.api(config, "GET", "/api/state")[1]
    assert [r["run"] for r in data["on_hold"]] == ["visitation-canary-v1"]
    assert data["on_hold"][0]["diagnostic"]
    assert len(data["catalog"]) == 2
    assert any(r.get("diagnostic") for r in data["catalog"])
    assert not any(r["run"] == "queue" for r in data["catalog"])


def test_duplicate_and_malformed_queue_fail_closed(project):
    config = load_config(project)
    config.path("queue").write_text(
        '[[entry]]\nrun="toy-line-fit"\nhold=true\n[[entry]]\nrun="toy-line-fit"', encoding="utf-8"
    )
    with pytest.raises(WorkflowError, match="Duplicate"):
        mutate(config, "release", queue_request(config, note="okay"))
    assert run.main(["toy-line-fit", "--no-gate", "--no-check", "--allow-guards"]) == 4
    config.path("queue").write_text("bad = [", encoding="utf-8")
    assert run.main(["toy-line-fit", "--no-gate"]) == 4


def test_acknowledgement_does_not_decide_and_changed_evidence_requires_both(evidence_project):
    config, kind, spec, out = evidence_project
    payload = request(config, "review")
    submit(config, "review", payload)
    data = view(config, spec, kind, out)
    assert data["review"] and data["decision_pending"]
    decision = request(config, "decide", selected=[0], note="Build on this evidence")
    recorded = submit(config, "decide", decision)
    assert not view(config, spec, kind, out)["decision_pending"]
    summary = json.loads((out / "results.json").read_text(encoding="utf-8"))
    summary["additional_evidence"] = 2
    atomic.write_json(out / "results.json", summary)
    data = view(config, spec, kind, out)
    assert data["review_required"] and data["decision_pending"]
    assert data["decision"] is None and data["decisions"][0]["applicability"] == "Earlier evidence"
    assert submit(config, "decide", decision)["finding"] == recorded["finding"]
    assert submit(config, "decide", decision)["applicability"] == "Earlier evidence"
    new = submit(
        config,
        "decide",
        request(config, "decide", selected=[0], note="Still supported by the new evidence"),
    )
    rows = open_ledger(config).query(kind="decision")
    assert len(rows) == 2 and rows[-1].supersedes == [recorded["finding"]]
    assert new["finding"] != recorded["finding"]


def test_reviewed_compatibility_and_explicit_override(evidence_project):
    config, kind, spec, out = evidence_project
    md = out / "README.md"
    md.write_text(
        set_region(
            md.read_text(encoding="utf-8"),
            "authored",
            "decision",
            "- [ ] **Reviewed** — acknowledge limitations; no experiment approved.",
        ),
        encoding="utf-8",
    )
    data = view(config, spec, kind, out)
    assert not data["decision_required"] and not data["options"]
    submit(
        config,
        "decide",
        request(
            config,
            "decide",
            choices=["**Reviewed** — acknowledge limitations; no experiment approved."],
        ),
    )
    assert not open_ledger(config).query(kind="decision")
    assert not view(config, spec, kind, out)["review_required"]
    path = config.path("specs") / "toy-line-fit.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n[workflow]\ndecision_required=true\n",
        encoding="utf-8",
    )
    assert policy(spec, kind, md.read_text(encoding="utf-8")).decision_required
    assert not view(config, spec, kind, out)["capabilities"]["decide"]["enabled"]


def test_unknown_legacy_decision_does_not_cover_current_evidence(evidence_project):
    config, kind, spec, out = evidence_project
    summary = read_evidence(spec, kind, out)["summary"]
    open_ledger(config).add(
        Finding(
            kind="decision",
            run=spec.name,
            choices=["go"],
            fingerprint=summary["fingerprint"],
            commit=summary["git_sha"][:12],
        )
    )
    data = view(config, spec, kind, out)
    assert data["decision"] is None and data["decision_pending"]
    assert data["decisions"][0]["applicability"] == "Evidence revision unknown"


def test_review_revalidates_external_evidence_at_commit(evidence_project, monkeypatch):
    config, kind, spec, out = evidence_project
    from rl_researcher import workflow

    entered, changed = threading.Event(), threading.Event()

    def pause():
        entered.set()
        assert changed.wait(5)

    monkeypatch.setattr(workflow, "_before_commit", pause)
    payload = request(config, "review")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(submit, config, "review", payload)
        assert entered.wait(5)
        atomic.write_json(out / "results.json", {"changed_during_request": True})
        changed.set()
        with pytest.raises(WorkflowError, match="Evidence changed"):
            future.result()
    assert not (config.path("ledger") / "reviews.json").exists()


def test_execution_starting_during_review_is_rechecked(evidence_project, monkeypatch):
    config, kind, spec, out = evidence_project
    from rl_researcher import workflow

    monkeypatch.setattr(
        workflow,
        "_before_commit",
        lambda: atomic.write_json(launch_path(config), {spec.name: {"pid": os.getpid()}}),
    )
    with pytest.raises(WorkflowError, match="Execution started"):
        submit(config, "review", request(config, "review"))
    assert not (config.path("ledger") / "reviews.json").exists()


def test_coordinated_edit_waits_for_review_commit(evidence_project, monkeypatch):
    config, kind, spec, out = evidence_project
    from rl_researcher import workflow

    entered, release, edited = threading.Event(), threading.Event(), threading.Event()

    def pause():
        entered.set()
        assert release.wait(5)

    def edit():
        with exclusive(config):
            atomic.write_json(out / "results.json", {"edited": True})
            edited.set()

    monkeypatch.setattr(workflow, "_before_commit", pause)
    payload = request(config, "review")
    with ThreadPoolExecutor(2) as pool:
        review = pool.submit(submit, config, "review", payload)
        assert entered.wait(5)
        update = pool.submit(edit)
        assert not edited.wait(0.1)
        release.set()
        assert review.result()["ok"]
        update.result()
    assert view(config, spec, kind, out)["review_required"]


def test_recover_decision_after_ack_write_failure(evidence_project, monkeypatch):
    config, kind, spec, out = evidence_project
    from rl_researcher import workflow

    original = workflow._ack
    monkeypatch.setattr(workflow, "_ack", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    payload = request(config, "decide", selected=[0], note="Evidence supports go")
    with pytest.raises(OSError):
        submit(config, "decide", payload)
    atomic.write_json(out / "results.json", {"later": True})
    monkeypatch.setattr(workflow, "_ack", original)
    assert submit(config, "decide", payload)["applicability"] == "Earlier evidence"
    assert len(open_ledger(config).query(kind="decision")) == 1
    assert view(config, spec, kind, out)["review_required"]


def test_revision_ignores_heartbeat_formatting_and_checkbox(evidence_project):
    config, kind, spec, out = evidence_project
    before = read_evidence(spec, kind, out)["revision"]
    summary = out / "results.json"
    summary.write_text(
        json.dumps(json.loads(summary.read_text(encoding="utf-8")), indent=8), encoding="utf-8"
    )
    md = out / "README.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace("- [ ] **go**", "- [x] **go**"), encoding="utf-8"
    )
    for path in out.glob("*/seed*/progress.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["updated"] = 1
        atomic.write_json(path, data)
    assert read_evidence(spec, kind, out)["revision"] == before


def test_aggregate_history_requires_adapter_verification(project):
    config = load_config(project)
    kind, spec, out = resolve(config, "toy-line-fit")
    atomic.write_json(out / "results.json", {"legacy": True})
    assert read_evidence(spec, kind, out)["completion"] == "unknown"
    kind.historical_evidence = lambda s, o: {
        "completion": "complete",
        "sources": ["results.json"],
        "summary": {"legacy": True},
    }
    assert read_evidence(spec, kind, out)["completion"] == "complete"
    from rl_researcher.status import run_status

    status = run_status(spec, kind, out)
    assert status.finished and status.done == 0
    kind.historical_evidence = lambda s, o: {
        "completion": "complete",
        "sources": ["../outside.json"],
    }
    with pytest.raises(ValueError):
        read_evidence(spec, kind, out)


def test_explicit_bad_metadata_is_catalog_diagnostic(project):
    config = load_config(project)
    path = config.path("specs") / "toy-line-fit.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + '\n[workflow]\ndecision_required="yes"', encoding="utf-8"
    )
    assert not discovery(config)[0]["valid"]


def test_os_lock_excludes_another_process_and_is_released(project):
    import subprocess
    import sys
    from pathlib import Path

    config = load_config(project)
    code = """
import sys
from rl_researcher.config import load_config
from rl_researcher.workflow_store import exclusive, WorkflowError
try:
    with exclusive(load_config(sys.argv[1]), timeout=.15):
        print('acquired')
except WorkflowError:
    print('locked')
"""
    command = [sys.executable, "-c", code, str(project)]
    kwargs = {
        "cwd": Path(__file__).resolve().parents[1],
        "capture_output": True,
        "text": True,
        "timeout": 10,
    }
    with exclusive(config):
        child = subprocess.run(command, **kwargs)
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "locked"
        assert (config.path("ledger") / "workflow.lock").exists()
    child = subprocess.run(command, **kwargs)
    assert child.returncode == 0 and child.stdout.strip() == "acquired", child.stderr


def test_operation_binding_and_authoritative_queue_receipt_recovery(project, monkeypatch):
    from rl_researcher import research_queue

    config = load_config(project)
    payload = queue_request(config)
    finish = research_queue.finish
    monkeypatch.setattr(
        research_queue, "finish", lambda *a: (_ for _ in ()).throw(OSError("journal unavailable"))
    )
    with pytest.raises(OSError):
        mutate(config, "add", payload)
    monkeypatch.setattr(research_queue, "finish", finish)
    assert mutate(config, "add", payload)["ok"]
    assert len(snapshot(config)["history"]) == 1
    for changed in ({"run": "another"}, {"note": "different"}, {"revision": "different"}):
        with pytest.raises(WorkflowError) as error:
            mutate(config, "add", {**payload, **changed})
        assert error.value.data["reason_code"] == "operation_id_reused"
    with pytest.raises(WorkflowError) as error:
        submit(config, "review", payload)
    assert error.value.data["reason_code"] == "operation_id_reused"


def test_startup_winning_lock_blocks_review(evidence_project):
    config, kind, spec, out = evidence_project
    payload = request(config, "review")
    with exclusive(config):
        atomic.write_json(launch_path(config), {spec.name: {"pid": os.getpid()}})
    with pytest.raises(WorkflowError, match="running or starting"):
        submit(config, "review", payload)


def test_incomplete_standard_evidence_does_not_use_legacy_hook(project):
    config = load_config(project)
    kind, spec, out = resolve(config, "toy-line-fit")
    unit = kind.units(spec)[0]
    atomic.write_json(out / unit / "results.json", {"status": "incomplete", "steps": 3})
    kind.historical_evidence = lambda *a: (_ for _ in ()).throw(
        AssertionError("must not inspect legacy evidence")
    )
    assert read_evidence(spec, kind, out)["completion"] == "incomplete"
