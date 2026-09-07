"""A throwaway project on disk: a config, a specs directory and a toy spec.

Every end-to-end test runs against one of these rather than against a real repo, so the
framework is exercised exactly the way a consuming project uses it: find the config by walking
up from the working directory, load the kind by entry point, write outputs under the kind's
root.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from rl_researcher.config import load_config
from rl_researcher.examples.toy.kind import write_example_spec

CONFIG = """
[project]
name = "toy-project"

[kinds]
toy = "rl_researcher.examples.toy.kind:ToyKind"

[out]
toy = "docs/toy"

[gate]
ungated_wall_minutes = {ungated_minutes}
ungated_money_usd = 0.0
unknown_device = "gate"

[devices."cpu"]
hourly_usd = 0.0
kind = "cpu"

[watcher]
interval_seconds = 1
"""


@contextmanager
def chdir(path: Path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield Path(path)
    finally:
        os.chdir(old)


def make_project(root: Path, *, ungated_minutes: float = 5.0, max_steps: int = 200,
                 seeds=(0, 1, 2), extra: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "rl-researcher.toml").write_text(CONFIG.format(ungated_minutes=ungated_minutes),
                                             encoding="utf-8")
    write_example_spec(root / "studies" / "toy-line-fit.toml", max_steps=max_steps, seeds=seeds, extra=extra)
    return root


@pytest.fixture
def project(tmp_path):
    """A project rooted at ``tmp_path/proj`` with the working directory inside it."""
    root = make_project(tmp_path / "proj")
    with chdir(root):
        yield root


@pytest.fixture
def toy(project):
    """``(config, kind, spec, out)`` for the project's toy spec."""
    from rl_researcher.config import kind_for, out_dir_for

    config = load_config()
    spec_path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(spec_path, config)
    spec = kind.load(spec_path)
    return config, kind, spec, out_dir_for(spec, config)
