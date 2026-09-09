"""Shared discovery, capabilities, acknowledgements and evidence-bound decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rl_researcher import atomic
from rl_researcher.evidence import acknowledgement, policy, read_evidence
from rl_researcher.ledger import Finding, open_ledger
from rl_researcher.units import stamp_now
from rl_researcher.workflow_store import (
    WorkflowError,
    actor,
    begin,
    exclusive,
    finish,
    pending,
    read,
)


def discovery(config: Any) -> list[dict]:
    from rl_researcher.config import kind_for
    from rl_researcher.spec import load_toml

    entries = []
    for path in sorted(config.path("specs").glob("*.toml")):
        if not path.is_file() or path.resolve() == config.path("queue").resolve():
            continue
        relative = path.relative_to(config.root).as_posix()
        row: dict = {"id": relative, "spec": relative, "run": path.stem, "valid": False}
        try:
            raw = load_toml(path)
            row["run"] = str(raw.get("name") or path.stem)
            row["kind"] = str(raw.get("kind") or "")
            kind = kind_for(path, config)
            spec = kind.load(path)
            policy(spec, kind, "")  # malformed explicit workflow metadata is a registration error
            row.update(
                run=spec.name,
                kind=kind.name,
                valid=True,
                _kind=kind,
                _spec=spec,
                _out=config.out_root(kind.name) / spec.name,
            )
        except Exception as exc:
            row.update(
                diagnostic=str(exc),
                reason_code="invalid_specification",
                next_step=f"Open {relative} and rl-researcher.toml; correct the specification or kind registration.",
            )
        entries.append(row)
    names: dict[str, int] = {}
    for row in entries:
        names[row["run"]] = names.get(row["run"], 0) + 1
    for row in entries:
        if names[row["run"]] > 1:
            row.update(
                valid=False,
                diagnostic="Duplicate run name. Resolve the conflicting specifications.",
                reason_code="duplicate_run",
                next_step="Open the specifications and give each run a unique name.",
            )
    return entries


def resolve(config: Any, run: str) -> tuple[Any, Any, Path]:
    row = next((r for r in discovery(config) if r["run"] == run), None)
    if row is None:
        raise WorkflowError(
            "unknown_run",
            f"No active registration matches {run!r}. Open the queue or register a specification.",
            404,
        )
    if not row["valid"]:
        raise WorkflowError(row["reason_code"], row["diagnostic"], next_step=row["next_step"])
    return row["_kind"], row["_spec"], row["_out"]


def capability(enabled: bool, code: str = "", reason: str = "", next_step: str = "") -> dict:
    return {"enabled": enabled, "reason_code": code, "reason": reason, "next_step": next_step}


def review_store(config: Any) -> dict:
    return read(config.path("ledger") / "reviews.json", {"records": [], "operations": {}})


def applicability(revision: str, current: str) -> str:
    return (
        "Evidence revision unknown"
        if not revision
        else "Current"
        if revision == current
        else "Earlier evidence"
    )


def active(config: Any, spec: Any, out: Path) -> bool:
    from rl_researcher.lock import lock_holder

    held = lock_holder(out)
    return bool(held and held.get("alive") is not False) or pending(config, spec.name)


def view(config: Any, spec: Any, kind: Any, out: Path, *, status: Any = None) -> dict:
    from rl_researcher.gate import decide
    from rl_researcher.research_queue import hold

    evidence = read_evidence(spec, kind, out, status)
    current = evidence["revision"]
    reviews = [
        {**r, "applicability": applicability(r["evidence_revision"], current)}
        for r in review_store(config)["records"]
        if r["run"] == spec.name
    ]
    records = open_ledger(config).query(kind="decision", run=spec.name)
    decisions = []
    for row in records:
        entry = {
            "id": row.id,
            "run": row.run,
            "date": row.date,
            "note": row.note,
            "choices": row.choices,
            "chose": row.choices,
            "evidence_revision": row.evidence_revision,
            "applicability": applicability(row.evidence_revision, current),
            "actor": row.actor,
        }
        if (row.choices and all(acknowledgement(c) for c in row.choices)) or (
            not row.choices and acknowledgement(row.note)
        ):
            reviews.append(entry)
        else:
            decisions.append(entry)
    from rl_researcher.artefacts.state import _decision_region, ticked

    checked = ticked(_decision_region(out / "README.md"))
    if checked and all(acknowledgement(c) for c in checked) and not reviews:
        reviews.append(
            {
                "run": spec.name,
                "date": "",
                "evidence_revision": "",
                "actor": "",
                "note": "Previously acknowledged in the report; evidence revision unknown.",
                "applicability": "Evidence revision unknown",
            }
        )
    review = next((r for r in reversed(reviews) if r["applicability"] == "Current"), None)
    decision = next((r for r in reversed(decisions) if r["applicability"] == "Current"), None)
    busy = active(config, spec, out)
    blocked = capability(
        False,
        "execution_active",
        "This run is already running or starting.",
        "Open progress; wait for execution to stop.",
    )
    review_cap = (
        blocked
        if busy
        else capability(
            evidence["available"],
            "missing_evidence",
            "No readable evidence is available.",
            "Run the registered experiment or inspect its output directory.",
        )
    )
    if review_cap["enabled"]:
        review_cap = capability(True)
    decision_cap = review_cap
    if not evidence["policy"]["decision_required"]:
        decision_cap = capability(
            False,
            "acknowledgement_only",
            "This item requests acknowledgement only.",
            "Mark the evidence reviewed.",
        )
    elif not busy:
        if evidence["completion"] != "complete":
            decision_cap = capability(
                False,
                "incomplete_evidence",
                "Completion has not been verified.",
                "Inspect trials or historical evidence; resolve incomplete results or adapter support.",
            )
        elif not evidence["summary"]:
            decision_cap = capability(
                False,
                "missing_summary",
                "A usable result summary is missing.",
                "Open evidence; generate the summary through the adapter.",
            )
        elif not evidence["policy"]["choices"]:
            decision_cap = capability(
                False,
                "missing_choices",
                "No research choices are available.",
                "Generate the report or correct the explicit decision policy.",
            )
    gate = decide(spec, kind, config, out=out)
    reason = hold(config, spec.name)
    launch = blocked if busy else capability(True)
    if reason:
        launch = capability(False, "research_hold", reason, "Release the hold with an explanation.")
    elif gate.gated and not gate.approved:
        launch = capability(
            False,
            "approval_required",
            "Compute approval is required.",
            "Open the estimate and approve compute.",
        )
    report_cap = (
        blocked
        if busy
        else capability(
            bool(evidence["summary"]),
            "missing_results",
            "No usable summary is available to generate a report.",
            "Open evidence or run breakdown.",
        )
    )
    if report_cap["enabled"]:
        report_cap = capability(True)
    return {
        "evidence_revision": current,
        "evidence": {k: v for k, v in evidence.items() if k != "summary"},
        "decision_policy": evidence["policy"],
        "decision_required": evidence["policy"]["decision_required"],
        "review_required": evidence["available"] and not review and not decision,
        "decision_pending": evidence["policy"]["decision_required"]
        and evidence["available"]
        and not decision,
        "review": review,
        "reviews": reviews,
        "decision": decision,
        "decisions": decisions,
        "options": evidence["policy"]["choices"],
        "capabilities": {
            "review": review_cap,
            "decide": decision_cap,
            "run": launch,
            "report": report_cap,
        },
        "starting": pending(config, spec.name),
    }


def _ack(
    config: Any, run: str, evidence: dict, operation: str, binding: str, note: str, receipt: dict
) -> None:
    store = review_store(config)
    if not any(
        r["run"] == run and r["evidence_revision"] == evidence["revision"] for r in store["records"]
    ):
        store["records"].append(
            {
                "run": run,
                "evidence_revision": evidence["revision"],
                "sources": evidence["sources"],
                "date": stamp_now(),
                "actor": actor(config),
                "note": note,
                "operation_id": operation,
                "binding": binding,
            }
        )
    store["operations"][operation] = {"binding": binding, "receipt": receipt}
    atomic.write_json(config.path("ledger") / "reviews.json", store)


def _before_commit() -> None:
    """A test seam for deterministic external-write and launch races."""


def submit(config: Any, action: str, payload: dict) -> dict:
    with exclusive(config):
        operation, binding, receipt = begin(config, action, payload)
        if receipt is not None:
            return _current_receipt(config, payload, receipt)
        stored = review_store(config)["operations"].get(operation)
        if stored:
            if stored["binding"] != binding:
                raise WorkflowError(
                    "operation_id_reused", "Acknowledgement belongs to another request."
                )
            return _current_receipt(
                config, payload, finish(config, operation, binding, stored["receipt"])
            )
        ledger = open_ledger(config)
        committed = next(
            (r for r in ledger.query(kind="decision") if r.operation_id == operation), None
        )
        if committed:
            if committed.binding != binding:
                raise WorkflowError("operation_id_reused", "Decision belongs to another request.")
            receipt = {
                "ok": True,
                "finding": committed.id,
                "evidence_revision": committed.evidence_revision,
                "operation_id": operation,
                "message": "Decision recorded.",
            }
            _ack(
                config,
                committed.run,
                {"revision": committed.evidence_revision, "sources": committed.sources},
                operation,
                binding,
                committed.note,
                receipt,
            )
            return _current_receipt(config, payload, finish(config, operation, binding, receipt))
        kind, spec, out = resolve(config, str(payload.get("run", "")))
        data = view(config, spec, kind, out)
        evidence = read_evidence(spec, kind, out)
        if payload.get("evidence_revision") != evidence["revision"]:
            raise WorkflowError(
                "evidence_changed",
                "Evidence changed. Review the current evidence before saving.",
                evidence_revision=evidence["revision"],
                conflict=True,
            )
        if action not in ("review", "decide"):
            raise WorkflowError("unknown_action", "Unknown workflow action.", 404)
        if active(config, spec, out):
            raise WorkflowError(
                "execution_active",
                "This run is running or starting. Wait before recording evidence.",
            )
        choices = payload.get("choices")
        if action == "decide":
            if choices is None and "selected" not in payload:
                from rl_researcher.artefacts.state import _decision_region, ticked, options

                body = _decision_region(out / "README.md")
                choices = ticked(body)
                if not choices:
                    raise WorkflowError(
                        "missing_choices",
                        "no box is ticked. A decision has to be made by a person.",
                        ok=False,
                        message="no box is ticked. A decision has to be made by a person.",
                        options=options(body),
                    )
            if (
                choices is None
                and payload.get("selected") == [0]
                and not evidence["policy"]["choices"]
            ):
                from rl_researcher.artefacts.state import _decision_region, options

                old_options = options(_decision_region(out / "README.md"))
                if old_options and all(acknowledgement(c) for c in old_options):
                    choices = old_options
            if choices is None:
                selected = payload.get("selected")
                offered = evidence["policy"]["choices"]
                if (
                    not isinstance(selected, list)
                    or not selected
                    or any(type(i) is not int or i < 0 or i >= len(offered) for i in selected)
                    or len(set(selected)) != len(selected)
                ):
                    raise WorkflowError(
                        "invalid_choices", "Select at least one offered research choice.", 400
                    )
                choices = [offered[i] for i in sorted(selected)]
            if (
                not isinstance(choices, list)
                or not choices
                or any(not isinstance(c, str) for c in choices)
            ):
                raise WorkflowError("invalid_choices", "Select offered research choices.", 400)
            acknowledgements = [acknowledgement(c) for c in choices]
            if any(acknowledgements):
                if not all(acknowledgements):
                    raise WorkflowError(
                        "mixed_acknowledgement",
                        "Submit acknowledgement separately from research choices.",
                        400,
                    )
                action = "review"
            elif any(c not in evidence["policy"]["choices"] for c in choices):
                raise WorkflowError(
                    "invalid_choices", "The research choices changed. Refresh the report.", 400
                )
        cap = data["capabilities"][action]
        if not cap["enabled"]:
            raise WorkflowError(cap["reason_code"], cap["reason"], next_step=cap["next_step"])
        if not isinstance(payload.get("note", ""), str):
            raise WorkflowError("invalid_note", "The note must be text.", 400)
        note = payload.get("note", "").strip()
        if action == "decide" and not note:
            raise WorkflowError(
                "note_required", "Explain your decision before recording it.", 400, field="note"
            )
        _before_commit()
        if active(config, spec, out):
            raise WorkflowError(
                "execution_active", "Execution started. Review again after it stops."
            )
        if read_evidence(spec, kind, out)["revision"] != evidence["revision"]:
            raise WorkflowError(
                "evidence_changed", "Evidence changed while saving. Review it again.", conflict=True
            )
        receipt = {
            "ok": True,
            "operation_id": operation,
            "evidence_revision": evidence["revision"],
            "message": "Evidence reviewed.",
        }
        if action == "decide":
            existing = data["decision"]
            if existing and (existing["choices"] != choices or existing["note"] != note):
                raise WorkflowError(
                    "decision_recorded", "A decision is already recorded for this evidence."
                )
            summary = evidence["summary"]
            previous = data["decisions"][-1:] if not existing else []
            row = ledger.add(
                Finding(
                    kind="decision",
                    run=spec.name,
                    date=stamp_now(),
                    choices=list(choices or []),
                    note=note,
                    evidence_revision=evidence["revision"],
                    operation_id=operation,
                    binding=binding,
                    sources=evidence["sources"],
                    actor=actor(config),
                    fingerprint=str(summary.get("fingerprint", "")),
                    commit=str(summary.get("git_sha", ""))[:12],
                    supersedes=[r["id"] for r in previous],
                    via=str(payload.get("via") or "dashboard"),
                    artefact=(out / "README.md").relative_to(config.root).as_posix(),
                )
            )
            receipt.update(finding=row.id, message="Decision recorded.")
        _ack(config, spec.name, evidence, operation, binding, note, receipt)
        return _current_receipt(config, payload, finish(config, operation, binding, receipt))


def _current_receipt(config: Any, payload: dict, receipt: dict) -> dict:
    try:
        kind, spec, out = resolve(config, str(payload.get("run", "")))
        current = read_evidence(spec, kind, out)["revision"]
        return {
            **receipt,
            "applicability": applicability(receipt.get("evidence_revision", ""), current),
        }
    except Exception:
        return {**receipt, "applicability": "Current evidence unavailable"}


def decorate(config: Any, result: Any) -> None:
    """Augment state without admitting output folders or archived specs to discovery."""
    from rl_researcher.research_queue import snapshot

    queue = snapshot(config)
    result.queue_revision = queue["revision"]
    result.queued = queue["entries"]
    result.on_hold = [dict(e) for e in result.queued if e.get("hold")]
    if queue["error"]:
        result.health["queue_error"] = queue["error"]
    catalog = []
    for row in discovery(config):
        item = {k: v for k, v in row.items() if not k.startswith("_")}
        if row["valid"]:
            root = config.root.resolve()
            item["source_files"] = [
                p.relative_to(root).as_posix()
                for p in (
                    row["_out"].resolve() / "README.md",
                    row["_out"].resolve() / "results.json",
                )
                if p.is_relative_to(root) and p.is_file()
            ]
            try:
                data = view(config, row["_spec"], row["_kind"], row["_out"])
                item.update(data)
                from rl_researcher.status import run_status

                status = run_status(row["_spec"], row["_kind"], row["_out"])
                item["state"] = status.state
            except Exception as exc:
                item.update(
                    diagnostic=str(exc),
                    reason_code="evidence_unreadable",
                    next_step="Open the specification and output files; repair the reported error, then retry.",
                )
        if item.get("diagnostic"):
            item["capabilities"] = {
                action: capability(
                    False, item["reason_code"], item["diagnostic"], item["next_step"]
                )
                for action in ("run", "review", "decide", "report")
            }
        catalog.append(item)
    result.catalog = catalog
    result.running = [r for r in result.running if r.get("state") != "historical"]
    names = {r["run"]: r for r in catalog}
    for row in [*result.queued, *result.on_hold]:
        target = names.get(row["run"])
        row["diagnostic"] = (
            target.get("diagnostic", "")
            if target
            else "No active registration matches this queue target."
        )
    # Keep the old serialized waiting shape, but its membership is now contractual.
    from rl_researcher.artefacts.state import Waiting

    old = {w.run: w for w in result.waiting}
    result.waiting = []
    for row in catalog:
        if row.get("review_required") or row.get("decision_pending"):
            waiting = old.get(row["run"]) or Waiting(
                run=row["run"], kind=row.get("kind", ""), artefact=""
            )
            waiting.review_required = row.get("review_required", False)
            waiting.decision_required = row.get("decision_pending", False)
            waiting.options = row.get("options", [])
            result.waiting.append(waiting)
    # Historical decisions are retained even if their registrations have been archived.
    revised = {d["id"]: d for row in catalog for d in row.get("decisions", [])}
    for row in result.decided:
        row.update(revised.get(row.get("id"), {"applicability": "Evidence revision unknown"}))
