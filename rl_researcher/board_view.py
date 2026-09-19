"""Read recorded execution evidence; never infer scientific outcomes."""

import time
import statistics
from pathlib import Path
from . import atomic, config, experiment, runlog


def catalog(root):
    entries = []
    for entry in config.load(root)["experiments"]:
        try:
            resolved = experiment.inspect(root, entry["id"])
            cached = Path(root) / ".research" / "validation" / (entry["id"] + ".json")
            valid = cached.exists() and atomic.read_json(cached)["revision"] == resolved["revision"]
            entries.append(
                dict(
                    entry,
                    definition=resolved["definition"],
                    revision=resolved["revision"],
                    validation="Ready" if valid else "Validation required",
                )
            )
        except Exception as e:
            entries.append(dict(entry, validation="Unavailable", error=str(e)))
    executions = []
    for directory in config.store(root).iterdir():
        if not (directory / "manifest.json").exists():
            continue
        manifest = atomic.read_json(directory / "manifest.json")
        s = runlog.project(manifest, runlog.events(directory))
        executions.append(
            {
                "id": directory.name,
                "name": manifest["definition"]["name"],
                "question": manifest["definition"]["question"],
                "state": s["state"],
                "updated": s["updated"],
                "completed": sum(t["state"] == "Completed" for t in s["trials"].values()),
                "total": len(s["trials"]),
            }
        )
    executions.sort(
        key=lambda e: (
            e["state"] not in ("Running", "Stopping"),
            -e["updated"],
        )
    )
    return {"experiments": entries, "executions": executions, "time": time.time()}


def execution(directory, after=0):
    manifest = atomic.read_json(directory / "manifest.json")
    rows = runlog.events(directory)
    state = runlog.project(manifest, rows)
    now = time.time()
    state["unresponsive"] = (
        state["state"] in ("Running", "Stopping")
        and now - (state["heartbeat"] or manifest["started"]) > 30
    )
    reason = (
        experiment.resume_reason(directory, manifest, state)
        if state["state"] not in ("Running", "Stopping", "Completed")
        else "Execution is " + state["state"]
    )
    return {
        "manifest": manifest,
        "state": state,
        "events": [e for e in rows if e["seq"] > after],
        "resume_reason": reason,
        "time": now,
        "progress": study_progress(manifest, state, rows),
        "preview_paths": list(preview_window(rows)),
        "force_available": bool(
            state.get("stop_requested")
            and now - state["stop_requested"] >= 60
            and state["state"] == "Stopping"
        ),
    }


def preview_window(rows, count=12):
    """Bound playback to recent captures per trial, never mix attempts."""
    groups = {}
    for e in rows:
        d = e["data"]
        if e["kind"] == "sample" and d.get("preview"):
            group = groups.setdefault(d["trial"], [])
            if group and group[-1]["attempt"] != d["attempt"]:
                group.clear()
            group.append(d)
            del group[:-count]
    return {s["preview"]["path"] for group in groups.values() for s in group}


def prune_previews(directory, now=None):
    """Remove only old, explicitly labeled live-preview copies, not domain evidence.

    Keep twelve captures per trial plus a two-minute publication grace. Paths
    shared with durable artifacts are protected. Journals retain capture metadata.
    """
    now = time.time() if now is None else now
    rows = runlog.events(directory)
    manifest = atomic.read_json(directory / "manifest.json")
    state = runlog.project(manifest, rows)
    # Frozen workers can republish identical content. Never race publication.
    if state["state"] in ("Running", "Stopping"):
        return
    keep = preview_window(rows)
    candidates = set()
    for e in rows:
        if e["kind"] != "artifact":
            continue
        d = e["data"]
        if not d.get("label", "").startswith("Live preview") or now - e["time"] < 120:
            keep.add(d["path"])
        else:
            candidates.add(d["path"])
    base = (directory / "artifacts").resolve()
    for relative in candidates - keep:
        path = (directory / relative).resolve()
        if path.parent == base and path.is_file() and now - path.stat().st_mtime >= 120:
            path.unlink(missing_ok=True)


def study_progress(manifest, state, rows):
    trials = manifest["definition"]["trials"]
    completed = sum(t["state"] == "Completed" for t in state["trials"].values())
    total_work = sum(t.get("decisions", 0) for t in trials)
    done = sum(min(t.get("decisions", 0), state["trials"][t["id"]]["progress"]) for t in trials)
    starts, durations = {}, []
    for e in rows:
        if e["kind"] == "trial":
            starts[e["data"]["id"]] = e["time"]
        elif e["kind"] == "result" and e["data"]["trial"] in starts:
            durations.append(e["time"] - starts[e["data"]["trial"]])
    eta = None
    if durations and state["state"] == "Running" and not state.get("unresponsive"):
        # End-to-end completed-trial durations include loading and checkpointing.
        typical = statistics.median(durations)
        remaining = len(trials) - completed
        current = state.get("trial")
        fraction = 0
        if current and state["trials"][current]["state"] != "Completed":
            definition = next(t for t in trials if t["id"] == current)
            fraction = min(
                1, state["trials"][current]["progress"] / max(1, definition.get("decisions", 1))
            )
        if remaining:
            eta = max(0, (remaining - fraction) * typical)
    return {
        "completed": completed,
        "total": len(trials),
        "done": done,
        "total_work": total_work,
        "eta_seconds": eta,
    }


def samples(directory, trial, attempt=None):
    return [
        dict(e["data"], time=e["time"], seq=e["seq"])
        for e in runlog.events(directory)
        if e["kind"] == "sample"
        and e["data"]["trial"] == trial
        and (attempt is None or e["data"]["attempt"] == attempt)
    ]
