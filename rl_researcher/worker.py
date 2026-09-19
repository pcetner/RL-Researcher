"""Internal worker. Not a public command interface."""

import json
import sys
import threading
import time
import traceback
import inspect
from multiprocessing.connection import Client
from pathlib import Path
from . import atomic, channel, checkpoint, experiment, runlog
from .stop import StopRequested, BudgetExpired


class Context:
    def __init__(self, directory, connection, manifest, attempt):
        self.directory, self.connection, self.manifest, self.attempt = (
            directory,
            connection,
            manifest,
            attempt,
        )
        self.root = Path(manifest["root"])
        self.source = directory / "source" / "project"
        self.inputs = {k: Path(v["path"]) for k, v in manifest["inputs"].items()}
        self.definition = manifest["definition"]
        self.trial = None
        self.deadline = manifest["deadline"]
        self.mutex = threading.Lock()
        self.shutdown = threading.Event()
        self.work = directory / "finalization"
        self.work.mkdir(exist_ok=True)
        self.last_sample = 0
        self.phase_name = "Preparing"
        self.channel_error = None

    def send(self, kind, **data):
        with self.mutex:
            channel.send(self.connection, {"kind": kind, "data": data})
            response = channel.receive(self.connection)
            if "error" in response:
                raise OSError(response["error"])
            return response.get("value")

    def heartbeat(self):
        while not self.shutdown.wait(5):
            try:
                self.send("heartbeat")
            except Exception as e:
                self.channel_error = e
                return

    def check(self):
        if self.channel_error:
            raise OSError("Required evidence channel failed") from self.channel_error
        if time.time() >= self.deadline:
            raise BudgetExpired("Original execution or trial deadline reached")
        if (self.directory / "stop-request.json").exists():
            raise StopRequested("Safe stop requested")

    @property
    def stop_requested(self):
        return (self.directory / "stop-request.json").exists()

    def phase(self, phase):
        if phase != self.phase_name:
            self.phase_name = phase
            self.send("phase", phase=phase)

    def progress(self, decision):
        self.send("progress", trial=self.trial["id"], decision=decision, attempt=self.attempt)

    def message(self, text, level="info"):
        self.send("message", text=text, level=level, trial=self.trial["id"] if self.trial else None)

    def publish_artifact(self, source, label, media_type="application/octet-stream"):
        return self.send(
            "publish_artifact",
            source=str(source),
            label=label,
            media_type=media_type,
            trial=self.trial["id"] if self.trial else None,
            attempt=self.attempt,
        )

    def publish_checkpoint(self, source, decision):
        return self.send(
            "publish_checkpoint", source=str(source), trial=self.trial["id"], decision=decision
        )

    def sample(self, decision, metrics, preview=None, details=None, force=False):
        now = time.time()
        if not force and now - self.last_sample < 5:
            return
        self.send(
            "sample",
            trial=self.trial["id"],
            attempt=self.attempt,
            phase=self.phase_name,
            decision=decision,
            metrics=metrics,
            preview=preview,
            details=details or {},
            captured=now,
        )
        self.last_sample = now


def run(directory, port, token):
    directory = Path(directory)
    manifest = atomic.read_json(directory / "manifest.json")
    state = runlog.project(manifest, runlog.events(directory))
    source = directory / "source" / "project"
    for rel in manifest["definition"].get("python_paths", []):
        sys.path.insert(0, str(source / rel))
    if port == 'stdio':
        conn = channel.Stream(sys.stdin.buffer, sys.stdout.buffer)
        sys.stdout = sys.stderr  # Project diagnostics must not corrupt the evidence pipe.
    else:
        conn = Client(("127.0.0.1", int(port)), authkey=bytes.fromhex(token))
    ctx = Context(directory, conn, manifest, state["attempt"])

    overall_seconds = manifest["definition"]["limits"]["overall_seconds"]
    remaining_overall = max(0.0, overall_seconds - state.get("spent_seconds", 0.0))
    overall_deadline = time.time() + remaining_overall
    if manifest.get('authorization'):
        overall_deadline = min(overall_deadline, atomic.read_json(directory / 'authorization.json')['deadline'])
    ctx.deadline = overall_deadline

    thread = threading.Thread(target=ctx.heartbeat, daemon=True)
    thread.start()
    try:
        execute = experiment.entrypoint(source, manifest["definition"]["executor"])
        for trial in manifest["definition"]["trials"]:
            saved = state["trials"][trial["id"]]
            if saved["state"] == "Completed":
                continue
            ctx.deadline = overall_deadline
            ctx.check()
            ctx.trial = trial
            trial_remaining = max(0.0, trial["seconds"] - saved.get("spent_seconds", 0.0))
            ctx.deadline = min(overall_deadline, time.time() + trial_remaining)
            ctx.work = directory / "trials" / trial["id"] / "work"
            ctx.work.mkdir(parents=True, exist_ok=True)
            restored = (
                checkpoint.validate(directory, manifest, saved["checkpoint"])
                if saved["checkpoint"]
                else None
            )
            ctx.send("trial", id=trial["id"], deadline=ctx.deadline, recovered=saved["checkpoint"])
            ctx.last_sample = 0
            ctx.phase_name = ""
            result = execute(ctx, trial, restored)
            ctx.check()
            ctx.send("result", trial=trial["id"], result=result)
        ctx.trial = None
        ctx.deadline = overall_deadline
        ctx.work = directory / "finalization"
        ctx.work.mkdir(exist_ok=True)
        ctx.check()
        ctx.phase("Finalizing analysis")
        results = {
            t["id"]: atomic.read_json(directory / "trials" / t["id"] / "result.json")
            for t in manifest["definition"]["trials"]
        }
        if manifest["definition"].get("finalizer"):
            experiment.entrypoint(source, manifest["definition"]["finalizer"])(ctx, results)
        ctx.check()
        ctx.send("finished", state="Completed", phase="Completed", results=results)
    except StopRequested as e:
        ctx.send("finished", state="Stopped", phase=str(e))
    except BudgetExpired as e:
        ctx.send("finished", state="Incomplete", phase=str(e))
    except BaseException:
        error = traceback.format_exc()
        print(error, file=sys.stderr, flush=True)
        try:
            ctx.send("finished", state="Failed", phase=ctx.phase_name, error=error)
        except Exception:
            pass
        return 1
    finally:
        ctx.shutdown.set()
        conn.close()
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "--resolve":
        root, identifier = Path(sys.argv[2]), sys.argv[3]
        d = experiment.inspect(root, identifier)["definition"]
        for rel in d.get("python_paths", []):
            sys.path.insert(0, str(root / rel))
        for field, arity in [("resolver", 1), ("executor", 3), ("finalizer", 2)]:
            if d.get(field):
                inspect.signature(experiment.entrypoint(root, d[field])).bind(*([None] * arity))
        # Resolvers must be cheap and must not initialize the experiment.
        print(json.dumps(experiment.entrypoint(root, d["resolver"])(d)))
    else:
        sys.exit(run(*sys.argv[1:]))
