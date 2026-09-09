"""Read-only evidence identity and explicit research decision policy."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rl_researcher.spec import load_toml, spec_fingerprint
from rl_researcher.workflow_store import digest


def acknowledgement(label: str) -> bool:
    label = re.split(r"\s+[—–-]\s+", label, maxsplit=1)[0]
    return re.sub(r"[*_`]", "", label).strip().casefold() == "reviewed"


@dataclass
class DecisionPolicy:
    decision_required: bool
    choices: list[str] = field(default_factory=list)
    source: str = "default"
    explanation: str = ""


def policy(spec: Any, kind: Any, text: str) -> DecisionPolicy:
    from rl_researcher.artefacts.state import options, section
    from rl_researcher.regions import find

    body = section(text, "Decision")
    try:
        body = next(
            (r.body for r in find(text) if r.kind == "authored" and r.arg.startswith("decision")),
            body,
        )
    except ValueError:
        pass
    offered = options(body)
    choices = [o for o in offered if not acknowledgement(o)]
    source_path = getattr(spec, "source_path", None)
    raw = load_toml(source_path) if source_path else {}
    metadata = raw.get("workflow", {})
    if not isinstance(metadata, dict):
        raise ValueError("workflow must be a table")
    if "decision_required" in metadata:
        if not isinstance(metadata["decision_required"], bool):
            raise ValueError("workflow.decision_required must be true or false")
        return DecisionPolicy(
            metadata["decision_required"],
            choices,
            "specification",
            "Decision requirement is explicitly registered.",
        )
    hook = getattr(kind, "decision_policy", None)
    declared = hook(spec) if callable(hook) else None
    if declared is not None:
        if isinstance(declared, DecisionPolicy):
            declared = asdict(declared)
        if not isinstance(declared, dict) or not isinstance(
            declared.get("decision_required"), bool
        ):
            raise ValueError("decision_policy must declare a boolean decision_required")
        chosen = declared.get("choices", choices)
        if not isinstance(chosen, list) or any(not isinstance(c, str) for c in chosen):
            raise ValueError("decision_policy choices must be strings")
        return DecisionPolicy(
            declared["decision_required"],
            [c for c in chosen if not acknowledgement(c)],
            "adapter",
            str(declared.get("explanation", "Adapter decision policy.")),
        )
    if offered and all(acknowledgement(o) for o in offered):
        return DecisionPolicy(
            False, [], "legacy-reviewed", "This report requests acknowledgement only."
        )
    required = getattr(kind, "artefact_kind", "report") != "measurement"
    return DecisionPolicy(
        required,
        choices,
        "default",
        "Research decision required." if required else "Measurement acknowledgement only.",
    )


def historical(spec: Any, kind: Any, out: Path) -> dict:
    """Called only without standard execution evidence. Never fabricates units."""
    paths = [p for p in (out / "results.json", out / "README.md") if p.is_file()]
    result: dict = {
        "completion": "unknown",
        "summary": {},
        "sources": paths,
        "explanation": "Historical evidence — completion unknown.",
    }
    hook = getattr(kind, "historical_evidence", None)
    declared = hook(spec, out) if callable(hook) else None
    if declared is not None:
        if not isinstance(declared, dict) or declared.get("completion") not in (
            "complete",
            "incomplete",
            "unknown",
        ):
            raise ValueError(
                "historical_evidence requires complete, incomplete, or unknown completion"
            )
        sources = []
        for name in declared.get("sources", []):
            p = Path(name)
            p = (p if p.is_absolute() else out / p).resolve()
            if not p.is_relative_to(out.resolve()) or not p.is_file():
                raise ValueError(
                    "Historical evidence sources must be readable files inside the output directory"
                )
            p.read_bytes()
            sources.append(p)
        if not sources:
            raise ValueError("Historical evidence requires source files")
        if not isinstance(declared.get("summary", {}), dict):
            raise ValueError("Historical summary must be an object")
        result.update(declared, sources=sources)
        if not declared.get("explanation") and result["completion"] == "complete":
            result["explanation"] = "Historical completion verified by the adapter."
    elif (out / "results.json").is_file():
        result["summary"] = json.loads((out / "results.json").read_text(encoding="utf-8"))
    return result


def _canonical(value: Any) -> Any:
    # Only generated bookkeeping timestamps, never measurement timestamps.
    if isinstance(value, dict):
        return {
            k: _canonical(v) for k, v in value.items() if k not in ("generated", "generated_at")
        }
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def read_evidence(spec: Any, kind: Any, out: Path, status: Any = None) -> dict:
    from rl_researcher.artefacts.state import section
    from rl_researcher.regions import find
    from rl_researcher.status import run_status
    from rl_researcher.units import unit_dir

    status = status or run_status(spec, kind, out)
    report = out / "README.md"
    text = report.read_text(encoding="utf-8") if report.is_file() else ""
    contract = policy(spec, kind, text)
    reading = section(text, "Reading") or ""
    try:
        reading = next(
            (r.body for r in find(text) if r.kind == "authored" and r.arg == "reading"), reading
        )
    except ValueError:
        pass
    reading = re.sub(r"<!--.*?-->", "", reading, flags=re.S).strip()
    paths = [
        unit_dir(out, u.unit) / "results.json"
        for u in status.units
        if (unit_dir(out, u.unit) / "results.json").is_file()
    ]
    summary: dict = {}
    if (out / "results.json").is_file():
        paths.append(out / "results.json")
        summary = json.loads((out / "results.json").read_text(encoding="utf-8"))
        if not isinstance(summary, dict):
            raise ValueError("Result summary must be a JSON object")
    completion = (
        "complete"
        if status.finished
        and not any((u.result or {}).get("status") == "incomplete" for u in status.units)
        else "incomplete"
    )
    explanation = ""
    if getattr(status, "historical", False) or (
        status.state == "not started"
        and not status.lock
        and not any(u.progress or u.resumable or u.result for u in status.units)
    ):
        old = historical(spec, kind, out)
        paths.extend(old["sources"])
        summary = old["summary"] or summary
        completion, explanation = old["completion"], old["explanation"]
    manifest = []
    for p in sorted(set(p.resolve() for p in paths)):
        raw = p.read_bytes()
        if p.suffix == ".json":
            source_digest = digest(_canonical(json.loads(raw)))
        elif p == report.resolve():
            # A generated report must not invalidate itself on every regeneration.
            stable = text
            for region in reversed(find(text)):
                if region.kind == "authored" and region.arg.startswith("decision"):
                    stable = stable[: region.start] + stable[region.end :]
            stable = re.sub(r"<!--.*?-->", "", stable, flags=re.S)
            stable = re.sub(r"(?ms)^## Decision.*?(?=^## |\Z)", "", stable)
            stable = re.sub(r"(?mi)^.*generated(?: at|=|:).*$", "", stable)
            source_digest = digest(stable.strip())
        else:
            source_digest = hashlib.sha256(raw).hexdigest()
        manifest.append({"path": p.relative_to(out.resolve()).as_posix(), "digest": source_digest})
    available = bool(paths or text.strip())
    revision = digest(
        {
            "spec": spec_fingerprint(spec),
            "sources": manifest,
            "summary": _canonical(summary),
            "reading": reading,
            "policy": {"required": contract.decision_required, "choices": contract.choices},
        }
    )
    return {
        "available": available,
        "completion": completion,
        "explanation": explanation,
        "revision": revision,
        "sources": manifest,
        "summary": summary,
        "policy": asdict(contract),
        "report": report.is_file(),
    }
