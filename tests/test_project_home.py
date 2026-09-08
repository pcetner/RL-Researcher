from rl_researcher import serve
from rl_researcher.artefacts.state import build_state
from rl_researcher.board_view import recovery
from rl_researcher.config import config_from_dict, load_config
from rl_researcher.units import UnitState


def test_project_context_and_plan_stay_inside_project(project, tmp_path):
    config = config_from_dict({"project": {"goal": "Learn useful dynamics", "focus": "Test planning"}}, project)
    config.path("plan").parent.mkdir(parents=True, exist_ok=True)
    config.path("plan").write_text("# Plan", encoding="utf-8")
    context = build_state(config).to_json()["context"]
    assert context == {"goal": "Learn useful dynamics", "focus": "Test planning", "plan": "docs/PLAN.md"}
    outside = tmp_path / "outside.md"
    outside.write_text("not a project page", encoding="utf-8")
    config.paths.plan = str(outside)
    assert build_state(config).context["plan"] == ""


def test_research_hold_refuses_launch_even_with_compute_approval(project, monkeypatch):
    config = load_config(project)
    config.path("queue").write_text('[[entry]]\nrun="toy-line-fit"\nhold=true\nwhy="Resolve the reward question."', encoding="utf-8")
    monkeypatch.setattr(serve, "_launch", lambda *a: (_ for _ in ()).throw(AssertionError("must not launch")))
    code, data = serve.api(config, "POST", "/api/run", {"run": "toy-line-fit"}, runs={})
    assert code == 409
    assert "Resolve the reward question" in data["error"]
    assert serve.research_hold(config, "another-run") == ""


def test_checkpoint_sidecar_does_not_promise_recovery():
    unit = UnitState(unit="arm/seed0", arm="arm", seed=0, status="failed", checkpoint_step=40, resumable=True)
    evidence = recovery([unit])
    assert evidence["status"] == "Unknown"
    assert "compatibility has not been verified" in evidence["units"][0]["reason"]
    assert recovery([])["status"] == "Not needed"


def test_home_reports_compute_gate(project):
    config = load_config(project)
    config.gate.ungated_wall_minutes = 0
    row = build_state(config).ready[0]
    assert row["approval_needed"]
    assert row["wall_seconds"] > 0


def test_recovery_uses_adapter_evidence_and_contains_inspection_errors():
    class Kind:
        def recovery_status(self, spec, unit, out):
            return {"status": "Yes", "reason": "Checkpoint identity and payload verified."}

    unit = UnitState(unit="arm/seed0", arm="arm", seed=0, status="failed")
    assert recovery([unit], kind=Kind())["status"] == "Yes"

    class Broken:
        def recovery_status(self, spec, unit, out):
            raise ValueError("invalid checkpoint")

    result = recovery([unit], kind=Broken())
    assert result["status"] == "Unknown"
    assert "inspection failed" in result["units"][0]["reason"]
