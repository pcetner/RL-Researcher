import shutil
import time
import uuid
import os
from pathlib import Path
from . import atomic, resources


def publish(directory, manifest, trial, source, progress):
    source = Path(source).resolve()
    if not source.is_relative_to((directory / "trials" / trial / "work").resolve()):
        raise ValueError("Checkpoint must come from this trial work directory")
    source = resources.work_source(directory,source)
    resources.publication_space(directory,resources.retained_bytes(source) if source.is_dir() else source.stat().st_size)
    parent = directory / "trials" / trial / "checkpoints"
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / (".staging-" + uuid.uuid4().hex)
    stage.mkdir()
    if source.is_dir():
        shutil.copytree(source, stage / "payload")
    else:
        (stage / "payload").mkdir()
        shutil.copy2(source, stage / "payload" / source.name)
    files = {
        str(p.relative_to(stage / "payload")).replace("\\", "/"): atomic.digest(p)
        for p in (stage / "payload").rglob("*")
        if p.is_file()
    }
    if not files:
        raise ValueError("Checkpoint payload is empty")
    for f in (stage / "payload").rglob("*"):
        if f.is_file():
            with f.open("r+b") as stream:
                os.fsync(stream.fileno())
    meta = {
        "trial": trial,
        "revision": manifest["revision"],
        "decision": progress,
        "files": files,
        "time": time.time(),
    }
    atomic.write_json(stage / "metadata.json", meta)
    target = parent / uuid.uuid4().hex
    stage.rename(target)
    return dict(meta, path=str(target.relative_to(directory)).replace("\\", "/"))


def validate(directory, manifest, checkpoint):
    path = directory / checkpoint["path"]
    meta = atomic.read_json(path / "metadata.json")
    if (
        meta["revision"] != manifest["revision"]
        or meta["trial"] != checkpoint["trial"]
        or meta != {k: v for k, v in checkpoint.items() if k != "path"}
    ):
        raise ValueError("Checkpoint identity does not match the execution")
    for rel, sha in meta["files"].items():
        p = path / "payload" / rel
        if not p.is_file() or atomic.digest(p) != sha:
            raise ValueError("Checkpoint corrupt or missing: " + rel)
    return path / "payload"


def retain(directory, rows, trial):
    committed = [
        e["data"]["path"] for e in rows if e["kind"] == "checkpoint" and e["data"]["trial"] == trial
    ]
    for rel in committed[:-2]:
        p = directory / rel
        if p.exists():
            shutil.rmtree(p)


def recover_committed(directory):
    """Quarantine corrupt stopped-run points and select a verified earlier commit.

    Keeps all files and journal history. Never repairs a running trial or changes
    its source, input identity, counters, or cumulative elapsed time.
    """
    from . import lock, runlog
    with lock.exclusive(directory/'supervisor.lock'):
        manifest=atomic.read_json(directory/'manifest.json')
        rows=runlog.events(directory)
        state=runlog.project(manifest,rows)
        if state['state'] not in {'Stopped','Failed','Incomplete'}:return
        journal=None
        quarantined={e['data']['path'] for e in rows if e['kind']=='checkpoint_quarantined'}
        for trial,saved in state['trials'].items():
            current=saved.get('checkpoint')
            if not current:continue
            try:
                validate(directory,manifest,current)
                continue
            except (ValueError,OSError,KeyError) as error:
                journal=journal or runlog.Journal(directory)
                journal.append('checkpoint_quarantined',dict(current,reason=str(error)))
                quarantined.add(current['path'])
            candidates=[e['data'] for e in reversed(rows) if e['kind']=='checkpoint' and e['data']['trial']==trial and e['data']['path'] not in quarantined]
            for candidate in candidates:
                try:validate(directory,manifest,candidate)
                except (ValueError,OSError,KeyError):continue
                journal.append('checkpoint',candidate)
                journal.append('checkpoint_fallback',{'trial':trial,'from':current['path'],'to':candidate['path']})
                break
