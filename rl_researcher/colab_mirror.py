"""Mirror a run's output directory onto Google Drive while it runs.

A Colab session can vanish at any moment, so the evidence has to live somewhere that
outlives it. Training straight onto Drive is the wrong fix: ``checkpoint.pt`` carries the
model *and* the Adam state (100-330 MB for the current variants), and writing that across
the Drive FUSE mount every few hundred steps costs more than the training it protects and
can tear on disconnect. So a study writes to local disk at full speed and this mirrors it,
small files often and the heavy checkpoints rarely.

    python -u -m rl_researcher.colab_mirror --src /content/study-out \
        --dst /content/drive/MyDrive/auto-sm64/studies/study3-sticky-actions &

Copies a file only when its size or mtime differs from the destination, writes through a
``.part`` temporary so a half-copied file is never mistaken for a good one, survives a
per-file error (Drive hiccups), and logs one line per pass (the never-silent rule).
``--once`` does a single pass, which is what the final sync after a run is.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, List, Tuple

Log = Callable[[str], None]


def _needs_copy(src: Path, dst: Path) -> bool:
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return True
    return a.st_size != b.st_size or int(a.st_mtime) > int(b.st_mtime)


def mirror_once(src: Path, dst: Path, *, slow_globs: List[str], include_slow: bool,
                log: Log = print) -> Tuple[int, int, int]:
    """One pass. Returns (files copied, bytes copied, files skipped as slow)."""
    copied = skipped = 0
    total = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        for name in files:
            if name.endswith(".part") or name.endswith(".tmp"):
                continue  # someone else's half-written file
            is_slow = any(fnmatch.fnmatch(name, g) for g in slow_globs)
            if is_slow and not include_slow:
                skipped += 1
                continue
            s = Path(root) / name
            d = dst / rel / name
            if not _needs_copy(s, d):
                continue
            try:
                d.parent.mkdir(parents=True, exist_ok=True)
                part = d.with_name(d.name + ".part")
                shutil.copy2(s, part)
                os.replace(part, d)
                copied += 1
                total += s.stat().st_size
            except OSError as exc:  # a Drive hiccup must not end the mirror
                log(f"  ! {rel / name}: {type(exc).__name__}: {exc}")
    return copied, total, skipped


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--src", required=True, help="the study's --out directory on local disk")
    p.add_argument("--dst", required=True, help="where it should survive (a Drive path)")
    p.add_argument("--every", type=float, default=180.0, help="seconds between passes")
    p.add_argument("--slow-glob", default="checkpoint.pt",
                   help="comma-separated names copied only every --slow-every (they are big)")
    p.add_argument("--slow-every", type=float, default=900.0)
    p.add_argument("--once", action="store_true", help="a single pass including the slow files")
    args = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    def log(s: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} {s}", flush=True)

    src, dst = Path(args.src), Path(args.dst)
    slow = [g for g in args.slow_glob.split(",") if g]
    dst.mkdir(parents=True, exist_ok=True)
    if args.once:
        c, b, _ = mirror_once(src, dst, slow_globs=slow, include_slow=True, log=log)
        log(f"final sync {src} -> {dst}: {c} files, {b / 1e6:.1f} MB")
        return 0

    log(f"mirroring {src} -> {dst} every {args.every:.0f}s ({','.join(slow) or 'nothing'} "
        f"every {args.slow_every:.0f}s); Ctrl-C or ending the runtime stops it")
    last_slow = 0.0
    while True:
        if not src.exists():
            log(f"  waiting for {src}")
        else:
            now = time.time()
            include_slow = now - last_slow >= args.slow_every
            c, b, sk = mirror_once(src, dst, slow_globs=slow, include_slow=include_slow, log=log)
            if include_slow:
                last_slow = now
            if c or sk:
                log(f"  {c} files ({b / 1e6:.1f} MB){'' if include_slow else f', {sk} big files next pass'}")
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
