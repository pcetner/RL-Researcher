"""Every check, failing on a fixture built to fail it.

A check nobody has seen fire is a check nobody knows the shape of. Each of the thirteen has a
case here that it refuses, and — where the distinction matters — a neighbouring case it lets
through, because a check that fires on everything is the same as one that fires on nothing.

The fixtures are built in the test rather than committed as files. A committed broken spec is a
thing someone eventually tries to run.
"""

import json

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from pathlib import Path  # noqa: E402

from rl_researcher.checks import REGISTRY, Context, catalogue, check, run_checks  # noqa: E402
from rl_researcher.config import kind_for, load_config, out_dir_for  # noqa: E402
from rl_researcher.examples.toy.kind import write_example_spec  # noqa: E402


def _load(project, extra="", **kw):
    """The toy spec with `extra` spliced in, loaded through the project's config."""
    path = project / "studies" / "toy-line-fit.toml"
    write_example_spec(path, extra=extra, **kw)
    config = load_config()
    kind = kind_for(path, config)
    spec = kind.load(path)
    return config, kind, spec, out_dir_for(spec, config)


def _ids(found):
    return sorted({f.check for f in found if f.level == "error"})


def _all(project, extra="", stages=("load", "check", "pin", "run"), **kw):
    config, kind, spec, out = _load(project, extra, **kw)
    found = []
    for stage in stages:
        found += run_checks(stage, spec, kind, config, out=out, root=Path(project))
    return found


# ── the registry itself ───────────────────────────────────────────────────────────────────

def test_every_check_names_a_stage_a_lesson_and_what_it_asks():
    from rl_researcher.checks import STAGES

    assert len(catalogue()) == 13
    for c in catalogue():
        assert c.stage in STAGES
        assert c.lesson.startswith("L") and len(c.lesson) == 4
        assert c.what and c.what[0].islower()      # a sentence fragment, not a heading
        assert c.fn.__doc__, f"{c.id} does not say why it exists"


def test_a_check_registered_twice_fails_at_import():
    with pytest.raises(ValueError, match="registered twice"):
        check("C01", stage="check", lesson="L001", what="x")(lambda ctx: [])
    with pytest.raises(ValueError, match="unknown stage"):
        check("C99", stage="whenever", lesson="L001", what="x")(lambda ctx: [])
    assert "C99" not in REGISTRY


def test_a_check_that_raises_is_a_warning_and_not_a_refusal(monkeypatch):
    """A broken check must not be able to refuse a run: that turns a bug in the tooling into a
    night of lost compute, which is the exact failure the checks exist to prevent."""
    def explode(ctx):
        raise RuntimeError("the check is wrong")

    monkeypatch.setitem(REGISTRY, "C08", REGISTRY["C08"].__class__(
        id="C08", stage="load", lesson="L008", what="x", fn=explode))
    found = run_checks("load")
    assert [f.level for f in found if f.check == "C08"] == ["warn"]
    assert "the check is wrong" in next(f.message for f in found if f.check == "C08")


# ── one fixture per check ─────────────────────────────────────────────────────────────────

def test_C08_a_silent_run_or_one_that_cannot_be_stopped(project):
    """The spec's own validation already bounds the heartbeat to [1, 300]. C08 is the tighter
    rule -- a run is never silent for more than a minute -- and the rule that a run which cannot
    checkpoint cannot be hot-stopped, which no loader asks."""
    config, kind, spec, out = _load(project)
    assert "C08" not in _ids(run_checks("load", spec, kind, config))     # the toy spec is fine

    spec.cadence.heartbeat_seconds = 120                                 # loadable, still silent
    found = run_checks("load", spec, kind, config)
    assert "C08" in _ids(found)
    assert "never silent for more than a minute" in next(
        f.message for f in found if f.check == "C08")

    spec.cadence.heartbeat_seconds = 30
    spec.cadence.checkpoint_every_steps = 0
    spec.cadence.checkpoint_seconds = 0
    found = run_checks("load", spec, kind, config)
    assert "C08" in _ids(found)
    assert "could not be hot-stopped" in next(f.message for f in found if f.check == "C08")

    spec.cadence.checkpoint_every_steps = 50
    spec.cadence.checkpoint_seconds = spec.budget.max_seconds            # a quarter of the run
    warned = [f for f in run_checks("load", spec, kind, config) if f.check == "C08"]
    assert [f.level for f in warned] == ["warn"]


def test_C13_a_metric_with_no_definition(project):
    """`load_run_spec` refuses this one before C13 sees it — which is the point. C13 is the
    copy for a kind whose loader does not validate, and for a spec built in code.
    """
    from rl_researcher.spec import MetricSpec, SpecError

    config, kind, spec, out = _load(project)
    with pytest.raises(SpecError, match="unknown metric"):
        _load(project, '[[metrics]]\nname = "made_up_number"\ndirection = "report"\nwhy = "x"\n')

    spec.metrics.append(MetricSpec(name="made_up_number", direction="report", why="x"))
    found = run_checks("load", spec, kind, config)
    assert "C13" in _ids(found)
    assert "not in toy's registry" in next(f.message for f in found if f.check == "C13")


def test_C13_warns_but_does_not_refuse_a_metric_with_no_reason(project):
    """A missing definition is a page that cannot be written; a missing `why` is a page that
    reads oddly. One refuses and one does not."""
    config, kind, spec, out = _load(project)
    spec.metrics[0].why = ""
    found = run_checks("load", spec, kind, config)
    assert [f.level for f in found if f.check == "C13"] == ["warn"]


def test_C05_one_seed_that_does_not_declare_itself(project):
    assert "C05" in _ids(_all(project, seeds=(0,)))
    assert "C05" not in _ids(_all(project, "screening = true\n", seeds=(0,)))
    assert "C05" not in _ids(_all(project, seeds=(0, 1, 2)))


def test_C05_does_not_ask_a_deterministic_kind_for_a_spread(project):
    """A measurement is deterministic given its data: a second seed produces the same number."""
    config, kind, spec, out = _load(project, seeds=(0,))

    class Deterministic:
        name = "det"
        compares_seeds = False

    assert "C05" in _ids(run_checks("check", spec, kind, config))
    assert "C05" not in _ids(run_checks("check", spec, Deterministic(), config))


def test_C02_a_comparison_nothing_can_win(project):
    """Phase 4 registered `parked_fraction` against random; every arm scored 0.000 and three
    were marked ✗ for not being strictly below zero."""
    config, kind, spec, out = _load(project)
    m = spec.metrics[0]
    m.compare_to, m.bar = "nobody", None
    assert "C02" in _ids(run_checks("check", spec, kind, config))

    m.compare_to, m.direction = "noisy", "report"
    found = run_checks("check", spec, kind, config)
    assert "C02" in _ids(found)
    assert "no arm can win or lose" in next(f.message for f in found if f.check == "C02")

    m.direction, m.baseline = "lower", ""
    warned = [f for f in run_checks("check", spec, kind, config) if f.check == "C02"]
    assert [f.level for f in warned] == ["warn"]            # names no expected value
    m.baseline = "noisy scores about 0.9"
    assert not [f for f in run_checks("check", spec, kind, config) if f.check == "C02"]


def test_C04_an_anchor_that_is_not_in_this_spec(project):
    """A new arm compared against an old study's number is compared against noise: the
    run-to-run spread is two to three times the seed spread."""
    config, kind, spec, out = _load(project)
    spec.metrics[0].anchor_of = "study3-action-visible/conv_ae"
    found = run_checks("check", spec, kind, config)
    assert "C04" in _ids(found)
    assert "not an arm here" in next(f.message for f in found if f.check == "C04")

    spec.metrics[0].anchor_of = "an-old-study/ols"           # `ols` is an arm here
    assert "C04" not in _ids(run_checks("check", spec, kind, config))


def test_C03_a_bar_above_everything_the_instrument_has_seen(project):
    """Six cells failing a bar by the same distance discriminate nothing."""
    from rl_researcher.ledger import Finding, open_ledger

    config, kind, spec, out = _load(project)
    kind.instrument_for = lambda metric: "capacity-probe"    # type: ignore[assignment]
    ledger = open_ledger(config)
    ledger.extend([Finding(kind="post-hoc", run="capacity-probe", metric="r2", value=0.4, n=1)])

    m = next(x for x in spec.metrics if x.name == "r2")
    m.direction, m.bar = "higher", 0.9
    found = run_checks("check", spec, kind, config)
    assert "C03" in _ids(found)
    assert "never seen above 0.4" in next(f.message for f in found if f.check == "C03")

    m.bar = 0.3                                              # under what has been measured
    assert "C03" not in _ids(run_checks("check", spec, kind, config))


def test_C01_a_bar_registered_with_no_instrument_measurement_on_file(project):
    config, kind, spec, out = _load(project)
    kind.instrument_for = lambda metric: "capacity-probe"    # type: ignore[assignment]
    found = [f for f in run_checks("pin", spec, kind, config) if f.check == "C01"]
    assert found and found[0].level == "warn"
    assert "has no ledger row" in found[0].message

    from rl_researcher.ledger import Finding, open_ledger

    open_ledger(config).extend([Finding(kind="post-hoc", run="capacity-probe",
                                        metric="r2", value=0.9, n=1)])
    assert not [f for f in run_checks("pin", spec, kind, config) if f.check == "C01"]


def test_C06_staging_a_run_whose_lock_is_alive(project, monkeypatch):
    """A lock committed to the repository looks alive to every later checkout."""
    import rl_researcher.checks as checks

    config, kind, spec, out = _load(project)
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(checks, "lock_holder" if hasattr(checks, "lock_holder") else "_nothing",
                        None, raising=False)
    monkeypatch.setattr("rl_researcher.lock.lock_holder", lambda p: "pid 1234 on this machine")
    monkeypatch.setattr(checks, "staged_under", lambda p, root=None: ["docs/toy/x/results.json"])
    found = run_checks("run", spec, kind, config, out=out)
    assert "C06" in _ids(found)
    assert "half-written state" in next(f.message for f in found if f.check == "C06")

    monkeypatch.setattr("rl_researcher.lock.lock_holder", lambda p: None)
    assert "C06" not in _ids(run_checks("run", spec, kind, config, out=out))


def test_C09_a_canary_older_than_the_thing_it_was_meant_to_check(project):
    config, kind, spec, out = _load(project)
    config.canary.spec = "toy-canary"
    found = [f for f in run_checks("run", spec, kind, config, out=out) if f.check == "C09"]
    assert found and found[0].level == "warn" and "no canary result" in found[0].message

    ledger_dir = config.path("ledger")
    ledger_dir.mkdir(parents=True, exist_ok=True)
    (ledger_dir / "canary.json").write_text(json.dumps({"commit": "0" * 40}), encoding="utf-8")
    assert not [f for f in run_checks("run", spec, kind, config, out=out) if f.check == "C09"]


def test_C07_a_number_in_a_reading_with_no_finding_behind_it(project):
    """A number in an authored region with no id is a number nobody can trace to an estimator."""
    config, kind, spec, out = _load(project)
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(
        "<!-- rl: kind=report run=toy -->\n# toy\n\n## Registered metrics\n\n"
        "<!-- generated: metrics -->\n| metric | target |\n|---|---|\n| slope_error | < 0.1 |\n"
        "<!-- /generated -->\n\n## Reading\n\n<!-- authored: reading -->\n"
        "The ols arm reached a slope_error of 0.021, which clears the bar.\n"
        "<!-- /authored -->\n", encoding="utf-8")
    found = [f for f in run_checks("lint", spec, kind, config, out=out) if f.check == "C07"]
    assert found and "slope_error of 0.021" in found[0].message

    (out / "README.md").write_text(
        (out / "README.md").read_text(encoding="utf-8").replace("clears the bar",
                                                                "clears the bar [F0007]"),
        encoding="utf-8")
    assert not [f for f in run_checks("lint", spec, kind, config, out=out) if f.check == "C07"]


def test_C12_a_diagnosis_that_never_says_post_hoc(project):
    config, kind, spec, out = _load(project)
    out.mkdir(parents=True, exist_ok=True)
    (out / "why.md").write_text("<!-- rl: kind=diagnosis run=toy -->\n# why\n\nIt was the data.\n",
                                encoding="utf-8")
    assert "C12" in _ids(run_checks("lint", spec, kind, config, out=out))

    (out / "why.md").write_text("<!-- rl: kind=diagnosis run=toy -->\n# why\n\n"
                                "Post-hoc: written after seeing the null.\n", encoding="utf-8")
    assert "C12" not in _ids(run_checks("lint", spec, kind, config, out=out))


def test_C10_an_invariants_block_that_drifted(tmp_path):
    """Four copies of a rule is four rules, and the one that drifts is the one being read."""
    skills = tmp_path / "skills"
    for i in (0, 1):
        d = skills / f"rl-s{i}"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x\n<!-- invariants -->\nthe same\n<!-- invariants -->\n",
                                    encoding="utf-8")
    assert "C10" not in _ids(run_checks("ci", root=tmp_path))

    (skills / "rl-s1" / "SKILL.md").write_text(
        "x\n<!-- invariants -->\ndrifted\n<!-- invariants -->\n", encoding="utf-8")
    found = run_checks("ci", root=tmp_path)
    assert "C10" in _ids(found)
    assert "four copies of a rule is four rules" in next(
        f.message for f in found if f.check == "C10")


def test_C10_a_shipped_skill_with_no_invariants_block_at_all(tmp_path):
    d = tmp_path / "skills" / "rl-only"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("no block here\n", encoding="utf-8")
    found = run_checks("ci", root=tmp_path)
    assert "C10" in _ids(found)
    assert "has no" in next(f.message for f in found if f.check == "C10")


def test_a_project_skill_beside_the_shipped_ones_is_not_asked_for_a_copy(tmp_path):
    """Auto-SM64 keeps a skill for its own instruments and its engine. It extends the four
    rather than repeating their rules, so C10 does not ask it for the block."""
    skills = tmp_path / "skills"
    (skills / "rl-one").mkdir(parents=True)
    (skills / "rl-one" / "SKILL.md").write_text(
        "x\n<!-- invariants -->\nthe rules\n<!-- invariants -->\n", encoding="utf-8")
    (skills / "autosm64").mkdir(parents=True)
    (skills / "autosm64" / "SKILL.md").write_text("no block, and that is fine\n",
                                                  encoding="utf-8")
    assert "C10" not in _ids(run_checks("ci", root=tmp_path))


def test_C11_the_two_lists_are_each_others_index():
    """A check whose lesson was deleted is a rule with no reason on file."""
    assert not _ids(run_checks("ci"))                        # the shipped pair agree

    ctx = Context(root=Path("nowhere-at-all"))
    assert ctx.path("lessons") is None


def test_the_shipped_skills_are_under_a_hundred_and_twenty_lines():
    """A skill nobody finishes reading is a skill nobody follows."""
    root = Path(__file__).resolve().parent.parent / "skills"
    files = sorted(root.rglob("SKILL.md"))
    assert len(files) == 4
    for path in files:
        n = len(path.read_text(encoding="utf-8").splitlines())
        assert n <= 120, f"{path.parent.name} is {n} lines"


def test_every_shipped_skill_carries_the_same_invariants_and_says_the_hard_rules():
    root = Path(__file__).resolve().parent.parent / "skills"
    blocks = set()
    for path in sorted(root.rglob("SKILL.md")):
        text = path.read_text(encoding="utf-8")
        blocks.add(text.split("<!-- invariants -->")[1])
        assert text.startswith("---\nname: ")                # a skill's own front matter
    assert len(blocks) == 1
    block = blocks.pop()
    for rule in ("The human decides", "never silent", "survives a shutdown",
                 "Register before you measure", "reproducible from disk"):
        assert rule in block, rule


# ── the seam the runner and `check` both go through ───────────────────────────────────────

def test_the_registry_reaches_the_run_path_and_the_check_command(project, capsys):
    """`findings.staged_checks` imports the registry by name inside a try/except, so a rename
    fails nothing and silently costs every run its checks."""
    from rl_researcher.check import main as check_main
    from rl_researcher.findings import collect

    config, kind, spec, out = _load(project, seeds=(0,))
    assert "C05" in _ids(collect(kind, spec, config, out))
    assert check_main(["toy-line-fit"]) == 1
    said = capsys.readouterr().out
    assert "[C05]" in said and "`run` would refuse" in said


def test_lint_asks_the_project_wide_checks_and_the_per_spec_ones(project, capsys):
    from rl_researcher.lint import main as lint_main

    _load(project, seeds=(0,))
    assert lint_main([]) == 1
    said = capsys.readouterr().out
    assert "[C05]" in said and "toy-line-fit:" in said
    assert "error(s)" in said

    _load(project, seeds=(0, 1, 2))
    assert lint_main(["--stage", "ci"]) == 0
