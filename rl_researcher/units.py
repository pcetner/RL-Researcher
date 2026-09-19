STATES = ("Ready", "Running", "Stopping", "Stopped", "Completed", "Incomplete", "Failed")
ACTIVE = ("Running", "Stopping")


def initial(trials):
    return {
        t["id"]: {"state": "Pending", "label": t["label"], "progress": 0, "checkpoint": None}
        for t in trials
    }
