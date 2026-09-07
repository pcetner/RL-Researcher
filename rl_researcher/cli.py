"""Shared argument handling for the ``python -m rl_researcher.<verb>`` entry points."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Tuple

from rl_researcher.config import Config, kind_for, load_config, out_dir_for, resolve_spec
from rl_researcher.kinds import RunKind
from rl_researcher.spec import RunSpec


def spec_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("spec", help="path to the spec TOML, or its name under the project's specs directory")
    p.add_argument("--out", default=None, help="output directory (default: the kind's root / the run name)")
    return p


def load_all(spec_arg: str, out_arg: Optional[str] = None) -> Tuple[Config, RunKind, RunSpec, Path]:
    config = load_config()
    spec_path = resolve_spec(spec_arg, config)
    kind = kind_for(spec_path, config)
    spec = kind.load(spec_path)
    out = Path(out_arg) if out_arg else out_dir_for(spec, config)
    return config, kind, spec, out
