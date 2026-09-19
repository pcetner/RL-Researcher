import json
import os
import time
from . import atomic
from .units import initial


def events(directory, after=0):
    path = directory / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            break  # a crash may leave one uncommitted tail
        if row["seq"] > after:
            rows.append(row)
    return rows


def project(manifest, rows):
    state = {
        "id": manifest["id"],
        "state": "Running",
        "phase": "Starting supervisor",
        "cursor": 0,
        "attempt": 0,
        "trial": None,
        "trials": initial(manifest["definition"]["trials"]),
        "updated": manifest["started"],
        "heartbeat": None,
        "sample": None,
        "artifacts": [],
        "error": None,
        "spent_seconds": 0.0,
        "active_since": manifest["started"],
    }
    for t in state["trials"].values():
        t["spent_seconds"] = 0.0
        t["active_since"] = None
    for e in rows:
        if state["state"] == "Running" and state["active_since"] is not None:
            elapsed = min(e["time"] - state["active_since"], 60.0)
            state["spent_seconds"] += elapsed
            state["active_since"] = e["time"]
            if state["trial"]:
                t = state["trials"].get(state["trial"])
                if t and t["state"] == "Running" and t.get("active_since") is not None:
                    t_elapsed = min(e["time"] - t["active_since"], 60.0)
                    t["spent_seconds"] += t_elapsed
                    t["active_since"] = e["time"]

        state["cursor"], state["updated"] = e["seq"], e["time"]
        d, k = e["data"], e["kind"]
        if k == "attempt":
            state.update(
                attempt=d["attempt"],
                state="Running",
                error=None,
                sample=None,
                stop_requested=None,
                worker_exited=False,
                resource_fault=None,
            )
            state["active_since"] = e["time"]
            if state["trial"] and state["trial"] in state["trials"]:
                 state["trials"][state["trial"]]["active_since"] = e["time"]
        elif k == "state":
            state.update(d)
            if d.get("state") in ("Stopped", "Incomplete", "Failed"):
                state["active_since"] = None
                for trial in state["trials"].values():
                    if trial["state"] == "Running":
                        trial["state"] = d["state"]
                        trial["active_since"] = None
        elif k == "trial":
            state.update(trial=d["id"], sample=None, phase="Preparing trial")
            t = state["trials"][d["id"]]
            t.update(state="Running", deadline=d["deadline"])
            t["active_since"] = e["time"]
        elif k == "phase":
            state["phase"] = d["phase"]
        elif k == "heartbeat":
            state["heartbeat"] = e["time"]
        elif k in ("sample", "progress"):
            if d.get("trial") in state["trials"]:
                state["trials"][d["trial"]]["progress"] = d["decision"]
            if k == "sample":
                state["sample"] = dict(d, time=e["time"])
        elif k == "checkpoint_quarantined":
            state["trials"][d["trial"]]["checkpoint"] = None
        elif k == "checkpoint":
            state["trials"][d["trial"]]["checkpoint"] = d
        elif k == "result":
            state["trials"][d["trial"]].update(state="Completed", result=d["result"])
            state["trials"][d["trial"]]["active_since"] = None
        elif k == "artifact":
            state["artifacts"].append(d)
    return state


class Journal:
    def __init__(self, directory):
        self.directory = directory
        self.manifest = atomic.read_json(directory / "manifest.json")
        self.rows = events(directory)
        # Truncate only an incomplete final record before appending after a crash.
        atomic.write_text(
            directory / "events.jsonl", "".join(json.dumps(e) + "\n" for e in self.rows)
        )

    def append(self, kind, data):
        row = {"seq": len(self.rows) + 1, "time": time.time(), "kind": kind, "data": data}
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, allow_nan=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.rows.append(row)
        atomic.write_json(self.directory / "state.json", project(self.manifest, self.rows))
        return row
