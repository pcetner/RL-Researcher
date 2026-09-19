import os
from rl_researcher import atomic, board_view


def test_preview_cleanup_preserves_durable_and_recent(tmp_path):
    directory = tmp_path
    (directory / "artifacts").mkdir()
    atomic.write_json(
        directory / "manifest.json",
        {"id": "test", "started": 0, "definition": {"trials": [{"id": "a", "label": "A"}]}},
    )
    rows = []

    def emit(kind, data):
        rows.append({"seq": len(rows) + 1, "time": 10, "kind": kind, "data": data})

    for i in range(20):
        path = f"artifacts/{i}.png"
        (directory / path).write_bytes(b"image")
        os.utime(directory / path, (10, 10))
        emit("artifact", {"path": path, "label": "Live preview"})
        emit("sample", {"trial": "a", "attempt": 1, "decision": i, "preview": {"path": path}})
    emit("artifact", {"path": "artifacts/0.png", "label": "Start frame"})

    def save():
        import json

        (directory / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    save()
    board_view.prune_previews(directory, 1000)
    assert len(list((directory / "artifacts").iterdir())) == 20  # active publication protected
    emit("state", {"state": "Completed"})
    save()
    board_view.prune_previews(directory, 1000)
    assert {p.name for p in (directory / "artifacts").iterdir()} == {"0.png"} | {
        f"{i}.png" for i in range(8, 20)
    }
    assert len(board_view.preview_window(rows)) == 12


def test_study_progress_eta():
    manifest = {
        "definition": {
            "trials": [
                {"id": "a", "decisions": 100},
                {"id": "b", "decisions": 100},
                {"id": "c", "decisions": 100},
            ]
        }
    }
    state = {
        "state": "Running",
        "trial": "b",
        "trials": {
            "a": {"state": "Completed", "progress": 100},
            "b": {"state": "Running", "progress": 50},
            "c": {"state": "Ready", "progress": 0},
        },
    }
    rows = [
        {"kind": "trial", "time": 10, "data": {"id": "a"}},
        {"kind": "result", "time": 110, "data": {"trial": "a"}},
    ]
    p = board_view.study_progress(manifest, state, rows)
    assert p == {"completed": 1, "total": 3, "done": 150, "total_work": 300, "eta_seconds": 150}
    state["unresponsive"] = True
    assert board_view.study_progress(manifest, state, rows)["eta_seconds"] is None


def test_catalog_prioritizes_active_then_recent_not_old_failures(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(board_view.config, "load", lambda root: {"experiments": []})
    monkeypatch.setattr(board_view.config, "store", lambda root: tmp_path)
    for identity, timestamp, state in [
        ("old-recovery", 1, "Incomplete"),
        ("study", 3, "Completed"),
        ("active", 2, "Running"),
    ]:
        directory = tmp_path / identity
        directory.mkdir()
        atomic.write_json(
            directory / "manifest.json",
            {
                "id": identity,
                "started": timestamp,
                "definition": {
                    "name": identity,
                    "question": "Question",
                    "trials": [{"id": "a", "label": "A"}],
                },
            },
        )
        rows = []
        if state == "Completed":
            rows.append(
                {
                    "seq": 1,
                    "time": timestamp,
                    "kind": "result",
                    "data": {"trial": "a", "result": {}},
                }
            )
        rows.append(
            {"seq": len(rows) + 1, "time": timestamp, "kind": "state", "data": {"state": state}}
        )
        (directory / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    entries = board_view.catalog(tmp_path)["executions"]
    assert [e["id"] for e in entries] == ["active", "study", "old-recovery"]
    assert entries[1]["completed"] == entries[1]["total"] == 1
    assert entries[2]["completed"] == 0
