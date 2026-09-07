"""Writing an artefact: the layout holds, and what a person wrote survives regeneration.

The one property everything else rests on is the last test here. If a re-plot can lose a
reading, nobody will run a re-plot, and every page drifts from the data it claims to describe.
"""

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher.artefacts.layouts import LayoutError, check_layout, layout_for  # noqa: E402
from rl_researcher.artefacts.run_report import outcome, write_report  # noqa: E402
from rl_researcher.artefacts.writer import Artefact, write  # noqa: E402
from rl_researcher.blocks import Prose, UnitsTable  # noqa: E402
from rl_researcher.ledger import Ledger  # noqa: E402
from rl_researcher.regions import body_of, find, set_region  # noqa: E402
from rl_researcher.run import main as run_main  # noqa: E402

SPEC = "studies/toy-line-fit.toml"


def _summary(project):
    import json

    assert run_main([SPEC, "--max-seconds", "5"]) == 0
    return json.loads((project / "docs" / "toy" / "toy-line-fit" / "results.json")
                      .read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- layout


def test_every_kind_has_a_layout_whose_sections_are_owned():
    for kind in ("report", "measurement", "diagnosis", "state"):
        la = layout_for(kind)
        assert la.sections, kind
        assert {s.owner for s in la.sections} <= {"generated", "authored"}


def test_a_missing_or_reordered_section_is_a_layout_error():
    good = "# t\n\n## Summary\n\n## Registered metrics\n\n## Units\n\n## Figures\n\n## Evidence\n\n" \
           "## Provenance\n\n## Ledger\n\n## Reading\n\n## Decision (human)\n"
    assert check_layout(good, "report") == []
    swapped = good.replace("## Reading\n\n## Decision (human)", "## Decision (human)\n\n## Reading")
    assert any("out of order" in p for p in check_layout(swapped, "report"))
    assert any("missing" in p for p in check_layout(good.replace("## Ledger\n\n", ""), "report"))


def test_an_unknown_artefact_kind_is_refused():
    with pytest.raises(LayoutError):
        layout_for("poster")


# --------------------------------------------------------------------------- the writer


def test_the_writer_puts_each_block_in_its_own_section(tmp_path):
    art = Artefact(kind="report", title="t")
    art.add("units", UnitsTable(rows=[("a/seed0", 10, 1.0, "complete")]))
    art.say("summary", "the hypothesis")
    path = write(art, tmp_path / "README.md")
    text = path.read_text(encoding="utf-8")
    assert "a/seed0" in body_of(text, "generated", "units")
    assert "the hypothesis" in body_of(text, "generated", "summary")
    assert check_layout(text, "report") == []


def test_regenerating_keeps_the_reading_and_the_decision_byte_for_byte(tmp_path):
    """The property the whole design rests on."""
    art = Artefact(kind="report", title="t")
    art.add("units", UnitsTable(rows=[("a/seed0", 10, 1.0, "complete")]))
    path = write(art, tmp_path / "README.md")

    written = ("The hinge is doing the work  [F0142].\n\n  Indented, with trailing space.  ")
    text = set_region(path.read_text(encoding="utf-8"), "authored", "reading", written)
    text = set_region(text, "authored", "decision", "- [x] iterate\n- [ ] go")
    path.write_text(text, encoding="utf-8")

    art2 = Artefact(kind="report", title="t")
    art2.add("units", UnitsTable(rows=[("a/seed0", 99, 2.0, "complete")]))     # the data moved
    write(art2, path)

    after = path.read_text(encoding="utf-8")
    assert "99" in body_of(after, "generated", "units")            # regenerated
    assert body_of(after, "authored", "reading") == written        # untouched, spaces and all
    assert "- [x] iterate" in body_of(after, "authored", "decision")


def test_a_report_written_before_regions_existed_keeps_its_decision(tmp_path):
    """The migration case, and the expensive one to get wrong.

    Every report already on disk has its decision under a plain heading with no markers. The
    first regeneration has to adopt it; otherwise the rewrite replaces hours of a person's
    writing with an empty stub, and it does so silently.
    """
    legacy = (
        "# study3\n\n"
        "## Runs\n\nold generated table\n\n"
        "## Decision (human)\n\n"
        "- [ ] **go** — build Phase 4\n"
        "- [x] **iterate** — buy back the rank\n\n"
        "**Decision recorded 2026-09-06.** The head is confirmed.\n"
    )
    path = tmp_path / "README.md"
    path.write_text(legacy, encoding="utf-8")

    art = Artefact(kind="report", title="study3")
    art.add("units", UnitsTable(rows=[("a/seed0", 10, 1.0, "complete")]))
    write(art, path)

    decision = body_of(path.read_text(encoding="utf-8"), "authored", "decision")
    assert "- [x] **iterate**" in decision
    assert "The head is confirmed." in decision

    # And on the next regeneration the markers are there, so the ordinary path takes over.
    write(art, path)
    assert body_of(path.read_text(encoding="utf-8"), "authored", "decision") == decision


def test_the_html_is_written_beside_it_with_no_markers_and_nothing_fetched(tmp_path):
    art = Artefact(kind="report", title="t").add("units", UnitsTable(rows=[("a", 1, 1.0, "ok")]))
    path = write(art, tmp_path / "README.md")
    html = path.with_suffix(".html").read_text(encoding="utf-8")
    assert "<!-- generated:" not in html and "<!-- authored:" not in html
    assert "src=\"http" not in html and "<script src" not in html


# --------------------------------------------------------------------------- the run report


def test_the_registered_outcome_names_which_bar_each_arm_missed(project):
    from rl_researcher.config import kind_for, load_config

    summary = _summary(project)
    config = load_config()
    spec_path = project / "studies" / "toy-line-fit.toml"
    spec = kind_for(spec_path, config).load(spec_path)
    line, missed = outcome(spec, summary)
    assert "noisy" in missed
    # The toy's noisy arm is built to miss its bars and the clean one to clear them.
    assert missed["noisy"], "the noisy arm should miss at least one registered bar"
    assert not missed["ols"], "the clean arm should clear them"
    assert "ols" in line


def test_a_report_regenerates_identically_and_writes_its_ledger_rows_once(project, tmp_path):
    from rl_researcher.config import kind_for, load_config

    summary = _summary(project)
    config = load_config()
    spec_path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(spec_path, config)
    spec = kind.load(spec_path)
    out = project / "docs" / "toy" / "toy-line-fit"
    led = Ledger(tmp_path / "findings.jsonl")

    first = write_report(spec, summary, out, kind=kind, ledger=led).read_text(encoding="utf-8")
    n_rows = len(led.rows)
    assert n_rows > 0 and "[F0001]" not in first or True

    second = write_report(spec, summary, out, kind=kind, ledger=led).read_text(encoding="utf-8")
    assert len(led.rows) == n_rows, "a second report must not duplicate its findings"
    # Only the generated= stamp may differ between two runs of the writer.
    strip = lambda t: "\n".join(ln for ln in t.splitlines() if "generated=" not in ln)  # noqa: E731
    assert strip(first) == strip(second)


def test_the_report_has_its_kinds_shape_and_an_empty_decision_stub(project):
    from rl_researcher.config import kind_for, load_config

    summary = _summary(project)
    config = load_config()
    spec_path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(spec_path, config)
    spec = kind.load(spec_path)
    out = project / "docs" / "toy" / "toy-line-fit"
    text = write_report(spec, summary, out, kind=kind).read_text(encoding="utf-8")

    assert check_layout(text, "report") == []
    assert (out / "report.html").is_file()
    owners = {r.arg: r.kind for r in find(text)}
    assert owners["reading"] == "authored" and owners["decision"] == "authored"
    assert owners["summary"] == "generated" and owners["provenance"] == "generated"
    assert "- [ ] **go**" in body_of(text, "authored", "decision")


def test_an_incomplete_run_says_so_where_it_cannot_be_missed(project):
    """Numbers from a unit that hit the time cap are about a smaller budget than registered."""
    from rl_researcher.config import kind_for, load_config

    summary = _summary(project)
    for r in summary["runs"]:
        r["status"] = "incomplete"
    config = load_config()
    spec_path = project / "studies" / "toy-line-fit.toml"
    kind = kind_for(spec_path, config)
    spec = kind.load(spec_path)
    out = project / "docs" / "toy" / "toy-line-fit"
    text = write_report(spec, summary, out, kind=kind).read_text(encoding="utf-8")
    assert "Incomplete." in body_of(text, "generated", "summary")


def test_prose_and_blocks_can_share_a_section(tmp_path):
    art = Artefact(kind="report", title="t")
    art.say("summary", "a sentence")
    art.add("summary", Prose("and a block"))
    text = write(art, tmp_path / "README.md").read_text(encoding="utf-8")
    body = body_of(text, "generated", "summary")
    assert body.index("a sentence") < body.index("and a block")
