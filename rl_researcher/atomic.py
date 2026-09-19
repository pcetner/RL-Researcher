import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as f:
        h = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
        return h.hexdigest()


def write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Windows readers and antivirus can briefly deny replacement. Bound retries;
        # a persistent failure still stops the execution rather than losing evidence.
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(min(0.01 * 2**attempt, 0.2))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_text(path, text):
    write_bytes(path, text.encode("utf-8"))


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, allow_nan=False))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
