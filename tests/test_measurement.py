"""A measurement, end to end: through the runner, onto disk, and back off it again.

The four scripts this replaces each grew their own heartbeat, their own log, their own output
layout and their own markdown writer, and each of those four sets had drifted from the others.
What is checked here is that a measurement gets the framework's versions and adds only the
thing that is its own: what it asked, what it computed, and what it concluded.

The property that matters most is the last one — that the document regenerates from the JSON on
disk with no re-measuring. A page only the process that produced it can produce is a page
nobody can check.
"""

import json

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from pathlib import Path  # noqa: E402

from rl_researcher.artefacts.layouts import check_layout  # noqa: E402
from rl_researcher.artefacts.measurement import write_measurement  # noqa: E402
from rl_researcher.config import load_config  # noqa: E402
from rl_researcher.measurement import UNIT, MeasurementKind  # noqa: E402
from rl_researcher.runner import run  # noqa: E402
from rl_researcher.spec import MetricRegistry, RunSpec  # noqa: E402
from rl_researcher.units import unit_dir  # noqa: E402

SPEC = '''name = "toy-measure"
kind = "measure"
seeds = [0]
hypothesis = """
Nothing is predicted here. The question is whether the held-out half of the data is drawn from
the same distribution as the training half, which is a fact about the data and not about a model.
"""

# A measurement registers no bars -- there is nothing to pass -- but it still declares what it
# will report, which is what stops anyone measuring first and choosing the number afterwards.
[[metrics]]
name = "total"
direction = "report"
why = "the sum the question is about"
'''


class Counting(MeasurementKind):
    """A measurement over nothing: it counts, which is enough to check the machinery."""

    name = "measure"
    # A measurement declares what its numbers mean like any other kind: the registry is what
    # makes `total` a quantity with a definition rather than a variable name in a table.
    registry = MetricRegistry(known={"total": "the sum of the per-key values"},
                              titles={"total": "Total"})
    calls = 0

    def measure(self, spec, prepared, ctx):
        Counting.calls += 1
        ctx.beat("running", step=1, elapsed_seconds=0.1)
        return {"rows": [("a", 1.5), ("b", 2.5)], "total": 4.0, "snapshot": "toy@abcdef",
                "device": "cpu", "calls": Counting.calls}

    def metrics_of(self, payload):
        return {"total": float(payload["total"])}

    def question(self, spec, payload):
        return "Do the two halves of the toy data have the same total?"

    def method(self, spec, payload):
        return [("Rows", str(len(payload.get("rows") or []))),
                ("Snapshot", payload.get("snapshot", "—"))]

    def tables(self, spec, payload):
        body = "| key | value |\n|---|---:|\n" + "\n".join(
            f"| {k} | {v:.2f} |" for k, v in payload["rows"])
        return [("Per key", body)]

    def verdict(self, spec, payload):
        return f"The two halves sum to {payload['total']:.2f}."

    def findings(self, spec, payload):
        return [{"metric": "total", "value": float(payload["total"]), "n": 1,
                 "estimator": "tests.test_measurement.Counting.measure"}]


@pytest.fixture
def measured(project):
    """A project with a measurement spec registered, run once."""
    cfg = project / "rl-researcher.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        'toy = "rl_researcher.examples.toy.kind:ToyKind"',
        'toy = "rl_researcher.examples.toy.kind:ToyKind"\n'
        'measure = "tests.test_measurement:Counting"').replace(
        'toy = "docs/toy"', 'toy = "docs/toy"\nmeasure = "docs/measurements"'),
        encoding="utf-8")
    (project / "studies" / "toy-measure.toml").write_text(SPEC, encoding="utf-8")
    config = load_config()
    kind = Counting()
    spec = kind.load(project / "studies" / "toy-measure.toml")
    out = config.out_root("measure") / spec.name
    Counting.calls = 0
    run(spec, kind, out, config=config, log=lambda _s: None)
    return config, kind, spec, out


def test_a_measurement_is_one_unit_with_no_arms_and_no_seeds(measured):
    config, kind, spec, out = measured
    assert kind.units(spec) == [UNIT]
    assert (unit_dir(out, UNIT) / "results.json").is_file()
    assert (unit_dir(out, UNIT) / "progress.json").is_file()   # the framework's heartbeat


def test_it_gets_the_frameworks_log_lock_and_provenance_rather_than_its_own(measured):
    config, kind, spec, out = measured
    assert (out / kind.log_name).is_file()
    summary = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert summary["git_sha"] and summary["run"] == spec.name
    assert not (out / ".study-lock.json").exists()             # released, not left behind


def test_the_document_has_the_measurement_layout_and_not_a_reports(measured):
    """A measurement laid out as a report presents numbers that answer a question as though
    they had settled a registered comparison."""
    config, kind, spec, out = measured
    text = (out / "README.md").read_text(encoding="utf-8")
    assert check_layout(text, "measurement") == []
    assert check_layout(text, "report") != []
    assert "kind=measurement" in text
    for heading in ("## Question", "## Method", "## Result", "## Verdict"):
        assert heading in text
    assert "Registered metrics" not in text


def test_what_it_asked_what_it_computed_and_what_it_concluded_are_all_on_the_page(measured):
    config, kind, spec, out = measured
    text = (out / "README.md").read_text(encoding="utf-8")
    assert "same total?" in text
    assert "| a | 1.50 |" in text and "| b | 2.50 |" in text
    assert "sum to 4.00" in text
    assert "**Trains.** nothing" in text


def test_the_document_regenerates_from_disk_without_measuring_again(measured):
    """A page only the process that produced it can produce is a page nobody can check."""
    from rl_researcher.report import main as report_main

    config, kind, spec, out = measured
    assert Counting.calls == 1
    from rl_researcher.ledger import open_ledger

    first = (out / "README.md").read_text(encoding="utf-8")
    before = len(open_ledger(config).rows)
    assert report_main(["toy-measure"]) == 0
    again = (out / "README.md").read_text(encoding="utf-8")
    assert Counting.calls == 1                                 # nothing was recomputed
    assert len(open_ledger(config).rows) == before             # ... and no row was duplicated
    strip = lambda t: "\n".join(ln for ln in t.splitlines() if "generated=" not in ln)  # noqa: E731
    assert strip(first) == strip(again)


def test_a_reading_written_into_it_survives_regeneration(measured):
    config, kind, spec, out = measured
    readme = out / "README.md"
    text = readme.read_text(encoding="utf-8").replace(
        "_(write here: what this measurement cannot show. It answers one "
        "question about data that already exists; it registers nothing.)_",
        "This cannot show *why* the halves agree, only that they do.")
    readme.write_text(text, encoding="utf-8")
    payload = kind.payload_of(json.loads((out / "results.json").read_text(encoding="utf-8")))
    write_measurement(spec, payload, out, kind=kind, ledger=None)
    assert "only that they do" in readme.read_text(encoding="utf-8")


def test_its_ledger_rows_are_post_hoc_because_nothing_about_it_was_registered(measured):
    from rl_researcher.ledger import open_ledger

    config, kind, spec, out = measured
    rows = open_ledger(config).query(run="toy-measure")
    assert rows and all(r.kind == "post-hoc" for r in rows)
    assert {r.metric for r in rows} == {"total"}
    assert all(r.estimator.endswith("Counting.measure") for r in rows)
    assert all(r.bar is None and r.passed is None for r in rows)


def test_a_measurement_that_cannot_calibrate_says_so_above_its_own_numbers(project):
    """Not in a line the reader reaches after believing them."""
    class Uncalibrated(Counting):
        def notices(self, spec, payload):
            return ["the self-test predicted 1.0 and got 0.4; every number below is suspect"]

    spec = RunSpec(name="m", kind="measure", hypothesis="h", seeds=[0], metrics=[])  # noqa: E501
    out = Path(project) / "m"
    out.mkdir()
    text = write_measurement(spec, {"rows": [], "total": 0.0}, out,
                             kind=Uncalibrated()).read_text(encoding="utf-8")
    body = text.split("## Result")[1]
    assert "Not calibrated." in body and "every number below is suspect" in body


def test_a_kind_that_declares_almost_nothing_still_gets_a_document(project):
    """A measurement is written by a person in a hurry after a null. What it does not declare
    must cost it that line, not the document."""
    class Bare(MeasurementKind):
        name = "bare"

    spec = RunSpec(name="bare-run", kind="bare", hypothesis="Is it?", seeds=[0], metrics=[])
    out = Path(project) / "bare"
    out.mkdir()
    text = write_measurement(spec, {}, out, kind=Bare()).read_text(encoding="utf-8")
    assert check_layout(text, "measurement") == []
    assert "Is it?" in text                       # the hypothesis stands in for the question
