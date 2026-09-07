"""The ledger, the plan sync, the state page and the decision.

These four are one mechanism: a number is written once with its provenance, cited everywhere by
id, regenerated into the plan, and surfaced on the state page until a person decides about it.
The tests are about the ways that mechanism can quietly lie.
"""

import json

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher import decide, run, state  # noqa: E402
from rl_researcher.ledger import Finding, Ledger, open_ledger, touches_match  # noqa: E402
from rl_researcher.ledger_cli import backfill  # noqa: E402
from rl_researcher.plan_sync import best_per_run, lines_for, sync  # noqa: E402
from rl_researcher.regions import set_region  # noqa: E402

SPEC = "studies/toy-line-fit.toml"


def _f(**kw) -> Finding:
    base = dict(kind="registered", run="r1", unit="a", metric="r2", value=0.9, n=3,
                direction="higher", bar=0.5, passed=True, commit="abc", fingerprint="f1",
                date="2026-01-01")
    base.update(kw)
    return Finding(**base)


# --------------------------------------------------------------------------- the ledger


def test_the_same_claim_is_never_written_twice(tmp_path):
    """Regenerating a report must not duplicate its rows; identity is what stops it."""
    led = Ledger(tmp_path / "findings.jsonl")
    first = led.add(_f())
    again = led.add(_f(value=0.9))
    assert again.id == first.id
    assert len(Ledger(tmp_path / "findings.jsonl").rows) == 1


def test_a_row_measured_at_a_different_commit_is_a_different_claim(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    led.add(_f())
    led.add(_f(commit="def", value=0.7))
    assert len(led.rows) == 2 and {r.value for r in led.rows} == {0.9, 0.7}


def test_ids_keep_counting_across_a_reopen(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    led.add(_f())
    assert Ledger(tmp_path / "findings.jsonl").next_id() == "F0002"


def test_a_correction_supersedes_and_neither_row_is_lost(tmp_path):
    """A ledger that quietly drops its mistakes cannot be used to check a plan."""
    led = Ledger(tmp_path / "findings.jsonl")
    old = led.add(_f(value=0.9))
    new = led.supersede(old.id, _f(commit="def", value=0.2, passed=False))
    assert old.id in new.supersedes
    assert led.superseded == {old.id}
    assert len(led.rows) == 2
    assert "~~" in "\n".join(lines_for(led))          # struck through, still there


def test_superseding_something_that_is_not_there_is_refused(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    with pytest.raises(ValueError):
        led.supersede("F9999", _f())


def test_a_decision_label_matches_its_own_row_and_not_a_longer_numbered_one():
    """The specs write the decision the way a person says it — "D4 (reconstruction-free
    representation)" — and a query asks for "D4". So the label matches when what follows it is
    not part of a label: D1 must not pick up D10, while §8 does cover §8.4, because a section
    contains its subsections and a region under §8 wants them."""
    full = ["D10 (masked-latent objective)", "§8.4 (the reward gate's thresholds)"]
    assert touches_match(full, "D10") and touches_match(full, "§8.4")
    assert touches_match(full, "§8")
    assert not touches_match(full, "D1")


def test_rows_that_are_not_comparable_get_a_banner(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    led.add(_f(data="snapA@1111", budget="10000 steps"))
    led.add(_f(unit="b", data="snapB@2222", budget="20000 steps"))
    notes = led.mixed(led.rows)
    assert any("data" in n for n in notes) and any("budget" in n for n in notes)


def test_best_per_run_takes_the_metrics_own_direction():
    lower = [_f(run="r1", unit="a", direction="lower", value=0.4),
             _f(run="r1", unit="b", direction="lower", value=0.1)]
    assert best_per_run(lower)[0].unit == "b"
    higher = [_f(run="r2", unit="a", value=0.4), _f(run="r2", unit="b", value=0.9)]
    assert best_per_run(higher)[0].unit == "b"


# --------------------------------------------------------------------------- plan sync


PLAN = """# Plan

## D4

Prose a person wrote, with a  double space and a trailing tab.\t

<!-- ledger: touches=D4 metric=r2 best=run -->
<!-- /ledger -->

## D5

More prose. No region here.
"""


def test_the_sync_rewrites_only_inside_the_markers(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    led.add(_f(touches=["D4 (reconstruction-free)"], value=0.91))
    plan = tmp_path / "PLAN.md"
    plan.write_text(PLAN, encoding="utf-8")
    changed, diff = sync(plan, led)
    out = plan.read_text(encoding="utf-8")
    assert changed and "F0001" in out and "0.910" in out
    assert "Prose a person wrote, with a  double space and a trailing tab.\t" in out
    assert "More prose. No region here." in out
    assert "-Prose a person wrote" not in diff        # nothing of theirs was removed


def test_the_sync_is_idempotent_and_check_says_so(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    led.add(_f(touches=["D4"], value=0.91))
    plan = tmp_path / "PLAN.md"
    plan.write_text(PLAN, encoding="utf-8")
    sync(plan, led)
    changed, _ = sync(plan, led, check=True)
    assert not changed


def test_check_refuses_to_write(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    led.add(_f(touches=["D4"], value=0.91))
    plan = tmp_path / "PLAN.md"
    plan.write_text(PLAN, encoding="utf-8")
    changed, diff = sync(plan, led, check=True)
    assert changed and diff and plan.read_text(encoding="utf-8") == PLAN


def test_a_region_with_no_findings_says_so_rather_than_going_blank(tmp_path):
    led = Ledger(tmp_path / "findings.jsonl")
    plan = tmp_path / "PLAN.md"
    plan.write_text(PLAN, encoding="utf-8")
    sync(plan, led)
    assert "_No findings touch this yet._" in plan.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- state, decide


def _finish_a_run(project):
    assert run.main([SPEC, "--max-seconds", "5"]) == 0
    return project / "docs" / "toy" / "toy-line-fit"


def test_backfill_writes_a_row_per_arm_and_metric_and_repeats_safely(project, capsys):
    _finish_a_run(project)
    from rl_researcher.config import load_config

    config = load_config()
    led = open_ledger(config)
    first = backfill(config, led)
    assert first, "a finished run should produce registered rows"
    assert {r.kind for r in first} == {"registered"}
    assert backfill(config, led) == []           # nothing new the second time
    assert len(open_ledger(config).rows) == len(first)


def test_a_finished_run_with_an_empty_stub_is_awaiting_and_a_ticked_one_is_not(project, capsys):
    out = _finish_a_run(project)
    report = out / "README.md"
    report.write_text("# toy\n\n## Decision\n\n<!-- authored: decision -->\n"
                      "- [ ] go\n- [ ] iterate\n<!-- /authored -->\n", encoding="utf-8")

    assert state.main([]) == 0
    view = json.loads((project / "docs" / "state.json").read_text(encoding="utf-8"))
    assert [w["run"] for w in view["waiting"]] == ["toy-line-fit"]
    assert view["waiting"][0]["options"] == ["go", "iterate"]

    report.write_text(set_region(report.read_text(encoding="utf-8"), "authored", "decision",
                                 "- [x] go\n- [ ] iterate"), encoding="utf-8")
    assert state.main([]) == 0
    view = json.loads((project / "docs" / "state.json").read_text(encoding="utf-8"))
    assert view["waiting"] == []


def test_decide_refuses_until_a_person_has_ticked_a_box(project, capsys):
    out = _finish_a_run(project)
    report = out / "README.md"
    report.write_text("# toy\n\n<!-- authored: decision -->\n- [ ] go\n- [ ] stop\n<!-- /authored -->\n",
                      encoding="utf-8")
    assert decide.main([SPEC]) == 1
    said = capsys.readouterr().out
    assert "no box is ticked" in said and "go, stop" in said

    report.write_text(set_region(report.read_text(encoding="utf-8"), "authored", "decision",
                                 "- [x] stop"), encoding="utf-8")
    assert decide.main([SPEC, "--note", "the bar was wrong"]) == 0
    said = capsys.readouterr().out
    assert "decided" in said and "stop" in said

    from rl_researcher.config import load_config

    rows = open_ledger(load_config()).query(kind="decision")
    assert len(rows) == 1 and rows[0].note == "the bar was wrong"


def test_the_state_page_is_written_as_markdown_json_and_html(project):
    _finish_a_run(project)
    assert state.main([]) == 0
    docs = project / "docs"
    assert (docs / "STATE.md").is_file() and (docs / "state.json").is_file()
    html = (docs / "state.html").read_text(encoding="utf-8")
    assert "<!doctype html>" in html and "kind-state" in html
    assert "<!-- ledger:" not in html and "<!-- generated:" not in html


def test_a_result_says_which_framework_produced_it(project):
    """The commit in a summary is the *consuming project's*. Nothing said which version of this
    package computed the numbers, though the README's whole argument for pinning a commit is
    that a run cannot otherwise be reproduced from the two repositories alone.
    """
    from rl_researcher.config import kind_for, load_config, out_dir_for
    from rl_researcher.ledger import open_ledger
    from rl_researcher.report import main as report_main
    from rl_researcher.runner import run

    config = load_config()
    path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(path, config)
    spec = kind.load(path)
    out = out_dir_for(spec, config)
    summary = run(spec, kind, out, config=config, log=lambda _s: None)

    stamp = summary.get("rl_researcher")
    assert stamp and stamp.startswith("0."), f"the summary does not name the framework: {stamp!r}"

    assert report_main([spec.name]) == 0
    rows = open_ledger(config).query(run=spec.name)
    assert rows and all(r.framework == stamp for r in rows), \
        "a ledger row does not carry the framework that computed it"
    assert stamp in (out / "README.md").read_text(encoding="utf-8")


def test_two_writers_never_mint_the_same_finding_id(tmp_path):
    """A watcher tick acting on a finished run while someone runs `report` by hand is two
    writers. Reading the rows once at open and appending later gave both the same `F####`, and
    an append-only file cannot tell two rows with one id apart afterwards."""
    import threading

    from rl_researcher.ledger import Finding, Ledger

    path = tmp_path / "findings.jsonl"
    n = 12
    ready = threading.Barrier(n)

    def write(i):
        ready.wait()
        Ledger(path).add(Finding(kind="post-hoc", run=f"r{i}", metric="m", value=float(i)))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    ids = [r.id for r in Ledger(path).rows]
    assert len(ids) == n, f"{n} rows written, {len(ids)} on file"
    assert len(set(ids)) == n, f"duplicate ids: {sorted(ids)}"


def test_the_state_page_is_built_by_the_writer_like_every_other_artefact(project):
    """It was assembled here as a list of strings, so the `STATE` layout declared five regions
    that nothing emitted and this was the one document whose shape was never checked against
    its own kind."""
    from rl_researcher.artefacts.layouts import LAYOUTS, check_layout
    from rl_researcher.regions import find

    _finish_a_run(project)
    assert state.main([]) == 0
    text = (project / "docs" / "STATE.md").read_text(encoding="utf-8")

    assert check_layout(text, "state") == []
    regions = {r.arg for r in find(text) if r.kind == "generated"}
    assert regions == {s.region for s in LAYOUTS["state"].sections}
    assert "kind=state" in text.split("\n", 1)[0]
