"""The concise workspace must retain the report's evidence and durable decisions."""

import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import chdir, make_project
from rl_researcher import report, run, serve
from rl_researcher.artefacts.overview import research_summary
from rl_researcher.artefacts.run_report import outcome
from rl_researcher.artefacts.state import build_state
from rl_researcher.config import kind_for, load_config
from rl_researcher.ledger import open_ledger
from rl_researcher.regions import body_of, set_region

pytestmark = [pytest.mark.tier1]
RUN = "toy-line-fit"


@pytest.fixture(scope="module")
def finished_source(tmp_path_factory):
    root = make_project(tmp_path_factory.mktemp("board-source"), max_steps=30)
    with chdir(root):
        assert run.main([RUN, "--max-seconds", "5"]) == 0
        assert report.main([RUN]) == 0
    return root


@pytest.fixture
def board_project(finished_source, tmp_path):
    root = tmp_path / "project"
    shutil.copytree(finished_source, root)
    config = load_config(root)
    out = root / "docs" / "toy" / RUN
    return config, out


def view(config):
    code, data = serve.api(config, "GET", f"/api/run/{RUN}?light=1", runs={})
    assert code == 200, data
    return data


def decide(config, data, **extra):
    from rl_researcher.workflow_store import digest
    payload = {"run": RUN, "selected": [1], "note": "Test fixture: improve the fit.",
               "revision": data["revision"], "evidence_revision": data["evidence_revision"], **extra}
    payload["operation_id"] = digest(payload)
    return serve.api(config, "POST", "/api/decide", payload, runs={})


def test_light_view_uses_report_outcome_without_embedding_images(board_project):
    config, out = board_project
    data = view(config)
    path = config.path("specs") / f"{RUN}.toml"
    spec = kind_for(path, config).load(path)
    summary = json.loads((out / "results.json").read_text())
    assert data["article"] == data["spec_text"] == ""
    assert data["research"]["headline"] == outcome(spec, summary)[0]
    assert data["research"]["provisional"] is False
    assert all(r["seeds"] == 3 for r in data["research"]["rows"])
    assert data["cost"]["basis"] == "nothing-to-run"
    assert data["decision"] is None


def test_results_keep_evidence_and_interpretation_without_second_decision_editor(board_project):
    config, _out = board_project
    code, data = serve.api(config, "GET", f"/api/content/{RUN}?view=results")
    assert code == 200
    assert 'data-region="reading"' in data["html"]
    assert 'data-region="decision"' not in data["html"]
    assert '<summary>Provenance</summary>' in data["html"]
    assert "<iframe" not in data["html"]


def test_selected_choice_and_reason_record_once_and_preserve_other_regions(board_project):
    config, out = board_project
    md = out / "README.md"
    before = md.read_text(encoding="utf-8")
    data = view(config)
    code, result = decide(config, data)
    assert code == 200, result
    after = md.read_text(encoding="utf-8")
    assert body_of(before, "authored", "reading") == body_of(after, "authored", "reading")
    assert after == before, "decision events preserve authored report content"
    assert decide(config, data)[0] == 200, "a retry with the old revision returns the same decision"
    rows = open_ledger(config).query(kind="decision", run=RUN)
    assert len(rows) == 1 and rows[0].note == "Test fixture: improve the fit."
    assert rows[0].choices == [data["options"][1]]
    assert not build_state(config).waiting
    assert view(config)["decision"]["id"] == result["finding"]


@pytest.mark.parametrize("selected", [[], [-1], [99], [True], [1, 1], ["1"]])
def test_invalid_selection_never_changes_the_report(board_project, selected):
    config, out = board_project
    before = (out / "README.md").read_bytes()
    assert decide(config, view(config), selected=selected)[0] == 400
    assert (out / "README.md").read_bytes() == before
    assert not open_ledger(config).query(kind="decision")


def test_empty_reason_never_records_a_decision(board_project):
    config, out = board_project
    before = (out / "README.md").read_bytes()
    code, data = decide(config, view(config), note="  ")
    assert code == 400
    assert data["field"] == "note"
    assert (out / "README.md").read_bytes() == before
    assert not open_ledger(config).query(kind="decision", run=RUN)


def test_external_edit_conflicts_without_overwriting_it(board_project):
    config, out = board_project
    data = view(config)
    md = out / "README.md"
    changed = set_region(md.read_text(encoding="utf-8"), "authored", "reading", "Changed externally.")
    md.write_text(changed, encoding="utf-8")
    code, result = decide(config, data)
    assert code == 409 and result["conflict"]
    assert md.read_text(encoding="utf-8") == changed
    code, result = serve.api(config, "POST", "/api/region", {
        "run": RUN, "region": "reading", "body": "Old edit", "revision": data["revision"]})
    assert code == 409 and result["conflict"]


def test_failed_decision_write_stays_waiting_and_retry_recovers(board_project, monkeypatch):
    config, out = board_project
    from rl_researcher.ledger import Ledger
    original = Ledger.add
    data = view(config)
    monkeypatch.setattr(Ledger, "add", lambda *a: (_ for _ in ()).throw(OSError("disk unavailable")))
    assert decide(config, data)[0] == 500
    assert build_state(config).waiting
    monkeypatch.setattr(Ledger, "add", original)
    assert decide(config, data)[0] == 200
    assert len(open_ledger(config).query(kind="decision")) == 1


def test_two_conflicting_submissions_do_not_record_different_choices(board_project):
    config, _out = board_project
    data = view(config)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: decide(config, data, selected=[i]), [0, 1]))
    assert sorted(code for code, _ in results) == [200, 409]
    assert len(open_ledger(config).query(kind="decision")) == 1


def test_receipt_failure_after_append_can_be_retried_without_duplicate(board_project, monkeypatch):
    config, _out = board_project
    from rl_researcher import workflow
    original = workflow.finish
    data = view(config)
    monkeypatch.setattr(workflow, "finish", lambda *a: (_ for _ in ()).throw(OSError("receipt failed")))
    assert decide(config, data)[0] == 500
    monkeypatch.setattr(workflow, "finish", original)
    assert decide(config, data)[0] == 200
    assert len(open_ledger(config).query(kind="decision")) == 1


def test_divergence_missing_values_and_provisional_results_match_report(board_project):
    config, out = board_project
    path = config.path("specs") / f"{RUN}.toml"
    spec = kind_for(path, config).load(path)
    summary = json.loads((out / "results.json").read_text())
    summary["runs"][0]["metrics"]["slope_error"] = float("inf")
    summary["runs"][1]["metrics"]["r2"] = float("nan")
    data = research_summary(spec, summary, provisional=True)
    assert data["headline"] == outcome(spec, summary)[0]
    assert data["provisional"]
    assert any(r["diverged"] == 1 for r in data["rows"])
    assert any(r["metric"] == "r2" and r["seeds"] == 2 for r in data["rows"])


def test_initial_log_read_is_bounded(board_project):
    config, out = board_project
    (out / "run.log").write_text("old line\n" * 20000 + "latest line\n")
    code, result = serve.api(config, "GET", f"/api/log/{RUN}?tail=1")
    assert code == 200 and len(result["text"].encode()) <= 65536
    assert result["text"].rstrip().endswith("latest line")
    assert result["offset"] == (out / "run.log").stat().st_size


def test_standalone_dashboard_leads_with_outcome_and_collapses_details(board_project):
    config, out = board_project
    path = config.path("specs") / f"{RUN}.toml"
    kind = kind_for(path, config)
    from rl_researcher.artefacts.dashboard import collect, render
    page = render(kind.load(path), kind, collect(kind.load(path), kind, out))
    assert "Registered outcome" in page and "Current best" not in page
    assert '<details class="disclosure" data-disclosure="Run log">' in page
    assert 'http-equiv="refresh"' not in page


def test_new_code_identity_requires_a_new_decision(board_project):
    config, out = board_project
    assert decide(config, view(config))[0] == 200
    path = out / "results.json"
    summary = json.loads(path.read_text())
    summary["git_sha"] = "new-commit"
    path.write_text(json.dumps(summary))
    assert view(config)["decision"] is None
    assert build_state(config).waiting


def test_saved_choice_does_not_rewrite_recorded_choices(board_project):
    config, out = board_project
    data = view(config)
    assert decide(config, data)[0] == 200
    md = out / "README.md"
    md.write_text(md.read_text(encoding="utf-8").replace("- [x] **iterate**", "- [ ] **iterate**")
                  .replace("- [ ] **stop**", "- [x] **stop**"), encoding="utf-8")
    assert view(config)["decision"]["choices"] == [data["options"][1]]
    assert decide(config, view(config), selected=[2])[0] == 409


def test_screening_warning_is_visible_in_compact_summary(board_project):
    config, out = board_project
    path = config.path("specs") / f"{RUN}.toml"
    spec = kind_for(path, config).load(path)
    from dataclasses import replace
    spec = replace(spec, screening=True)
    summary = json.loads((out / "results.json").read_text())
    data = research_summary(spec, summary, provisional=False)
    assert any("Screening" in w for w in data["warnings"])
    assert "Screening" in data["markdown"]


def test_board_history_contains_all_runs_while_state_page_stays_short(board_project):
    config, _out = board_project
    from rl_researcher.ledger import Finding
    ledger = open_ledger(config)
    for i in range(8):
        ledger.add(Finding(kind="decision", run=f"history-{i}", note="Test decision."))
    assert len(build_state(config).decided) == 5
    code, data = serve.api(config, "GET", "/api/state")
    assert code == 200 and len(data["decided"]) == 8


def test_custom_blocks_remain_available_on_finished_results(board_project):
    config, out = board_project
    from rl_researcher.board_view import content
    from rl_researcher.blocks import Prose
    path = config.path("specs") / f"{RUN}.toml"
    kind = kind_for(path, config)
    spec = kind.load(path)
    kind.page_sections = ("headline", "custom-evidence")
    kind.blocks = lambda spec, summary, out, view: [Prose(text="Custom evidence.")] if view == "custom-evidence" else []
    assert "Custom evidence." in content(spec, kind, out, "results")["html"]


def test_legacy_report_remains_readable(board_project):
    config, out = board_project
    (out / "README.md").write_text("# Legacy report\n\nA result without generated regions.\n", encoding="utf-8")
    code, data = serve.api(config, "GET", f"/api/content/{RUN}?view=results")
    assert code == 200 and "A result without generated regions." in data["html"]


def test_recorded_decision_rows_without_choices_remain_readable(board_project):
    config, out = board_project
    from rl_researcher.ledger import Finding
    summary = json.loads((out / "results.json").read_text())
    row = Finding.from_json({"kind": "decision", "run": RUN, "note": "Old decision.",
                             "fingerprint": summary.get("fingerprint", ""),
                             "commit": str(summary.get("git_sha", ""))[:12]})
    assert row.choices == []
    open_ledger(config).add(row)
    assert view(config)["decision"]["note"] == "Old decision."
    assert view(config)["decision"]["applicability"] == "Evidence revision unknown"
    assert not view(config)["decision_pending"]
    assert view(config)["decisions"][0]["note"] == "Old decision."
    assert view(config)["decisions"][0]["applicability"] == "Evidence revision unknown"


def test_live_overview_bounds_trends_without_repeating_the_unit_table():
    from rl_researcher.blocks.disclosure import LiveTrends
    from rl_researcher.blocks.live import LiveUnit, LiveUnits
    units = [LiveUnit(arm="arm", seed=i, step=90, max_steps=200,
                       curves=[("Loss", [1.0, .5, .2], .2, None)]) for i in range(6)]
    trends = LiveTrends(units=units)
    html = trends.html()
    assert html.count('<figure class="trend">') == 3
    assert "Showing 3 of 6 active units" in html and "<table" not in html
    assert not trends.undeclared()
    assert "0.0 steps/s" not in LiveUnits(units=units).md()
    assert "—" in LiveUnits(units=units).html()
