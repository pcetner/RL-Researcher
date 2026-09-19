import shutil
from pathlib import Path
from . import atomic, resources


def artifact(directory, source, label, media_type):
    source = Path(source).resolve()
    if not source.is_relative_to(directory.resolve()):
        raise ValueError("Publish from the execution directory")
    source = resources.work_source(directory,source)
    resources.publication_space(directory,source.stat().st_size)
    sha = atomic.digest(source)
    suffix = source.suffix.lower()
    target = directory / "artifacts" / (sha + suffix)
    if not target.exists():
        atomic.write_bytes(target, source.read_bytes())
    return {
        "label": label,
        "type": media_type,
        "path": str(target.relative_to(directory)).replace("\\", "/"),
        "sha256": sha,
    }


def freeze(directory, resolved):
    for rel, sha in resolved["sources"].items():
        src = Path(resolved["root"]) / rel
        dst = directory / "source" / "project" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if atomic.digest(dst) != sha:
            raise ValueError("Source changed during capture: " + rel)
    package = Path(__file__).parent
    for rel, sha in resolved["runtime"].items():
        dst = directory / "source" / "toolkit" / "rl_researcher" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package / rel, dst)
        if atomic.digest(dst) != sha:
            raise ValueError("Runtime changed during capture: " + rel)


def verify_source(directory, manifest):
    for section, folder in [("sources", "project"), ("runtime", "toolkit/rl_researcher")]:
        for rel, sha in manifest[section].items():
            p = directory / "source" / folder / rel
            if not p.is_file() or atomic.digest(p) != sha:
                raise ValueError("Captured source changed or missing: " + rel)
