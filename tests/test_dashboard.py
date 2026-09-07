"""The live page's data layer: what it reads, and what it must not misread.

It reads only files the runner already writes, so the risk is that it misreads them: a unit
shown as running when it failed, a stale heartbeat shown as progress, a completion lost to a
burst of step lines. Those are what these check.

The assertions come from `Auto-SM64/python/tests/study/test_dashboard.py`, re-pointed at the
toy kind. They were written against real misreadings and are kept in those terms.
"""

import json
import re
import time

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from rl_researcher.artefacts.dashboard import (  # noqa: E402
    DEFAULT_SECTIONS, DEFAULT_VOCAB, _rung, arm_table, bubble, chip, collect, curves_of,
    failed_panel, finished_table, floors_of, format_log, headline, history_of, ladder,
    last_of, metric_rows, metrics_of, notes,
    page_foot, page_tiles, periodic_writer, pick_log, queued_panel, render, running_table,
    section, vocab_of, write_dashboard, write_page)
from rl_researcher.kinds import LogVocab  # noqa: E402
from rl_researcher.units import unit_dir  # noqa: E402


def _cell(out, arm, seed, **files):
    d = unit_dir(out, f"{arm}/seed{seed}")
    d.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (d / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def _done(**metrics):
    return {"arm": "ols", "seed": 0, "status": "complete", "steps": 200, "max_steps": 200,
            "seconds": 4.0, "metrics": metrics or {"slope_error": 0.02},
            "history": {"loss": [1.0, 0.5, 0.2]}}


def _beat(**over):
    payload = {"status": "running", "step": 90, "max_steps": 200, "rate": 30.0,
               "eta_seconds": 3.6, "elapsed_seconds": 3.0,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}
    payload.update(over)
    return payload


def test_it_counts_progress_over_every_unit(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    _cell(out, "ols", 1, progress=_beat())
    data = collect(spec, kind, out)
    assert len(data.units) == 6                      # every registered unit, started or not
    assert data.total_steps == 6 * 200
    assert data.done_steps == 200 + 90
    assert data.eta_all and data.eta_all > 0         # projected from what has finished
    assert sum(1 for u in data.units if u.status == "not started") == 4


def test_a_failed_unit_is_not_shown_as_running(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat(status="failed", error="RuntimeError: CUDA out of memory"))
    data = collect(spec, kind, out)
    u = next(u for u in data.units if u.arm == "ols" and u.seed == 0)
    assert u.failed and chip(u) == ("failed", "crit")
    assert data.status.state == "FAILED"
    assert "CUDA out of memory" in (u.error or "")


def test_a_stale_heartbeat_is_called_out(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat(updated="2020-01-01T00:00:00+00:00"))
    data = collect(spec, kind, out)
    u = next(u for u in data.units if u.arm == "ols" and u.seed == 0)
    assert u.stale and chip(u) == ("stale", "crit")
    assert data.status.state == "STALE"


def test_a_resumable_unit_says_where_to_resume_from(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat(step=500), checkpoint={"step": 500})
    data = collect(spec, kind, out)
    u = next(u for u in data.units if u.arm == "ols" and u.seed == 0)
    assert u.resumable and u.checkpoint_step == 500


def test_a_result_beats_the_heartbeat_because_the_result_is_the_proof(toy):
    """A unit that finished and then had a stale heartbeat left beside it is finished."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done(), progress=_beat(updated="2020-01-01T00:00:00+00:00"))
    u = next(u for u in collect(spec, kind, out).units if u.seed == 0 and u.arm == "ols")
    assert u.done and not u.stale and chip(u) == ("done", "ok")


def test_an_incomplete_unit_is_not_reported_as_done(toy):
    """It produced numbers, but about a smaller budget than the one registered, and the page
    has to say which."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results={**_done(), "status": "incomplete", "steps": 60})
    u = next(u for u in collect(spec, kind, out).units if u.seed == 0 and u.arm == "ols")
    assert chip(u) == ("incomplete", "ok")


def test_a_running_unit_carries_its_curves_and_a_finished_one_keeps_them(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    _cell(out, "ols", 1, progress=_beat(history={"loss": [1.0, 0.6]}, last={"loss": 0.6}))
    units = {(u.arm, u.seed): u for u in collect(spec, kind, out).units}
    assert history_of(units[("ols", 0)])["loss"] == [1.0, 0.5, 0.2]   # off the result
    assert history_of(units[("ols", 1)])["loss"] == [1.0, 0.6]        # off the heartbeat
    assert last_of(units[("ols", 1)], ["loss"]) == {"loss": 0.6}
    assert metrics_of(units[("ols", 0)])["slope_error"] == 0.02
    assert metrics_of(units[("ols", 1)]) == {}


def test_a_heartbeat_that_reports_at_the_top_level_is_still_read(toy):
    """Every heartbeat written before `UnitContext.beat` existed put its numbers there."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat(coverage_cells=17))
    u = next(u for u in collect(spec, kind, out).units if u.seed == 0 and u.arm == "ols")
    assert last_of(u, ["coverage_cells"]) == {"coverage_cells": 17}


def test_the_page_and_the_status_command_read_the_same_files(toy):
    """Two collectors is how a page says running while `status` exits 2. There is one."""
    from rl_researcher.status import run_status

    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat(updated="2020-01-01T00:00:00+00:00"))
    mine = collect(spec, kind, out).status.to_dict()
    theirs = run_status(spec, kind, out).to_dict()

    def _ageless(d):
        # `age` is seconds since the heartbeat, measured from now, so two calls a microsecond
        # apart differ in it by design. Everything the two disagree about is the point.
        return {**d, "units": [{k: v for k, v in u.items() if k != "age"} for u in d["units"]]}

    assert _ageless(mine) == _ageless(theirs)
    assert mine["state"] == theirs["state"] == "STALE"


def test_the_projection_is_a_ratio_of_sums_not_a_mean_of_rates(toy):
    """One very short unit would otherwise pull the whole estimate down with it."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results={**_done(), "steps": 200, "seconds": 200.0})
    _cell(out, "ols", 1, results={**_done(), "steps": 2, "seconds": 0.2})
    data = collect(spec, kind, out)
    # 200.2s over 202 steps, times the 800 steps left of the six-unit budget.
    assert data.eta_all == pytest.approx(200.2 / 202 * (1200 - 202), rel=1e-6)


# ── the log tail ──────────────────────────────────────────────────────────────────────────

def test_the_log_never_loses_a_completion_to_a_burst_of_progress():
    lines = [f"12:00:{i:02d} [ols seed 0] step {i}/200 loss 0.5" for i in range(30)]
    lines.insert(3, "12:00:03 [ols seed 0] complete after 200 steps")
    lines.insert(4, "12:00:04 FAILED: RuntimeError: boom")
    kept = pick_log(lines, DEFAULT_VOCAB, keep=6)
    assert any("complete after" in ln for ln in kept)
    assert any("FAILED" in ln for ln in kept)
    assert len(kept) == 6
    assert kept == [ln for ln in lines if ln in kept]     # still in the order they happened


def test_the_log_is_coloured_by_what_each_line_says():
    out = format_log(["12:00:00 FAILED: RuntimeError: boom",
                      "12:00:01 complete after 200 steps",
                      "12:00:02 hot-stopped at step 60",
                      "12:00:03 nothing notable here"], DEFAULT_VOCAB)
    assert 'class="ln crit"' in out and 'class="ln ok"' in out and 'class="ln warn"' in out
    assert 'class="ln "' in out                       # the unremarkable line gets no tone
    assert 'class="ts">12:00:00<' in out              # and every timestamp is dimmed
    assert format_log([], DEFAULT_VOCAB) == '<div class="ln of">no log yet</div>'


def test_a_step_line_is_broken_into_columns_and_coloured_by_its_unit():
    out = format_log(["12:00:00 [ols seed 0] step 1,200/10,000 loss 0.42"], DEFAULT_VOCAB,
                     order=["ols", "noisy"])
    assert 'class="ln step"' in out
    assert 'class="stepn">1,200<' in out and 'class="of">/10,000<' in out
    assert "loss 0.42" in out and "color:#" in out


def test_a_kind_that_names_its_own_step_line_gets_it_parsed(toy):
    """A closed-loop run logs decisions, not steps, and its log is unreadable if the page can
    only align one of the two."""
    vocab = LogVocab(marks={"FAILED": "crit"},
                     step_line=r"^(?P<ts>\d\d:\d\d:\d\d)\s+\[(?P<unit>[^\]]+)\]\s+decision\s+"
                               r"(?P<step>[\d,]+)/(?P<max>[\d,]+)\s+(?P<rest>.*?)\s*$")
    out = format_log(["12:00:00 [planner seed 0] decision 40/1,000 coverage 3 cells"], vocab)
    assert 'class="ln step"' in out and 'class="stepn">40<' in out and "coverage 3 cells" in out


def test_a_kind_with_no_vocabulary_still_gets_the_frameworks_own_words(toy):
    config, kind, spec, out = toy

    class Mute:
        pass

    assert vocab_of(Mute()) is DEFAULT_VOCAB
    assert vocab_of(kind).marks                        # the toy kind declares one
    # ...and a kind whose log_vocab raises does not take the page down with it.
    class Angry:
        def log_vocab(self):
            raise RuntimeError("nope")

    assert vocab_of(Angry()) is DEFAULT_VOCAB


# ── writing it ────────────────────────────────────────────────────────────────────────────

def test_the_writer_throttles_to_its_interval_and_force_bypasses_it(tmp_path):
    now = {"t": 1000.0}
    calls = []

    def render(*, refresh=True):
        calls.append(refresh)
        return "<html></html>"

    write = periodic_writer(render, tmp_path / "p.html", interval=15,
                            clock=lambda: now["t"])
    write()
    assert len(calls) == 1                       # the first is always written
    write()
    assert len(calls) == 1                       # ...and the second is inside the interval
    write(force=True)
    assert len(calls) == 2                       # force goes through anyway
    now["t"] += 20
    write()
    assert len(calls) == 3
    assert (tmp_path / "p.html").read_text(encoding="utf-8") == "<html></html>"


def test_a_broken_dashboard_never_takes_the_run_down(tmp_path):
    """One logged line, then silence: a page that raised every fifteen seconds for eight hours
    would bury the run's own log, and one that raised into the run would end it."""
    said = []

    def render(*, refresh=True):
        raise RuntimeError("render is broken")

    write = periodic_writer(render, tmp_path / "p.html", log=said.append)
    write(force=True)
    write(force=True)
    write(force=True)
    assert len(said) == 1 and "render is broken" in said[0]
    assert "the run itself is unaffected" in said[0]
    assert not (tmp_path / "p.html").exists()


def test_a_failed_render_leaves_the_page_that_was_there(tmp_path):
    """A browser refreshing every fifteen seconds must never be handed half a page."""
    target = tmp_path / "p.html"
    write_page(lambda *, refresh=True: "<html>first</html>", target)
    with pytest.raises(RuntimeError):
        write_page(lambda *, refresh=True: (_ for _ in ()).throw(RuntimeError("boom")), target)
    assert target.read_text(encoding="utf-8") == "<html>first</html>"
    assert not list(tmp_path.glob("*.tmp*"))


def test_the_index_is_rewritten_on_the_same_tick(tmp_path):
    """Other runs on the index may still be going, so it cannot wait for this one to finish."""
    hits = []
    write = periodic_writer(lambda *, refresh=True: "<html></html>", tmp_path / "p.html",
                            also=lambda: hits.append(1))
    write(force=True)
    write(force=True)
    assert len(hits) == 2


# ── the panels ────────────────────────────────────────────────────────────────────────────

def _agg_of(spec, kind, out):
    from rl_researcher.artefacts.report import aggregate

    data = collect(spec, kind, out)
    return data, aggregate([u.result or {} for u in data.with_metrics()],
                           [m.name for m in spec.metrics])


def test_the_scorecard_exists_before_anything_finishes(toy):
    """A panel that does not exist yet cannot tell a reader what is being measured."""
    config, kind, spec, out = toy
    data = collect(spec, kind, out)
    rows = metric_rows(spec, kind, data.with_metrics(), data.order)
    for m in spec.metrics:
        assert kind.registry.title(m.name) in rows
    assert 'class="mrow"' in rows and rows.count('class="mrow"') == len(spec.metrics)


def test_a_metric_carries_its_definition_and_the_specs_own_reason(toy):
    """Both are already written -- one in the kind's registry, one in the spec -- and neither
    is invented for the page."""
    config, kind, spec, out = toy
    m = spec.metrics[0]
    got = bubble(kind, m)
    assert kind.registry.description(m.name)[:40] in got
    if m.why:
        assert m.why[:40] in got
    assert kind.registry.title(m.name) in got


def test_a_kind_with_no_registry_still_renders_a_panel(toy):
    """A page is not the place to discover that a kind declared nothing."""
    config, kind, spec, out = toy

    class Bare:
        pass

    got = bubble(Bare(), spec.metrics[0])
    assert spec.metrics[0].name in got            # falls back to the metric's own name
    data = collect(spec, kind, out)
    assert metric_rows(spec, Bare(), data.with_metrics(), data.order)


def test_a_diverged_value_is_counted_rather_than_averaged_in(toy):
    """Averaging it in gave 9.2e19, which is not the metric's central value in any sense a
    reader could use."""
    config, kind, spec, out = toy
    name = spec.metrics[0].name
    _cell(out, "ols", 0, results={**_done(**{name: 0.5})})
    _cell(out, "ols", 1, results={**_done(**{name: float("inf")})})
    data = collect(spec, kind, out)
    rows = metric_rows(spec, kind, data.with_metrics(), data.order)
    assert "1 diverged" in rows
    assert "9.2e" not in rows and "inf" not in rows.replace("infinity", "")


def test_the_arm_table_crowns_a_best_only_when_there_is_something_to_compare(toy):
    """Early in a run one arm has finished units and every column crowned it. Best-of-one is
    not a comparison, and it reads like a result."""
    config, kind, spec, out = toy
    bars = [m for m in spec.metrics if m.bar is not None]
    assert len(bars) > 1, "this needs more than one judged column to be worth running"
    _cell(out, "ols", 0, results=_done(slope_error=0.01, r2=0.99))
    data, agg = _agg_of(spec, kind, out)
    assert "crown" not in arm_table(spec, kind, agg, data.order)

    _cell(out, "noisy", 0, results={**_done(slope_error=0.9, r2=0.10), "arm": "noisy"})
    data, agg = _agg_of(spec, kind, out)
    table = arm_table(spec, kind, agg, data.order)
    assert table.count("crown") == len(bars), table
    # ...and the crown went to the arm that actually won, in each direction.
    ols = next(r for r in table.split("<tr>") if ">ols<" in r)
    assert ols.count("crown") == len(bars), "the better arm did not take both columns"


def test_an_arm_with_a_diverged_seed_never_wins_a_column(toy):
    """Its mean is over the seeds that survived, which is not the quantity the others report."""
    config, kind, spec, out = toy
    name = spec.metrics[0].name
    _cell(out, "ols", 0, results=_done(**{name: 0.001}))
    _cell(out, "ols", 1, results=_done(**{name: float("inf")}))
    _cell(out, "noisy", 0, results={**_done(**{name: 0.5}), "arm": "noisy"})
    _cell(out, "noisy", 1, results={**_done(**{name: 0.6}), "arm": "noisy"})
    data, agg = _agg_of(spec, kind, out)
    table = arm_table(spec, kind, agg, data.order)
    assert "diverged" in table
    rows = table.split("<tr>")
    ols = next(r for r in rows if ">ols<" in r)
    assert "crown" not in ols, "an arm with a diverged seed was crowned"


def test_the_reference_arm_of_a_comparison_is_not_marked_against_itself(toy):
    """A cross printed against a number that was never on trial."""
    from rl_researcher.artefacts.report import aggregate

    config, kind, spec, out = toy
    m = spec.metrics[0]
    object.__setattr__(m, "bar", None) if False else None
    m.compare_to = "noisy"
    m.bar = 0.5
    agg = aggregate([{"arm": a, "metrics": {m.name: 0.4}} for a in ("ols", "noisy")],
                    [m.name for m in spec.metrics])
    table = arm_table(spec, kind, agg, ["ols", "noisy"])
    noisy = next(r for r in table.split("<tr>") if ">noisy<" in r)
    assert "✗" not in noisy and "✓" not in noisy


def test_a_failed_unit_gets_its_own_panel_and_leaves_in_progress(toy):
    """One sat in the in-progress table reading 0.0 steps/s with the reason off past the
    horizontal scroll."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat(status="failed", error="RuntimeError: CUDA out of memory"))
    _cell(out, "ols", 1, progress=_beat())
    data = collect(spec, kind, out)
    fails = failed_panel(data)
    assert "CUDA out of memory" in fails and "1 unit" in fails
    running = running_table(spec, kind, data)
    assert "CUDA out of memory" not in running
    assert ">ols<" in running                                  # the live one is still there
    assert failed_panel(collect(spec, kind, out.parent / "empty")) == ""


def test_queued_units_get_their_own_box(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)
    panel = queued_panel(data)
    assert "queued · 5" in panel and panel.count("qchip") == 5


def test_finished_units_are_ordered_best_first(toy):
    config, kind, spec, out = toy
    m = spec.metrics[0]
    _cell(out, "ols", 0, results=_done(**{m.name: 0.9}))
    _cell(out, "ols", 1, results=_done(**{m.name: 0.01}))
    data = collect(spec, kind, out)
    table = finished_table(spec, kind, data)
    assert "bestrow" in table
    rows = [r for r in table.split("<tr") if 'class="cell"' in r]   # [1] is the header row
    better = "0.01" if m.direction == "lower" else "0.9"
    assert better in rows[0], rows[0][:400]


def test_the_curve_columns_are_the_ones_the_kind_declares(toy):
    """The study page had three metric names hardcoded here. Which series a page draws, and
    which bar is a curve's floor, is the kind's to say."""
    config, kind, spec, out = toy
    declared = kind.curves(spec)
    assert declared, "the toy kind declares no curves, so this test proves nothing"
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)
    table = finished_table(spec, kind, data)
    for c in declared:
        assert c.title in table

    floors = floors_of(spec, kind)
    bars = {m.name: m.bar for m in spec.metrics}
    for c in declared:
        assert floors[c.key] == (bars.get(c.floor_metric) if c.floor_metric else None)


def test_a_kind_whose_curves_raise_still_renders(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)

    class Angry:
        registry = kind.registry

        def curves(self, spec):
            raise RuntimeError("nope")

    assert curves_of(spec, Angry()) == []
    assert finished_table(spec, Angry(), data)          # a table, just without curve columns


def test_the_headline_reports_the_floor_alongside_the_win(toy):
    """A unit over the primary bar but under a collapse floor is not a winner, and the header
    must not read like one."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)
    head = headline(spec, kind, data)
    assert "Current best" in head and "ols" in head
    assert 'class="stat' in head


def test_no_headline_before_anything_finishes(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat())
    assert headline(spec, kind, collect(spec, kind, out)) == ""


def test_a_diverged_unit_is_never_crowned_the_headline(toy):
    """On a higher-is-better primary, max() would have made it the headline of the whole run."""
    config, kind, spec, out = toy
    m = spec.metrics[0]
    _cell(out, "ols", 0, results=_done(**{m.name: float("inf")}))
    assert headline(spec, kind, collect(spec, kind, out)) == ""
    _cell(out, "ols", 1, results=_done(**{m.name: 0.02}))
    assert "seed 1" in headline(spec, kind, collect(spec, kind, out))


def test_a_resumable_or_failed_unit_says_so_in_its_notes(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, progress=_beat(step=500), checkpoint={"step": 500})
    u = next(u for u in collect(spec, kind, out).units if u.arm == "ols" and u.seed == 0)
    assert "resumable from 500" in notes(u)
    assert "updated" in notes(u)


def test_nothing_a_panel_writes_reaches_the_network(toy):
    """A page that fetches is a page that does not open on a machine with no network, and this
    one is meant to open from a file and from a share."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    _cell(out, "ols", 1, progress=_beat())
    data = collect(spec, kind, out)
    _d, agg = _agg_of(spec, kind, out)
    whole = "".join([
        headline(spec, kind, data),
        metric_rows(spec, kind, data.with_metrics(), data.order),
        arm_table(spec, kind, agg, data.order),
        running_table(spec, kind, data),
        finished_table(spec, kind, data),
        failed_panel(data), queued_panel(data),
        format_log(data.log_tail, vocab_of(kind), data.order),
    ])
    for bad in ("http://", "https://", "//fonts.", "<script src", "@import"):
        assert bad not in whole, bad


# ── the page ──────────────────────────────────────────────────────────────────────────────

def test_the_page_is_the_sections_the_kind_names_in_the_order_it_names_them(toy):
    """The two pages this replaces were each one f-string, so a kind that wanted a panel of
    its own had to be given a second page. That is how there came to be two of everything."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)

    class Picky:
        name = "picky"
        registry = kind.registry
        page_sections = ("log", "metrics")

        def curves(self, spec):
            return kind.curves(spec)

    page = render(spec, Picky(), data)
    assert page.index("<h2>log") < page.index("registered metrics")
    assert "<h2>queued" not in page                   # a section not named is not drawn

    normal = render(spec, kind, data)
    assert normal.index("registered metrics") < normal.index("<h2>log")


def test_a_kind_contributes_a_panel_of_its_own_without_a_second_page(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)

    class Extra:
        name = "extra"
        registry = kind.registry
        page_sections = ("ladder", "log")
        page_extras = {"ladder": lambda spec, kind, data:
                       f'<div class="panel">rungs: {len(data.units)}</div>'}

        def curves(self, spec):
            return []

    page = render(spec, Extra(), data)
    assert "rungs: 6" in page
    assert page.index("rungs:") < page.index("<h2>log")


def test_a_section_name_nothing_defines_renders_as_nothing(toy):
    """A page is the artefact a person opens *because* something has gone wrong. It must not
    be the second thing to break."""
    config, kind, spec, out = toy
    data = collect(spec, kind, out)
    assert section("no-such-panel", spec, kind, data) == ""

    class Typo:
        name = "typo"
        registry = kind.registry
        page_sections = ("mterics", "log")

        def curves(self, spec):
            return []

    assert "<h2>log" in render(spec, Typo(), data)


def test_the_tiles_count_in_the_kinds_own_nouns(toy):
    """One page said cells and steps, the other arms and decisions, and that was the whole of
    the difference between them at the top of the page."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)
    assert "units done" in page_tiles(spec, kind, data)

    class Loopish:
        name = "loop"
        registry = kind.registry
        unit_noun = "arm"
        step_noun = "decision"

    got = page_tiles(spec, Loopish(), data)
    assert "arms done" in got and "decisions" in got and "steps" not in got


def test_the_foot_says_what_the_kind_wants_it_to_and_points_at_status(toy):
    config, kind, spec, out = toy
    data = collect(spec, kind, out)

    class Pinned:
        name = "pinned"
        registry = kind.registry

        def page_note(self, spec, data):
            return "snapshot <code>abc123def456</code>"

    foot = page_foot(spec, Pinned(), data, refresh=True)
    assert "abc123def456" in foot
    assert "<code>status</code>" in foot               # the page is not the authority on liveness
    assert "refreshing every" in foot
    assert "rendered once" in page_foot(spec, kind, data, refresh=False)


def test_a_page_rendered_after_the_run_does_not_reload_itself(toy):
    """A finished page that keeps reloading is a page that looks alive."""
    config, kind, spec, out = toy
    data = collect(spec, kind, out)
    assert "http-equiv=\"refresh\"" in render(spec, kind, data, refresh=True)
    assert "http-equiv=\"refresh\"" not in render(spec, kind, data, refresh=False)


def test_the_whole_page_fetches_nothing(toy):
    """It has to open from a file and from a share, on a machine with no network -- which is
    where a run tends to be."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    _cell(out, "ols", 1, progress=_beat(status="failed", error="boom"))
    _cell(out, "noisy", 0, progress=_beat())
    page = render(spec, kind, collect(spec, kind, out))
    for bad in ("http://", "https://", "//fonts.", "<script src", "@import"):
        assert bad not in page, bad
    # `url(#...)` is a reference to a pattern defined in this same document; a `url(` with a
    # scheme after it would not be.
    assert not re.search(r"url\(\s*['\"]?[a-z]+:", page)


def test_the_theme_script_is_opened_exactly_once(toy):
    """It used to carry its own tags and the callers wrapped it anyway, so the browser read
    the literal text `<script>` as the first token of the program."""
    config, kind, spec, out = toy
    page = render(spec, kind, collect(spec, kind, out))
    assert page.count("<script>") == 1 and page.count("</script>") == 1
    assert page.startswith("<!doctype html>")


def test_write_dashboard_lands_beside_the_run_and_is_written_whole(toy):
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    target = write_dashboard(spec, kind, out)
    assert target == out / "dashboard.html"
    assert target.read_text(encoding="utf-8").rstrip().endswith("</html>")
    assert not list(out.glob("*.tmp*"))                # nothing half-written left behind


def test_every_panel_renders_before_a_single_unit_has_started(toy):
    """The emptiest possible run is the one a person is most likely to open the page on."""
    config, kind, spec, out = toy
    page = render(spec, kind, collect(spec, kind, out))
    assert "registered metrics" in page and "queued" in page
    assert "not started" in page


# ── the ladder ────────────────────────────────────────────────────────────────────────────

class _Ladder:
    """A kind that draws the ladder instead of the four unit tables."""

    name = "ladder"
    page_sections = ("metrics", "ladder", "log")

    def __init__(self, kind):
        self.registry = kind.registry
        self._kind = kind

    def curves(self, spec):
        return self._kind.curves(spec)


def test_the_ladder_draws_every_registered_unit_including_the_unstarted(toy):
    """The shape of a run is the shape of the argument it makes. A reader has to be able to
    see that a whole arm is missing, which withholds a comparison, rather than one seed of
    each, which does not."""
    config, kind, spec, out = toy
    _cell(out, "ols", 0, results=_done())
    data = collect(spec, kind, out)
    grid = ladder(spec, kind, data)
    assert grid.count('class="lcell') == len(data.units) == 6
    assert grid.count('class="lrow"') == 1 + len(data.order)     # a header row, then the arms
    for seed in spec.seeds:
        assert f"seed {seed}" in grid
    assert "2 arms × 3 seeds" in grid


def test_every_state_a_rung_can_be_in_says_something_different(toy):
    config, kind, spec, out = toy
    m = spec.metrics[0].name
    _cell(out, "ols", 0, results=_done(**{m: 0.02}))
    _cell(out, "ols", 1, progress=_beat(status="failed", error="RuntimeError: CUDA OOM\nline 2"))
    _cell(out, "ols", 2, progress=_beat(step=90, rate=30.0, eta_seconds=3.6))
    _cell(out, "noisy", 0, progress=_beat(updated="2020-01-01T00:00:00+00:00"))
    data = collect(spec, kind, out)
    rungs = {(u.arm, u.seed): _rung(spec, kind, u) for u in data.units}
    assert "0.02" in rungs[("ols", 0)]
    assert rungs[("ols", 1)] == "RuntimeError: CUDA OOM"        # the reason, first line only
    assert "90/200" in rungs[("ols", 2)] and "30.0/s" in rungs[("ols", 2)]
    assert "eta" in rungs[("ols", 2)]
    assert "quiet" in rungs[("noisy", 0)]                       # not an eta: it stopped talking
    assert "200 steps" in rungs[("noisy", 2)]                   # never started


def test_the_ladder_is_not_on_a_page_that_did_not_ask_for_it(toy):
    """It says what the four unit tables say, more compactly and with less room each. A kind
    picks one or the other, and the default is the tables."""
    config, kind, spec, out = toy
    assert "ladder" not in DEFAULT_SECTIONS
    data = collect(spec, kind, out)
    assert "<h2>ladder" not in render(spec, kind, data)
    page = render(spec, _Ladder(kind), data)
    assert "<h2>ladder" in page and "<h2>queued" not in page


def test_the_ladder_uses_the_kinds_own_word_for_a_step(toy):
    config, kind, spec, out = toy

    class Decisions(_Ladder):
        step_noun = "decision"

    data = collect(spec, kind, out)
    assert "200 decisions" in ladder(spec, Decisions(kind), data)
