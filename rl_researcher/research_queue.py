"""The explicit operational queue, separate from specification discovery."""

from __future__ import annotations

import hashlib
from typing import Any

import tomlkit

from rl_researcher import atomic
from rl_researcher.units import stamp_now
from rl_researcher.workflow_store import WorkflowError, actor, begin, exclusive, finish


def snapshot(config: Any) -> dict:
    path = config.path("queue")
    raw = path.read_bytes() if path.exists() else b""
    revision = hashlib.sha256(raw).hexdigest()
    try:
        doc = tomlkit.parse(raw.decode("utf-8"))
        entries = doc.get("entry", [])
        if not isinstance(entries, list) or any(
            not isinstance(e, dict)
            or not isinstance(e.get("run"), str)
            or not e["run"].strip()
            or not isinstance(e.get("hold", False), bool)
            for e in entries
        ):
            raise ValueError("entries require a run name and boolean hold")
        history = doc.get("history", [])
        if not isinstance(history, list) or any(not isinstance(e, dict) for e in history):
            raise ValueError("history must be an array of tables")
        return {
            "revision": revision,
            "entries": [dict(e) for e in entries],
            "history": [dict(e) for e in history],
            "document": doc,
            "error": "",
        }
    except Exception as exc:
        return {
            "revision": revision,
            "entries": [],
            "history": [],
            "document": None,
            "error": f"Queue unreadable: {exc}. Open {path} and correct it.",
        }


def hold(config: Any, run: str) -> str:
    data = snapshot(config)
    if data["error"]:
        return data["error"]
    return "\n".join(
        str(e.get("why") or "Research decision required before launch.")
        for e in data["entries"]
        if e["run"] == run and e.get("hold")
    )


def mutate(config: Any, action: str, payload: dict) -> dict:
    from rl_researcher.workflow import resolve

    with exclusive(config):
        operation, binding, receipt = begin(config, "queue/" + action, payload)
        if receipt is not None:
            return receipt
        data = snapshot(config)
        if data["error"]:
            raise WorkflowError("queue_invalid", data["error"])
        event = next((e for e in data["history"] if e.get("operation_id") == operation), None)
        if event:
            if event.get("binding") != binding:
                raise WorkflowError(
                    "operation_id_reused", "Queue operation belongs to another request."
                )
            return finish(config, operation, binding, dict(event["receipt"]))
        if payload.get("revision") != data["revision"]:
            raise WorkflowError(
                "queue_changed",
                "The queue changed. Refresh and review your action.",
                revision=data["revision"],
                conflict=True,
            )
        if not isinstance(payload.get("note", ""), str):
            raise WorkflowError("invalid_note", "The explanation must be text.", 400)
        run = payload.get("run")
        if not isinstance(run, str) or not run.strip():
            raise WorkflowError("invalid_run", "A run name is required.", 400)
        indexes = [i for i, e in enumerate(data["entries"]) if e["run"] == run]
        if len(indexes) > 1:
            raise WorkflowError(
                "duplicate_queue_target",
                "Duplicate queue entries. Open the queue and resolve them.",
            )
        doc = data["document"]
        if "entry" not in doc:
            doc["entry"] = tomlkit.aot()
        entries = doc["entry"]
        if action == "add":
            resolve(config, run)
            if not indexes:
                entry = tomlkit.table()
                entry.update({"run": run, "hold": False, "since": stamp_now()})
                entries.append(entry)
        elif action in ("remove", "release"):
            if not indexes:
                raise WorkflowError("queue_target_missing", "This run is no longer queued.", 404)
            entry = entries[indexes[0]]
            if action == "remove":
                if entry.get("hold"):
                    raise WorkflowError(
                        "research_hold", "Release this hold with an explanation before removing it."
                    )
                del entries[indexes[0]]
            else:
                if not str(payload.get("note") or "").strip():
                    raise WorkflowError(
                        "note_required", "Explain why this hold can be released.", 400
                    )
                entry["hold"] = False
        else:
            raise WorkflowError("unknown_action", "Unknown queue action.", 404)
        # Receipt revision cannot include itself. Use the operation ID as the receipt;
        # clients fetch the byte revision after success, also valid for committed retries.
        receipt = {"ok": True, "operation_id": operation, "run": run, "action": action}
        if "history" not in doc:
            doc["history"] = tomlkit.aot()
        event = tomlkit.table()
        event.update(
            {
                "operation_id": operation,
                "binding": binding,
                "action": action,
                "run": run,
                "date": stamp_now(),
                "note": str(payload.get("note") or ""),
                "actor": actor(config),
                "receipt": receipt,
            }
        )
        doc["history"].append(event)
        if snapshot(config)["revision"] != data["revision"]:
            raise WorkflowError(
                "queue_changed", "The queue changed while saving. Refresh and retry.", conflict=True
            )
        atomic.write_text(config.path("queue"), tomlkit.dumps(doc))
        return finish(config, operation, binding, receipt)
