"""The project contract: resolve(config), execute(context, trial, checkpoint), finalize(context, results)."""

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from . import atomic, config


def entrypoint(root, reference):
    filename, function = reference.split(":")
    path = (Path(root) / filename).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError("Missing or invalid entrypoint: " + reference)
    spec = importlib.util.spec_from_file_location("_research_experiment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = getattr(module, function)
    if not callable(target):
        raise ValueError("Entrypoint is not callable: " + reference)
    return target


def inspect(root, identifier):
    """Metadata discovery does not import project code or validate by executing it."""
    cfg = config.load(root)
    record = next((e for e in cfg["experiments"] if e["id"] == identifier), None)
    if record is None:
        raise ValueError("Experiment is not registered")
    path = (Path(root) / record["definition"]).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Definition outside project")
    definition = atomic.read_json(path)
    sources = {}
    for declaration in [record["definition"], *definition["sources"]]:
        p = (Path(root) / declaration).resolve()
        if not p.is_relative_to(Path(root).resolve()) or not p.exists():
            raise ValueError("Missing source: " + declaration)
        for f in [p] if p.is_file() else sorted(p.rglob("*.py")):
            sources[f.relative_to(root).as_posix()] = atomic.digest(f)
    inputs = {}
    input_metadata = {}
    for name, entry in definition.get("inputs", {}).items():
        p = Path(entry["path"])
        if not p.is_absolute():
            p = Path(root) / p
        inputs[name] = {"path": str(p.resolve()), "sha256": entry["sha256"]}
        if not p.is_file():
            raise ValueError("Missing declared input: " + name)
        stat = p.stat()
        input_metadata[name] = {"size": stat.st_size, "modified_ns": stat.st_mtime_ns}
    # Viewer changes do not change execution identity or invalidate recovery.
    runtime = {
        p.name: atomic.digest(p)
        for p in Path(__file__).parent.glob("*.py")
        if p.name not in ("serve.py", "board_view.py")
    }
    value = {
        "root": str(Path(root).resolve()),
        "definition": definition,
        "sources": sources,
        "inputs": inputs,
        "runtime": runtime,
        "input_metadata": input_metadata,
        "python": sys.executable,
        "python_version": sys.version,
        "dependencies": {
            name: importlib.metadata.version(name) for name in definition.get("dependencies", [])
        },
    }
    value["revision"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def validate(root, identifier, isolation_policy=None):
    resolved = inspect(root, identifier)
    definition = resolved["definition"]
    for name, item in resolved["inputs"].items():
        if not Path(item["path"]).is_file():
            raise ValueError("Missing input: " + name + " (" + item["path"] + ")")
        if atomic.digest(item["path"]) != item["sha256"]:
            raise ValueError("Input hash changed: " + name)
    for ref in ("resolver", "executor", "finalizer"):
        if definition.get(ref):
            path, symbol = definition[ref].split(":")
            if path not in resolved["sources"]:
                raise ValueError("Entrypoint must be captured in sources: " + path)
    # Run technical resolution in an isolated process. Discovery never does this.
    if isolation_policy:
        from . import evidence, isolation, resources
        import uuid
        box = Path(root)/'.research/validation-boxes'/uuid.uuid4().hex
        box.mkdir(parents=True)
        resources.preflight(box,isolation_policy)
        evidence.freeze(box,resolved)
        manifest = dict(resolved,isolation=isolation_policy)
        argv = isolation.worker_command(box,manifest)
        index = argv.index('--chdir')
        argv[index:index] = ['--ro-bind',str(root),str(root)]
        argv[-3:] = ['--resolve',str(root),identifier]
        with resources.compute_slot(isolation_policy), (box/'stdout').open('w+b') as output, (box/'stderr').open('w+b') as errors:
            result = subprocess.run(argv,stdout=output,stderr=errors,timeout=30,check=False)
            output.seek(0)
            errors.seek(0)
            stdout, stderr = output.read(1024*1024+1), errors.read(3000)
        if len(stdout)>1024*1024:
            raise ValueError('Resolver output exceeded its bound')
    else:
        result = subprocess.run(
            [sys.executable, "-m", "rl_researcher.worker", "--resolve", str(root), identifier],
            capture_output=True, text=True, timeout=30)
        stdout, stderr = result.stdout, result.stderr
    if result.returncode:
        raise ValueError("Technical validation failed: " + str(stderr)[-3000:])
    returned = json.loads(stdout)
    if returned != definition:
        raise ValueError(
            "Resolver must return the displayed definition unchanged; generate derived trials in the definition document"
        )
    trials = definition["trials"]
    ids = [t["id"] for t in trials]
    if (
        not ids
        or len(set(ids)) != len(ids)
        or any(not re.fullmatch("[a-zA-Z0-9_-]+", i) for i in ids)
    ):
        raise ValueError("Trial IDs must be unique, nonempty, and path-safe")
    for limit in [definition["limits"]["overall_seconds"], *[t["seconds"] for t in trials]]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, (int, float))
            or not math.isfinite(limit)
            or limit <= 0
        ):
            raise ValueError("Limits must be positive finite seconds")
    if inspect(root, identifier)["revision"] != resolved["revision"]:
        raise ValueError("Definition changed during validation")
    atomic.write_json(Path(root) / ".research" / "validation" / (identifier + ".json"), resolved)
    return resolved


def resume_reason(directory, manifest, state):
    from . import checkpoint, evidence

    try:
        if state["state"] not in ("Stopped", "Failed", "Incomplete"):
            raise ValueError("Execution is " + state["state"])
        overall_seconds = manifest["definition"]["limits"]["overall_seconds"]
        if state.get("spent_seconds", 0.0) >= overall_seconds:
            raise ValueError("Overall execution budget has been exhausted")
        if sys.executable != manifest["python"] or sys.version != manifest["python_version"]:
            raise ValueError("Required local interpreter has changed")
        for name, version in manifest["dependencies"].items():
            if importlib.metadata.version(name) != version:
                raise ValueError("Execution dependency changed: " + name)
        evidence.verify_source(directory, manifest)
        for name, item in manifest["inputs"].items():
            if not Path(item["path"]).is_file():
                raise ValueError("Missing input: " + name)
            if atomic.digest(item["path"]) != item["sha256"]:
                raise ValueError("Checkpoint input has changed: " + name)
        for trial in manifest["definition"]["trials"]:
            saved = state["trials"][trial["id"]]
            if saved["state"] == "Completed":
                continue
            if saved.get("deadline"):
                if saved.get("spent_seconds", 0.0) >= trial["seconds"]:
                    raise ValueError("Trial budget has been exhausted: " + trial["label"])
                if not saved["checkpoint"]:
                    raise ValueError(
                        "Interrupted trial has no committed checkpoint: " + trial["label"]
                    )
                checkpoint.validate(directory, manifest, saved["checkpoint"])
            break
        return None
    except (ValueError, OSError, KeyError, importlib.metadata.PackageNotFoundError) as e:
        return str(e)
