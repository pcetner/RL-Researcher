"""The canary result: produced by `report`, consumed by C09 and the state page.

Nothing in this file writes `canary.json`. That is the whole point of the file. The check that
reads it had a test that hand-wrote the artefact and then asserted the reader responded to it,
so the reader was covered, the writer did not exist, and C09 could only ever say "no canary
result on file" -- in every project, forever. A test for a reader must consume what the writer
produced.

Every case here needs a real repository, because every claim the canary result makes is about a
commit.
"""

import json
import subprocess

import pytest

pytest.importorskip("matplotlib")
pytestmark = pytest.mark.tier1

from pathlib import Path  # noqa: E402

from rl_researcher import report, run  # noqa: E402
from rl_researcher.canary import canary_path, is_canary, read_canary, record_canary  # noqa: E402
from rl_researcher.checks import run_checks  # noqa: E402
from rl_researcher.config import kind_for, load_config, out_dir_for  # noqa: E402
from rl_researcher.examples.toy.kind import write_example_spec  # noqa: E402
from tests.conftest import chdir  # noqa: E402

CONFIG = """
[project]
name = "canary-project"

[kinds]
toy = "rl_researcher.examples.toy.kind:ToyKind"

[out]
toy = "docs/toy"

[gate]
ungated_wall_minutes = 600

[canary]
spec = "{canary}"
watched = ["src"]
"""


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                          cwd=str(root), capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _spec(root: Path, name: str) -> Path:
    """A toy spec under another name. Two units, so a canary run is a second or two.

    `screening = true` because that is what a canary is: one seed, and no claim that the
    difference between the arms means anything (C05).
    """
    path = root / "studies" / f"{name}.toml"
    write_example_spec(path, max_steps=20, seeds=(0,), extra="screening = true")
    path.write_text(path.read_text(encoding="utf-8").replace('name = "toy-line-fit"',
                                                             f'name = "{name}"', 1),
                    encoding="utf-8")
    return path


def _project(root: Path, *, canary: str = "studies/toy-canary.toml", git: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "rl-researcher.toml").write_text(CONFIG.format(canary=canary), encoding="utf-8")
    _spec(root, "toy-canary")
    _spec(root, "toy-real")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    if git:
        _git(root, "init", "--quiet")
        _git(root, "add", "-A")
        _git(root, "commit", "--quiet", "-m", "the project")
    return root


def _loaded(root: Path, name: str):
    config = load_config()
    path = root / "studies" / f"{name}.toml"
    kind = kind_for(path, config)
    spec = kind.load(path)
    return config, kind, spec, out_dir_for(spec, config)


def _c09(root: Path, name: str = "toy-real"):
    config, kind, spec, out = _loaded(root, name)
    return [f for f in run_checks("run", spec, kind, config, out=out, root=root) if f.check == "C09"]


def _run_the_canary(root: Path, *extra: str) -> None:
    assert run.main(["toy-canary", *extra]) == 0
    assert report.main(["toy-canary"]) == 0


# -- the writer produces what the readers consume ------------------------------------------

def test_report_records_the_canary_and_C09_then_has_something_to_read(tmp_path, capsys):
    root = _project(tmp_path / "proj")
    with chdir(root):
        assert "no canary result" in _c09(root)[0].message      # nothing has run yet

        _run_the_canary(root)
        assert "canary ->" in capsys.readouterr().out

        config, _, _, _ = _loaded(root, "toy-real")
        blob = read_canary(config)
        assert blob["commit"] == _git(root, "rev-parse", "HEAD")
        assert blob["run"] == "toy-canary" and blob["date"] and blob["rl_researcher"]
        assert not _c09(root)


def test_the_state_page_reports_the_canary_it_was_given(tmp_path):
    """`STATE.md` could only ever print `never run`, for the same reason."""
    from rl_researcher import state

    root = _project(tmp_path / "proj")
    with chdir(root):
        _run_the_canary(root)
        assert state.main([]) == 0
        said = (root / "docs" / "STATE.md").read_text(encoding="utf-8")
        assert _git(root, "rev-parse", "HEAD")[:12] in said
        assert "never run" not in said


# -- the discrimination C09 exists to make, exercised for the first time --------------------

def test_a_watched_path_that_changed_after_the_canary_is_named(tmp_path):
    root = _project(tmp_path / "proj")
    with chdir(root):
        _run_the_canary(root)
        assert not _c09(root)

        (root / "src" / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")
        found = _c09(root)
        assert found and found[0].level == "warn"
        assert "thing.py" in found[0].message and "changed after it" in found[0].message


def test_a_commit_this_repository_cannot_resolve_is_not_a_pass(tmp_path):
    """The fail-open behind the old test: `git diff <nonsense>` errors, and an error read as
    "nothing changed" means one unresolvable line silences C09 for good."""
    root = _project(tmp_path / "proj")
    with chdir(root):
        _run_the_canary(root)
        config, _, _, _ = _loaded(root, "toy-real")
        p = canary_path(config)
        blob = json.loads(p.read_text(encoding="utf-8"))
        blob["commit"] = "0" * 40
        p.write_text(json.dumps(blob), encoding="utf-8")

        found = _c09(root)
        assert found and found[0].level == "warn" and "cannot resolve" in found[0].message


# -- what must not be recorded --------------------------------------------------------------

def test_a_canary_with_units_missing_records_nothing(tmp_path, capsys):
    root = _project(tmp_path / "proj")
    with chdir(root):
        assert run.main(["toy-canary", "--units", "ols"]) == 0
        assert report.main(["toy-canary"]) == 0
        assert "canary not recorded" in capsys.readouterr().out

        config, _, _, _ = _loaded(root, "toy-real")
        assert not canary_path(config).is_file()
        assert "no canary result" in _c09(root)[0].message


def test_a_unit_that_did_not_complete_records_nothing(tmp_path):
    root = _project(tmp_path / "proj")
    with chdir(root):
        _run_the_canary(root)
        config, _, spec, out = _loaded(root, "toy-canary")
        canary_path(config).unlink()

        summary = json.loads((out / "results.json").read_text(encoding="utf-8"))
        summary["runs"][0]["status"] = "incomplete"
        path, why = record_canary(config, spec, summary)
        assert path is None and "did not complete" in why
        assert not canary_path(config).is_file()


def test_a_commit_git_cannot_name_is_never_written(tmp_path, capsys):
    """No repository, so no commit -- and a blob whose commit is `unknown` would silence C09
    permanently, which is worse than having none."""
    root = _project(tmp_path / "proj", git=False)
    with chdir(root):
        _run_the_canary(root)
        assert "canary not recorded" in capsys.readouterr().out

        config, _, _, _ = _loaded(root, "toy-real")
        assert not canary_path(config).is_file()
        assert "no canary result" in _c09(root)[0].message


# -- the canary has to be able to recognise itself ------------------------------------------

@pytest.mark.parametrize("written", ["studies/toy-canary.toml", "toy-canary", "toy-canary.toml"])
def test_the_canary_is_the_canary_however_the_config_spells_it(tmp_path, written):
    """`[canary] spec` is documented as a path and C09 compared it with the spec's *name*, so
    the canary warned about itself in the only project that has one."""
    root = _project(tmp_path / "proj", canary=written)
    with chdir(root):
        config, _, canary, _ = _loaded(root, "toy-canary")
        _, _, other, _ = _loaded(root, "toy-real")
        assert is_canary(canary, config)
        assert not is_canary(other, config)
        assert not _c09(root, "toy-canary")
