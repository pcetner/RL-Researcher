"""The entry points, in the order a session uses them.

The skills drive the framework through ``python -m rl_researcher.<verb>``, so the verbs and
their exit codes are the contract, not the Python API. This walks the whole M1 path in a
throwaway project: check, estimate, refuse, approve, run, status. Exit codes carry meaning
(0 fine, 2 stale or failed, 3 gated), because a monitor and a skill both read them.
"""

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher import approve, check, estimate, pin, run, status  # noqa: E402
from rl_researcher.units import read_json, unit_dir  # noqa: E402

SPEC = "studies/toy-line-fit.toml"


def test_check_describes_the_registration(project, capsys):
    assert check.main([SPEC]) == 0
    out = capsys.readouterr().out
    assert "toy toy-line-fit" in out and "6 unit(s)" in out and "fingerprint" in out


def test_check_refuses_a_spec_that_registers_an_unmeasurable_metric(project, capsys):
    p = project / "studies" / "toy-line-fit.toml"
    p.write_text(p.read_text(encoding="utf-8").replace('name = "r2"', 'name = "vibes"', 1), encoding="utf-8")
    assert check.main([SPEC]) == 1
    assert "unknown metric" in capsys.readouterr().out


def test_pin_says_plainly_that_this_kind_has_nothing_to_pin(project, capsys):
    assert pin.main([SPEC]) == 0
    assert "nothing to pin" in capsys.readouterr().out


def test_the_gated_path_is_estimate_refuse_approve_run(project, capsys):
    # 1. the estimate says it is over the line and exits 3
    assert estimate.main([SPEC]) == 3
    out = capsys.readouterr().out
    assert "budget-cap" in out and "needs an approval" in out

    # 2. run refuses, and leaves nothing behind
    assert run.main([SPEC]) == 3
    assert "over the gate line" in capsys.readouterr().out

    # 3. the human says yes; the LLM records what they said
    assert approve.main([SPEC, "--quote", "yes, go ahead", "--session", "s1"]) == 0
    assert "approval written" in capsys.readouterr().out

    # 4. now it runs, and the estimate agrees it may
    assert estimate.main([SPEC]) == 0
    assert "approved:" in capsys.readouterr().out
    assert run.main([SPEC]) == 0

    # 5. status is clean and the results are on disk
    assert status.main([SPEC]) == 0
    said = capsys.readouterr().out
    assert "6/6 units done" in said and "state: finished" in said


def test_a_short_run_needs_no_approval_at_all(project, capsys):
    """The whole point of a cost gate: small work is not gated, so the LLM just does it."""
    assert estimate.main([SPEC, "--max-seconds", "5"]) == 0
    assert "may run now" in capsys.readouterr().out
    assert run.main([SPEC, "--max-seconds", "5"]) == 0


def test_approve_writes_nothing_when_the_run_is_under_the_line(tmp_path, capsys):
    """An approval file that exists for a run nobody had to approve would make the gate's
    own record untrustworthy."""
    from tests.conftest import chdir, make_project

    root = make_project(tmp_path / "cheap", ungated_minutes=600)
    with chdir(root):
        assert approve.main([SPEC, "--quote", "y"]) == 0
        assert "no approval needed" in capsys.readouterr().out
        assert not list((root / "studies" / "approvals").glob("*.toml"))


def test_an_approval_needs_the_humans_own_words(project, capsys):
    assert approve.main([SPEC, "--quote", "   "]) == 1
    assert "quote of what the human said" in capsys.readouterr().out


def test_status_before_anything_runs_is_not_a_failure(project, capsys):
    """A monitor calls this on a timer; counting queued units as failures would make the exit
    code meaningless for most of a run."""
    assert status.main([SPEC]) == 0
    assert "not started" in capsys.readouterr().out


def test_status_exits_2_on_a_failed_unit(project, capsys):
    from rl_researcher.units import mark_failed

    assert run.main([SPEC, "--max-seconds", "5"]) == 0
    cell = unit_dir(project / "docs" / "toy" / "toy-line-fit", "ols/seed0")
    (cell / "results.json").unlink()
    mark_failed(cell / "progress.json", RuntimeError("CUDA out of memory"))
    assert status.main([SPEC]) == 2
    assert "FAILED" in capsys.readouterr().out


def test_a_run_is_resumable_from_the_same_command(project, capsys):
    assert run.main([SPEC, "--max-seconds", "5"]) == 0
    out = project / "docs" / "toy" / "toy-line-fit"
    first = read_json(unit_dir(out, "ols/seed0") / "results.json")
    assert run.main([SPEC, "--max-seconds", "5"]) == 0     # idempotent
    assert read_json(unit_dir(out, "ols/seed0") / "results.json") == first


def test_a_spec_can_be_named_instead_of_pathed(project, capsys):
    """The skills say `run toy-line-fit`; the project's specs directory is searched."""
    assert check.main(["toy-line-fit"]) == 0
    assert "toy-line-fit" in capsys.readouterr().out


def test_every_spec_taking_verb_accepts_a_bare_run_name(project, capsys):
    """The skills say `status toy-line-fit`, not a path.

    `status` used to build its own parser and hand the argument straight to Path, so a bare name
    raised FileNotFoundError while the same name worked on `check`. It is the verb a monitor
    calls on a timer, so it is the worst one to have a second way of being addressed.
    """
    from rl_researcher import estimate as estimate_mod

    assert check.main(["toy-line-fit"]) == 0
    assert status.main(["toy-line-fit"]) == 0
    assert pin.main(["toy-line-fit"]) == 0
    assert estimate_mod.main(["toy-line-fit", "--max-seconds", "5"]) == 0


def test_report_is_written_from_disk_and_says_so_when_there_is_nothing_to_write(project, capsys):
    """A page only the process that produced it can produce is a page nobody can check."""
    from rl_researcher import report

    assert report.main([SPEC]) == 1
    assert "has not finished" in capsys.readouterr().out

    assert run.main([SPEC, "--max-seconds", "5"]) == 0
    assert report.main([SPEC]) == 0
    said = capsys.readouterr().out
    assert "report ->" in said and "page   ->" in said and "new finding(s)" in said

    out = project / "docs" / "toy" / "toy-line-fit"
    assert (out / "README.md").is_file() and (out / "report.html").is_file()

    # Twice is the same document and no new rows: regenerating is how a page is checked.
    before = (out / "README.md").read_text(encoding="utf-8")
    assert report.main([SPEC]) == 0
    assert "0 new finding(s)" in capsys.readouterr().out
    after = (out / "README.md").read_text(encoding="utf-8")
    strip = [ln for ln in before.splitlines() if "generated=" not in ln]
    assert strip == [ln for ln in after.splitlines() if "generated=" not in ln]


def test_the_report_footer_names_a_command_that_exists(project):
    """The regenerate command in a footer is the whole reason the footer is there."""
    import importlib

    from rl_researcher import report, run

    assert run.main([SPEC, "--max-seconds", "5"]) == 0
    assert report.main([SPEC]) == 0
    text = (project / "docs" / "toy" / "toy-line-fit" / "README.md").read_text(encoding="utf-8")
    named = "python -m rl_researcher.report toy-line-fit"
    assert named in text
    assert importlib.import_module("rl_researcher.report") is report


def test_installing_skills_from_a_tree_that_has_none_is_not_a_crash(tmp_path):
    """A fresh clone has no skills directory. Nothing to install is not an error, and a
    traceback would read as a broken install rather than as an empty one."""
    from rl_researcher.install_skills import install

    assert install(tmp_path / "dest", source=tmp_path / "absent") == []


def test_run_exits_4_on_a_check_error_and_no_check_waives_it(project, capsys, monkeypatch):
    """The check command and the run command agree about the same spec, and the exit code says
    the run was refused rather than that it broke."""
    from rl_researcher.examples.toy.kind import ToyKind
    from rl_researcher.kinds import Finding

    monkeypatch.setattr(ToyKind, "check",
                        lambda self, s, c=None: [Finding("data", "error", "the data moved")])
    assert check.main([SPEC]) == 1
    assert "run` would refuse this spec (exit 4)" in capsys.readouterr().out

    assert run.main([SPEC, "--max-seconds", "5"]) == 4
    printed = capsys.readouterr().out
    assert "refused:" in printed and "the data moved" in printed

    assert run.main([SPEC, "--max-seconds", "5", "--no-check"]) == 0


def test_the_skills_the_hook_and_the_lessons_are_inside_the_package():
    """They are the package's own assets, and three commands and two checks need them.

    Kept beside the package rather than inside it, they were in the repository and absent from
    every wheel built from it: `install_skills` found none, `install_hooks` exited 1, C10 had
    nothing to compare and C11 fell through to a project file that need not exist. All of it
    invisible from a checkout, which is the only way the package had ever been run.
    """
    from pathlib import Path

    import rl_researcher
    from rl_researcher.spec import load_toml

    pkg = Path(rl_researcher.__file__).resolve().parent
    wanted = [pkg / "lessons.md", pkg / "hooks" / "pre-commit",
              *[pkg / "skills" / n / "SKILL.md" for n in
                ("rl-researcher", "rl-design", "rl-operate", "rl-interpret")]]
    missing = [p for p in wanted if not p.is_file()]
    assert not missing, f"not inside the package, so not in any wheel: {missing}"

    # ... and declared, which is the other half of shipping: a file inside the package that no
    # glob names is still left out of the wheel.
    # Through the package's own loader, which falls back to `tomli` under 3.10 -- `tomllib`
    # is 3.11+, and CI runs both.
    globs = load_toml(pkg.parent / "pyproject.toml")["tool"]["setuptools"]["package-data"]["rl_researcher"]
    for path in wanted:
        rel = path.relative_to(pkg).as_posix()
        assert any(Path(rel).match(g) for g in globs), f"{rel} matches no package-data glob"
