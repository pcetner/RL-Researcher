"""The index over every run in a project, whatever kind each one is.

There used to be one index per kind, because the survey called one kind's spec loader by name.
That is the same fault that produced two dashboards: the thing that varies between kinds was
reached for directly instead of being asked for. These check that the index dispatches, that
its links resolve across output roots, and that a spec which stopped parsing appears on the
page rather than silently vanishing from it.
"""

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

import json  # noqa: E402
import time  # noqa: E402

from rl_researcher.artefacts.dashboard import (  # noqa: E402
    STATE_RANK, index_row, render_index, survey, write_index)
from rl_researcher.config import load_config  # noqa: E402
from rl_researcher.examples.toy.kind import ToyKind, write_example_spec  # noqa: E402
from rl_researcher.units import unit_dir  # noqa: E402


class OtherKind(ToyKind):
    """A second kind, so the index has two to tell apart. It is the toy kind in every respect
    but its name and its output root, which is the whole of what the index dispatches on."""

    name = "other"


def _second_kind(root):
    """Register `other` in the project's config and give it a spec of its own."""
    cfg = root / "rl-researcher.toml"
    text = cfg.read_text(encoding="utf-8")
    text = text.replace('toy = "rl_researcher.examples.toy.kind:ToyKind"',
                        'toy = "rl_researcher.examples.toy.kind:ToyKind"\n'
                        'other = "tests.test_index:OtherKind"')
    text = text.replace('toy = "docs/toy"', 'toy = "docs/toy"\nother = "elsewhere/other"')
    cfg.write_text(text, encoding="utf-8")
    spec = write_example_spec(root / "studies" / "other-run.toml")
    body = spec.read_text(encoding="utf-8")
    spec.write_text(body.replace('name = "toy-line-fit"', 'name = "other-run"')
                    .replace('kind = "toy"', 'kind = "other"'), encoding="utf-8")
    return spec


def _finish(out, arm="ols", seed=0, **metrics):
    d = unit_dir(out, f"{arm}/seed{seed}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(json.dumps(
        {"arm": arm, "seed": seed, "status": "complete", "steps": 200, "max_steps": 200,
         "seconds": 4.0, "metrics": metrics or {"slope_error": 0.02, "r2": 0.99}}),
        encoding="utf-8")
    return d


def test_one_index_lists_every_kind(project):
    """The reason there were two indexes is the reason there were two dashboards."""
    _second_kind(project)
    rows = survey(load_config())
    assert {r.name for r in rows} == {"toy-line-fit", "other-run"}
    assert {r.kind for r in rows} == {"toy", "other"}


def test_a_link_resolves_from_wherever_the_index_is_written(project):
    """The kinds have different output roots, so a link built by joining the run's name to the
    index's own directory is dead for every kind but one -- and a dead link looks like a live
    one until it is clicked."""
    _second_kind(project)
    config = load_config()
    _finish(config.out_root("other") / "other-run")
    _finish(config.out_root("toy") / "toy-line-fit")
    base = config.path("state")
    rows = survey(config)
    for row in rows:
        href = index_row(row, base).split('href="')[1].split('"')[0]
        assert (base / href).resolve() == (row.out / "dashboard.html").resolve()
        assert "\\" not in href                 # a Windows separator is not a URL


def test_a_run_with_nothing_on_disk_is_listed_without_a_link(project):
    config = load_config()
    row = next(r for r in survey(config) if r.name == "toy-line-fit")
    assert row.data is None and row.state == "not started"
    assert "<a href" not in index_row(row, config.path("state"))


def test_a_spec_that_stopped_parsing_is_named_on_the_page(project):
    """Skipping it hides the only symptom, on the page a person would look at to find it."""
    (project / "studies" / "broken.toml").write_text("name = \nkind =", encoding="utf-8")
    rows = survey(load_config())
    bad = next(r for r in rows if r.name == "broken")
    assert bad.state == "unreadable" and bad.tone == "crit"
    assert bad.error and bad.spec is None
    assert "broken" in render_index(rows, project, title="p")
    assert len(rows) == 2                       # the readable one is still there


def test_worst_news_first(project):
    """A finished run is the least urgent thing on the page; a stale one is a failure that has
    not admitted it yet."""
    _second_kind(project)
    config = load_config()
    _finish(config.out_root("toy") / "toy-line-fit")
    stale = unit_dir(config.out_root("other") / "other-run", "ols/seed0")
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "progress.json").write_text(json.dumps(
        {"status": "running", "step": 10, "max_steps": 200,
         "updated": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")
    rows = survey(config)
    assert [r.name for r in rows] == ["other-run", "toy-line-fit"]
    assert STATE_RANK[rows[0].state] < STATE_RANK[rows[1].state]


def test_the_index_is_written_whole_and_fetches_nothing(project):
    _second_kind(project)
    config = load_config()
    _finish(config.out_root("toy") / "toy-line-fit")
    target = write_index(config)
    text = target.read_text(encoding="utf-8")
    assert target == config.path("state") / "index.html"
    assert text.rstrip().endswith("</html>") and text.startswith("<!doctype html>")
    assert config.name in text
    for bad in ("http://", "https://", "//fonts.", "<script src", "@import"):
        assert bad not in text, bad


def test_a_finished_index_does_not_reload_itself(project):
    config = load_config()
    rows = survey(config)
    assert 'http-equiv="refresh"' in render_index(rows, project, refresh=True)
    assert 'http-equiv="refresh"' not in render_index(rows, project, refresh=False)


def test_a_run_in_progress_shows_how_far_in_it_is(project):
    config = load_config()
    d = unit_dir(config.out_root("toy") / "toy-line-fit", "ols/seed0")
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.json").write_text(json.dumps(
        {"status": "running", "step": 100, "max_steps": 200, "elapsed_seconds": 3.0,
         "updated": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}), encoding="utf-8")
    row = next(r for r in survey(config) if r.name == "toy-line-fit")
    assert row.state == "running"
    cell = index_row(row, config.path("state"))
    assert "0/6" in cell                        # one unit started, none of the six done
    assert "width:8.3%" in cell                 # 100 of 1,200 steps


def test_the_heartbeat_writes_the_page_and_the_index_together(project):
    """`rl_researcher.run` imports this factory by name and silently falls back to no page if
    it is missing, so the seam has to be checked from the run's side, not the page's."""
    import inspect

    from rl_researcher.artefacts.dashboard import page_writer_factory
    from rl_researcher import run as run_module

    # `run.main` imports it by name inside a try/except ImportError, so a rename here does not
    # fail a test -- it silently costs every run its page.
    assert "page_writer_factory" in inspect.getsource(run_module.main)

    config = load_config()
    kind = OtherKind()
    _second_kind(project)
    spec = kind.load(project / "studies" / "other-run.toml")
    out = config.out_root("other") / "other-run"
    _finish(out)
    write = page_writer_factory(spec, kind, out, print)
    write(force=True)
    assert (out / "dashboard.html").is_file()
    assert (config.path("state") / "index.html").is_file()


def test_a_run_with_no_config_above_it_still_gets_its_own_page(tmp_path, project):
    """A run driven straight from the library, in a temp directory: no index to write, and
    that must not cost it the page."""
    from rl_researcher.artefacts.dashboard import page_writer_factory

    config = load_config()
    kind = ToyKind()
    spec = kind.load(project / "studies" / "toy-line-fit.toml")
    loose = tmp_path / "nowhere" / "toy-line-fit"
    loose.mkdir(parents=True)
    _finish(loose)
    page_writer_factory(spec, kind, loose, print)(force=True)
    assert (loose / "dashboard.html").is_file()
    assert not (config.path("state") / "index.html").exists()
