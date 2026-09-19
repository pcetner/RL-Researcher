from pathlib import Path
from . import atomic


def load(root):
    root = Path(root).resolve()
    config = atomic.read_json(root / "research.json")
    config["root"] = str(root)
    return config


def store(root):
    path = Path(root) / ".research" / "executions"
    path.mkdir(parents=True, exist_ok=True)
    return path
