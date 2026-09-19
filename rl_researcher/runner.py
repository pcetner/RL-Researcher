"""Detached supervisor: the only event-journal writer and owner of a sequential worker."""

import os
import math
import subprocess
import queue
import sys
import threading
import time
import uuid
from multiprocessing.connection import Listener
from pathlib import Path
from . import atomic, channel, checkpoint, config, evidence, experiment, lock, resources, runlog
from .units import ACTIVE


def state(directory):
    return runlog.project(atomic.read_json(directory / "manifest.json"), runlog.events(directory))


def launch(directory):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(directory / "source" / "toolkit")
    for name in ("stop-request.json", "force-request.json"):
        (directory / name).unlink(missing_ok=True)
    with (directory / "supervisor.log").open("ab") as log:
        process = lock.spawn(
            [sys.executable, "-m", "rl_researcher.runner", str(directory)],
            cwd=directory,
            env=env,
            stdout=log,
            stderr=log,
        )
    atomic.write_json(directory / "supervisor.json", {"pid": process.pid, "started": time.time()})
    return process


def validate_authorization(authorization):
    if authorization is None:
        return
    deadline = authorization.get('deadline')
    if (not isinstance(deadline, (int, float)) or not math.isfinite(deadline)
            or deadline <= time.time() or not authorization.get('campaign') or not authorization.get('job')):
        raise ValueError('A current bounded campaign authorization is required')


def start(root, identifier, revision, request_id, isolation=None, authorization=None):
    root = Path(root)
    with lock.exclusive(root / ".research" / "operations.lock"):
        existing = []
        for d in config.store(root).iterdir():
            if not (d / "manifest.json").exists():
                continue
            m = atomic.read_json(d / "manifest.json")
            if m.get("request_id") == request_id:
                if (m.get("experiment") != identifier or m.get("revision") != revision
                        or m.get('isolation') != isolation or m.get('authorization') != authorization):
                    raise ValueError("Request ID already used with different experiment or revision")
                return d.name
            existing.append(d)
        validate_authorization(authorization)
        resources.preflight(root,isolation)
        if any(state(d)["state"] in ACTIVE for d in existing):
            raise ValueError("Another execution is already active")
        resolved = experiment.inspect(root, identifier)
        if resolved["revision"] != revision:
            raise ValueError("Definition changed. Inspect the updated setup and validate again.")
        validated = root / ".research" / "validation" / (identifier + ".json")
        if not validated.exists() or atomic.read_json(validated)["revision"] != revision:
            raise ValueError("Technical validation is required for this revision")
        for name, item in resolved["inputs"].items():
            if not Path(item["path"]).is_file() or atomic.digest(item["path"]) != item["sha256"]:
                raise ValueError("Input changed: " + name)
        identity = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        directory = config.store(root) / identity
        directory.mkdir()
        try:
            evidence.freeze(directory, resolved)
            if experiment.inspect(root, identifier)["revision"] != revision:
                raise ValueError("Definition changed during launch")
            now = time.time()
            manifest = dict(
                resolved,
                id=identity,
                experiment=identifier,
                request_id=request_id,
                started=now,
                deadline=now + resolved["definition"]["limits"]["overall_seconds"],
                isolation=isolation,
                authorization=authorization,
            )
            atomic.write_json(directory / "manifest.json", manifest)
            if authorization:
                atomic.write_json(directory / 'authorization.json', authorization)
            launch(directory)
        except Exception:
            if (directory / "manifest.json").exists():
                runlog.Journal(directory).append(
                    "state", {"state": "Failed", "error": "Supervisor launch failed"}
                )
            raise
        return identity


def resume(directory, authorization=None):
    m = atomic.read_json(directory / "manifest.json")
    resources.preflight(directory,m.get('isolation'))
    if m.get('authorization'):
        validate_authorization(authorization)
        if not authorization or any(authorization.get(k) != m['authorization'][k] for k in ('campaign', 'job')):
            raise ValueError('Recovery requires authorization for the original campaign and job')
    elif authorization is not None:
        raise ValueError('Cannot attach campaign authority to an unregistered historical execution')
    with lock.exclusive(Path(m["root"]) / ".research" / "operations.lock"):
        for other in config.store(m["root"]).iterdir():
            if (other / "manifest.json").exists() and state(other)["state"] in ACTIVE:
                raise ValueError("An execution is already active or starting")
        why = experiment.resume_reason(directory, m, state(directory))
        if why:
            raise ValueError("Resume unavailable: " + why)
        # A launch receipt closes the reconnect/double-click race before the supervisor starts.
        receipt = directory / "resume-launch.json"
        current_attempt = state(directory)['attempt']
        if receipt.exists():
            previous = atomic.read_json(receipt)
            if time.time()-previous['time'] < 30 and current_attempt <= previous.get('after_attempt',current_attempt):
                raise ValueError("Resume is already starting")
        atomic.write_json(receipt, {"time": time.time(),'after_attempt':current_attempt})
        if authorization:
            atomic.write_json(directory / 'authorization.json', authorization)
        launch(directory)


def request_stop(directory, force=False):
    s = state(directory)
    if s["state"] not in ACTIVE:
        raise ValueError("Execution is not running")
    path = directory / "stop-request.json"
    if force:
        if not path.exists() or time.time() - atomic.read_json(path)["time"] < 60:
            raise ValueError("Force stop is available 60 seconds after an unanswered safe stop")
        atomic.write_json(directory / "force-request.json", {"time": time.time()})
    elif not path.exists():
        atomic.write_json(path, {"time": time.time()})


def recover_orphans(root):
    """Restart only the journal owner to reconcile a crashed supervisor, never a trial."""
    for directory in config.store(root).iterdir():
        if not (directory / "manifest.json").exists() or state(directory)["state"] not in ACTIVE:
            continue
        receipt = directory / "supervisor.json"
        if not receipt.exists() or time.time() - atomic.read_json(receipt)["started"] < 10:
            continue
        try:
            with lock.exclusive(directory / "supervisor.lock"):
                env = dict(os.environ, PYTHONPATH=str(directory / "source" / "toolkit"))
                with (directory / "supervisor.log").open("ab") as log:
                    lock.spawn(
                        [sys.executable, "-m", "rl_researcher.runner", "--recover", str(directory)],
                        cwd=directory,
                        env=env,
                        stdout=log,
                        stderr=log,
                    )
        except OSError:
            pass


def reconcile(directory):
    directory = Path(directory)
    with lock.exclusive(directory / "supervisor.lock"):
        journal = runlog.Journal(directory)
        state = runlog.project(journal.manifest, journal.rows)
        if state['state'] in ACTIVE and journal.manifest.get('isolation'):
            pid = state.get('worker_pid')
            observed = lock.linux_process_identity(pid) if pid else None
            expected = state.get('worker_start_ticks')
            confirmed = pid and (observed is None or observed['state']=='Z' or
                                (expected and observed['start_ticks'] != expected))
            if not confirmed:
                journal.append('state', {'state':'Stopping', 'worker_exited':False,
                                         'error':'Worker termination is unconfirmed; dispatch and resume remain blocked.'})
                return
        if state["state"] in ACTIVE:
            journal.append(
                "state",
                {
                    "state": "Failed",
                    "error": "Supervisor interrupted. Owned processes have been terminated; resume from validated committed evidence.",
                    "worker_exited": True,
                },
            )


def supervise(directory):
    directory = Path(directory).resolve()
    manifest = atomic.read_json(directory/'manifest.json')
    try:
        with resources.compute_slot(manifest.get('isolation')):
            resources.preflight(directory,manifest.get('isolation'))
            _supervise(directory)
    except (OSError, ValueError) as error:
        with lock.exclusive(directory/'supervisor.lock'):
            runlog.Journal(directory).append('state',{'state':'Failed','worker_exited':True,
                                                     'error':'Supervisor resource boundary: '+str(error)})


def _supervise(directory):
    directory = Path(directory).resolve()
    with lock.exclusive(directory / "supervisor.lock"):
        journal = runlog.Journal(directory)
        manifest = journal.manifest
        before = runlog.project(manifest, journal.rows)
        journal.append("attempt", {"attempt": before["attempt"] + 1})
        token = os.urandom(32)
        listener = None if manifest.get('isolation') else Listener(("127.0.0.1", 0), authkey=token)
        messages = queue.Queue()

        def receive(pipe=None):
            try:
                connection = pipe if pipe is not None else listener.accept()
                while True:
                    message = channel.receive(connection, worker_message=True)
                    response = queue.Queue()
                    messages.put((message, response))
                    reply = response.get()
                    channel.send(connection, reply)
                    if "error" in reply:
                        return
            except (EOFError, OSError, ValueError, UnicodeError):
                pass

        if listener:
            threading.Thread(target=receive, daemon=True).start()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(directory / "source" / "toolkit")
        with (directory / "worker.log").open("ab") as logs:
            from . import isolation
            command = isolation.worker_command(directory, manifest) if manifest.get('isolation') else [
                manifest['python'], '-m', 'rl_researcher.worker', str(directory), str(listener.address[1]), token.hex()]
            proc = lock.spawn(
                command,
                cwd=directory,
                env=env,
                stdin=subprocess.PIPE if manifest.get('isolation') else None,
                stdout=subprocess.PIPE if manifest.get('isolation') else logs,
                stderr=subprocess.PIPE,
            )
            owned = lock.OwnedProcess(proc)
            log_overflow = threading.Event()
            def capture_log():
                count = logs.tell()
                while True:
                    block = proc.stderr.read(65536)
                    if not block:
                        return
                    if count+len(block)>8*1024**2:
                        log_overflow.set()
                        return
                    logs.write(block)
                    logs.flush()
                    count += len(block)
            log_thread = threading.Thread(target=capture_log,daemon=True)
            log_thread.start()
            if manifest.get('isolation'):
                threading.Thread(target=receive, args=(channel.Stream(proc.stdout, proc.stdin),), daemon=True).start()
            finished, forced, stop_seen = None, False, False
            process_identity = lock.linux_process_identity(proc.pid) if manifest.get('isolation') else None
            journal.append("state", {"worker_pid": proc.pid, "phase": "Starting worker",
                                     'worker_start_ticks':process_identity['start_ticks'] if process_identity else None})
            try:
                while proc.poll() is None or not messages.empty():
                    if log_overflow.is_set() or (directory/'events.jsonl').stat().st_size>16*1024**2:
                        owned.kill()
                        forced = True
                        finished = {'state':'Failed','error':'Worker diagnostic output allowance exhausted'}
                        break
                    try:
                        resources.preflight(directory,manifest.get('isolation'))
                    except (OSError,ValueError) as error:
                        owned.kill()
                        forced = True
                        finished = {'state':'Failed','error':str(error)}
                        break
                    stopfile = directory / "stop-request.json"
                    if stopfile.exists() and not stop_seen:
                        stop_seen = True
                        journal.append(
                            "state",
                            {
                                "state": "Stopping",
                                "stop_requested": atomic.read_json(stopfile)["time"],
                            },
                        )
                    current = runlog.project(manifest, journal.rows)
                    overall_seconds = manifest["definition"]["limits"]["overall_seconds"]
                    deadline = current.get("active_since", time.time()) + overall_seconds - current.get("spent_seconds", 0.0)
                    authority_path = directory / 'authorization.json'
                    authority = atomic.read_json(authority_path) if manifest.get('authorization') else None
                    if current["trial"] and current["phase"] != "Finalizing analysis":
                        deadline = min(
                            deadline, current["trials"][current["trial"]].get("deadline", deadline)
                        )
                    if ((directory / "force-request.json").exists() or time.time() > deadline + 10
                            or (authority and time.time() >= authority['deadline'])):
                        forced = True
                        finished = {
                            "state": "Failed"
                            if (directory / "force-request.json").exists()
                            else "Incomplete",
                            "error": "Owned worker processes terminated; progress after the committed checkpoint may be lost.",
                        }
                        owned.kill()
                        break
                    try:
                        message, response = messages.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        kind, data = message["kind"], message["data"]
                        value = None
                        if kind == "publish_checkpoint":
                            value = checkpoint.publish(
                                directory, manifest, data["trial"], data["source"], data["decision"]
                            )
                            journal.append("checkpoint", value)
                            checkpoint.retain(directory, journal.rows, data["trial"])
                        elif kind == "publish_artifact":
                            value = evidence.artifact(
                                directory, data["source"], data["label"], data["media_type"]
                            )
                            value.update(trial=data.get("trial"), attempt=data.get("attempt"))
                            journal.append("artifact", value)
                            atomic.write_json(
                                directory / "artifacts.json",
                                [e["data"] for e in journal.rows if e["kind"] == "artifact"],
                            )
                        elif kind == "finished":
                            finished = data
                            if data.get("results") is not None:
                                atomic.write_json(directory / "results.json", data.pop("results"))
                        else:
                            if kind == "result":
                                atomic.write_json(
                                    directory / "trials" / data["trial"] / "result.json",
                                    data["result"],
                                )
                            if kind == "sample":
                                if (
                                    data["trial"] != current["trial"]
                                    or data["attempt"] != current["attempt"]
                                ):
                                    raise ValueError(
                                        "Telemetry identity does not match the active trial and attempt"
                                    )
                                preview = data.get("preview")
                                if preview and (
                                    preview.get("trial") != data["trial"]
                                    or preview.get("attempt") != data["attempt"]
                                    or not (directory / preview["path"]).is_file()
                                    or atomic.digest(directory / preview["path"])
                                    != preview["sha256"]
                                ):
                                    raise ValueError(
                                        "Preview must be a committed artifact for this trial and attempt"
                                    )
                            journal.append(kind, data)
                        response.put({"value": value})
                    except Exception as exc:
                        response.put({"error": str(exc)})
                        finished = {
                            "state": "Failed",
                            "error": "Required evidence storage failed: " + str(exc),
                        }
                        if isinstance(exc,resources.ResourceUnavailable):
                            finished['resource_fault']=exc.facts
                        owned.kill()
                        break
                proc.wait(timeout=10)
                if finished is None:
                    finished = {
                        "state": "Failed",
                        "error": "Worker exited without explicit finalization; inspect complete logs.",
                    }
                # Stopped/Completed are published only after process exit.
                journal.append("state", dict(finished, worker_exited=True, forced=forced))
            finally:
                owned.close()
                log_thread.join(timeout=5)
                resources.cleanup_work(directory,manifest.get('isolation'))
                if listener:
                    listener.close()


if __name__ == "__main__":
    if sys.argv[1] == "--recover":
        reconcile(sys.argv[2])
    else:
        supervise(sys.argv[1])
