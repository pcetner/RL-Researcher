"""The live page's data layer: what it reads, and what it must not misread.

It reads only files the runner already writes, so the risk is that it misreads them: a unit
shown as running when it failed, a stale heartbeat shown as progress, a completion lost to a
burst of step lines. Those are what these check.

The assertions come from `Auto-SM64/python/tests/study/test_dashboard.py`, re-pointed at the
toy kind. They were written against real misreadings and are kept in those terms.
"""

import json
import time

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from rl_researcher.artefacts.dashboard import (  # noqa: E402
    DEFAULT_VOCAB, chip, collect, format_log, history_of, last_of, metrics_of, periodic_writer,
    pick_log, vocab_of, write_page)
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
