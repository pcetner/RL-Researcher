"""Presentation data for the interactive workspace. Files remain authoritative."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict

from rl_researcher.artefacts.dashboard import collect, section, sections_of
from rl_researcher.artefacts.overview import research_summary
from rl_researcher.ledger import open_ledger
from rl_researcher.render import editable_article
from rl_researcher.units import LIVE_STATUSES, STALE_FACTOR


def revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def overview(config: Any, spec: Any, kind: Any, out: Path, text: str) -> Dict[str, Any]:
    data = collect(spec, kind, out, stale_factor=float(config.watcher.stale_factor))
    summary = data.summary or {"runs": [u.result for u in data.units if u.result]}
    incomplete = [u.unit for u in data.units if (u.result or {}).get("status") == "incomplete"]
    provisional = data.status.state != "finished" or bool(incomplete)
    try:
        research = research_summary(spec, summary, provisional=provisional,
                                     registry=getattr(kind, "registry", None))
    except (AttributeError, KeyError, TypeError, ValueError):
        research = {"headline": "Open Results for this run's findings.",
                    "provisional": provisional, "rows": [], "markdown": ""}
    decisions = open_ledger(config).query(kind="decision", run=spec.name)
    fingerprint = str(summary.get("fingerprint", ""))
    decisions = [r for r in decisions if r.fingerprint == fingerprint
                 and r.commit == str(summary.get("git_sha", ""))[:12]]
    alerts = [{"unit": u.unit, "message": u.error or "Heartbeat is stale."}
              for u in data.units if u.failed or u.stale]
    alerts += [{"unit": unit, "message": "Incomplete result."} for unit in incomplete]
    from rl_researcher.presentation import hypothesis_view
    return {**hypothesis_view(config.root, spec.name, getattr(spec, "hypothesis", "")),
            "question": getattr(spec, "hypothesis", ""), "revision": revision(text),
            "state": data.status.state, "done": data.status.done, "total": len(data.units),
            "research": research, "alerts": alerts,
            "progress": {"done": data.done_steps, "total": data.total_steps,
                         "eta_seconds": data.eta_all, "heartbeat_seconds": data.heartbeat,
                         "compute_seconds": data.elapsed_all},
            "decision": ({"id": decisions[-1].id, "note": decisions[-1].note,
                          "choices": decisions[-1].choices}
                         if decisions else None),
            "resumable": any(u.resumable and not u.done for u in data.units),
            "recovery": recovery(data.units, kind=kind, spec=spec, out=out)}


def recovery(units: Any, *, kind: Any = None, spec: Any = None, out: Any = None) -> Dict[str, Any]:
    """A sidecar records saved progress, not proof that an adapter can load it."""
    pending = [u for u in units if not u.done and (u.failed or u.stale or u.status in ("stopped", "incomplete"))]
    evidence = [{"unit": u.unit, "step": u.checkpoint_step,
                 "reason": (f"Saved progress at step {u.checkpoint_step}; checkpoint compatibility has not been verified."
                            if u.checkpoint_step is not None else "No saved checkpoint is reported; a restart may be needed.")}
                for u in pending]
    inspect = getattr(kind, "recovery_status", None)
    for item in evidence:
        item["status"] = "Unknown"
        if callable(inspect):
            try:
                result = inspect(spec, item["unit"], out)
                if result.get("status") in ("Yes", "No", "Unknown") and result.get("reason"):
                    item.update(status=result["status"], reason=str(result["reason"]))
            except Exception:  # noqa: BLE001 - failed inspection is not failed recovery
                item["reason"] = "Checkpoint inspection failed; recovery has not been verified."
    statuses = {item["status"] for item in evidence}
    status = next(iter(statuses)) if len(statuses) == 1 else "Unknown"
    verified = sum(item["status"] == "Yes" for item in evidence)
    return {"status": status if pending else "Not needed",
            "reason": (("Recovery has not been verified." if verified == 0 and status == "Unknown"
                        else f"{verified} of {len(pending)} interrupted units verified recoverable.")
                       if pending else "No interrupted units."), "units": evidence}



def content(spec: Any, kind: Any, out: Path, view: str, *,
            stale_factor: float = STALE_FACTOR) -> Dict[str, str]:
    """Heavy evidence is loaded only when requested. Custom panels keep their blocks."""
    md = out / "README.md"
    text = md.read_text(encoding="utf-8") if md.is_file() else ""
    if view == "results":
        # A complete report remains available through its file link. In the workspace,
        # technical sections use disclosures and the decision has one dedicated editor.
        pieces = re.split(r"(?m)(?=^## )", text)
        fragments = []
        for piece in pieces:
            title = re.match(r"## (.+)", piece)
            if title is None:
                continue
            label = title.group(1).strip()
            if label.startswith("Decision"):
                continue
            body = editable_article(piece, kind=getattr(kind, "artefact_kind", "report"),
                                    run=spec.name, embed_images_from=out)
            if label in ("Units", "Provenance", "Ledger", "Method", "Summary", "Registered metrics"):
                import html
                body = f'<details><summary>{html.escape(label)}</summary>{body}</details>'
            fragments.append(body)
        if not fragments and text:
            fragments.append(editable_article(text, kind=getattr(kind, "artefact_kind", "report"),
                                              run=spec.name, embed_images_from=out))
        extra = content(spec, kind, out, "extras", stale_factor=stale_factor)
        return {"html": "\n".join(fragments) + extra["html"], "css": extra["css"],
                "revision": revision(text)}
    data = collect(spec, kind, out, stale_factor=stale_factor)
    names = (["running", "failed", "queued", "finished"] if view == "units" else
             [n for n in sections_of(kind) if n not in
              ("headline", "metrics", "arms", "running", "failed", "queued", "finished", "log")])
    blocks = [b for name in names for b in section(name, spec, kind, data)]
    # Keep live curves available without embedding a second page shell.
    if view == "progress":
        from rl_researcher.blocks.live import LiveUnits
        from rl_researcher.blocks.disclosure import Disclosure, LiveTrends
        live = section("running", spec, kind, data)
        blocks = [LiveTrends(units=[u for u in b.units if u.state in LIVE_STATUSES]) if isinstance(b, LiveUnits) else
                  Disclosure(title="Live detail", blocks=[b]) for b in live] + blocks
    from rl_researcher import charts, plotstyle
    defs = charts.seed_defs([(plotstyle.variant_color(u.arm, data.order), int(u.seed))
                             for u in data.units] + [(charts.SEED_KEY_COLOUR, 1)])
    return {"html": defs + "".join(b.html() for b in blocks),
            "css": "".join(dict.fromkeys(b.css for b in blocks)) + str(getattr(kind, "page_css", "")),
            "revision": revision(text)}
