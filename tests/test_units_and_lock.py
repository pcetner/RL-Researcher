"""Reading a unit's state, and one run per output directory.

These are the files a monitor reads. The risk is not that they are missing but that they are
misread: a failed unit shown as running, a finished one shown as stale, a killed run's
directory silently taken over by a second process while the first is still writing.
"""

import json
import os
import time

import pytest

pytestmark = pytest.mark.tier1

from rl_researcher import atomic  # noqa: E402
from rl_researcher.checkpoint import clear_checkpoint, verify_identity, write_sidecar  # noqa: E402
from rl_researcher.lock import RunLocked, acquire_lock, lock_holder, release_lock  # noqa: E402
from rl_researcher.spec import SpecError  # noqa: E402
from rl_researcher.units import (age_of, mark_failed, parse_unit, read_unit, stamp_now, thin,  # noqa: E402
                                 unit_dir, unit_id, write_progress)


def _cell(tmp_path, unit="ols/seed0"):
    d = unit_dir(tmp_path, unit)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── unit ids and heartbeats ───────────────────────────────────────────────
def test_a_unit_id_round_trips():
    assert parse_unit(unit_id("planner_untrained", 2)) == ("planner_untrained", 2)


def test_a_malformed_unit_id_is_refused():
    with pytest.raises(ValueError, match="not a unit id"):
        parse_unit("ols-seed0")


def test_a_result_on_disk_beats_the_heartbeat(tmp_path):
    """The result is the proof: a unit that finished is done even if its last heartbeat said
    it was training, which is what a crash between the two writes looks like."""
    cell = _cell(tmp_path)
    write_progress(cell / "progress.json", status="running", step=5, updated="2020-01-01T00:00:00+00:00")
    atomic.write_json(cell / "results.json", {"steps": 300, "seconds": 12.0, "max_steps": 300})
    st = read_unit(cell, unit="ols/seed0", heartbeat_seconds=5)
    assert st.done and st.status == "done" and st.step == 300 and not st.stale


def test_a_quiet_live_unit_is_stale_and_a_quiet_finished_one_is_not(tmp_path):
    cell = _cell(tmp_path)
    write_progress(cell / "progress.json", status="running", step=5, updated="2020-01-01T00:00:00+00:00")
    assert read_unit(cell, unit="ols/seed0", heartbeat_seconds=5).stale
    write_progress(cell / "progress.json", status="stopped", step=5, updated="2020-01-01T00:00:00+00:00")
    assert not read_unit(cell, unit="ols/seed0", heartbeat_seconds=5).stale


def test_a_failure_keeps_where_it_died(tmp_path):
    """Where a unit died is most of the diagnosis, so the marker keeps the last progress."""
    cell = _cell(tmp_path)
    write_progress(cell / "progress.json", status="running", step=120, rate=9.1)
    mark_failed(cell / "progress.json", RuntimeError("CUDA out of memory"))
    st = read_unit(cell, unit="ols/seed0", heartbeat_seconds=5)
    assert st.failed and st.step == 120 and "CUDA out of memory" in (st.error or "")
    assert not st.stale  # a failure is reported as a failure, never as silence


def test_a_checkpoint_without_a_result_is_resumable(tmp_path):
    cell = _cell(tmp_path)
    write_sidecar(cell, step=150, elapsed_seconds=42.0)
    st = read_unit(cell, unit="ols/seed0", heartbeat_seconds=5)
    assert st.resumable and st.checkpoint_step == 150
    atomic.write_json(cell / "results.json", {"steps": 300, "seconds": 12.0})
    assert not read_unit(cell, unit="ols/seed0", heartbeat_seconds=5).resumable


def test_an_epoch_stamp_from_an_older_writer_is_still_readable(tmp_path):
    """The framework writes ISO; a file written by the pre-migration engine loop is epoch."""
    assert (age_of(time.time() - 30) or 0) == pytest.approx(30, abs=2)
    assert (age_of(stamp_now()) or 0) < 2
    assert age_of("not a time") is None


def test_the_heartbeat_history_stays_small(tmp_path):
    """A run with a small log interval would otherwise write a growing array every minute."""
    assert thin(list(range(10_000)), keep=120) == pytest.approx(
        [round(i * (9999 / 119)) for i in range(120)], abs=1)
    assert thin([1.0, 2.0]) == [1.0, 2.0]


# ── the lock ──────────────────────────────────────────────────────────────
def test_a_second_run_on_one_directory_is_refused(tmp_path):
    """Two runs sharing an output directory agree on the numbers and nothing says it happened
    twice, which is worse than a loud conflict."""
    acquire_lock(tmp_path, "toy", print)
    with pytest.raises(RunLocked, match="already running"):
        acquire_lock(tmp_path, "toy", print)


def test_a_dead_run_leaves_a_lock_that_is_taken_over(tmp_path):
    """The hot-start case: the laptop was shut down, so the pid is gone."""
    acquire_lock(tmp_path, "toy", print)
    path = tmp_path / ".study-lock.json"
    held = json.loads(path.read_text(encoding="utf-8"))
    held["pid"] = 999_999                       # a pid that is not running
    path.write_text(json.dumps(held), encoding="utf-8")
    said = []
    acquire_lock(tmp_path, "toy", said.append)
    assert any("stale lock" in s for s in said)
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_the_holder_says_whether_the_process_is_alive(tmp_path):
    acquire_lock(tmp_path, "toy", print)
    assert lock_holder(tmp_path)["alive"] is True
    path = tmp_path / ".study-lock.json"
    held = json.loads(path.read_text(encoding="utf-8"))
    held["host"] = "some-other-machine"
    path.write_text(json.dumps(held), encoding="utf-8")
    assert lock_holder(tmp_path)["alive"] is None   # not ours to ask about


def test_only_the_owner_releases_the_lock(tmp_path):
    path = acquire_lock(tmp_path, "toy", print)
    held = json.loads(path.read_text(encoding="utf-8"))
    held["pid"] = os.getpid() + 1
    path.write_text(json.dumps(held), encoding="utf-8")
    release_lock(path)
    assert path.is_file()


# ── checkpoint identity ───────────────────────────────────────────────────
def test_a_checkpoint_from_another_registration_is_refused(tmp_path):
    """Continuing it would report a run nobody registered."""
    with pytest.raises(SpecError, match="different spec or unit"):
        verify_identity({"spec_fingerprint": "old", "unit": "ols/seed0"},
                        fingerprint="new", unit="ols/seed0")
    with pytest.raises(SpecError, match="different spec or unit"):
        verify_identity({"spec_fingerprint": "same", "unit": "ols/seed1"},
                        fingerprint="same", unit="ols/seed0")
    verify_identity({"spec_fingerprint": "same", "unit": "ols/seed0"}, fingerprint="same", unit="ols/seed0")


def test_the_old_variant_and_seed_form_is_still_understood(tmp_path):
    """Auto-SM64's existing checkpoints name the unit as variant plus seed."""
    verify_identity({"spec_fingerprint": "s", "variant": "conv_ae", "seed": 2},
                    fingerprint="s", unit="conv_ae/seed2")


def test_clearing_removes_every_checkpoint_file(tmp_path):
    cell = _cell(tmp_path)
    for name in ("checkpoint.pt", "checkpoint.json", "checkpoint.tmp", "checkpoint.state.json"):
        (cell / name).write_text("x", encoding="utf-8")
    clear_checkpoint(cell)
    assert not list(cell.glob("checkpoint.*"))


def test_the_age_parser_reads_both_stamp_forms_and_refuses_the_ambiguous_one():
    """Two forms are readable, one is written, and the dispatch is on type.

    Runs from before the ISO rule carry a `time.time()` float and those files are still
    evidence, so both parse. But an epoch stamped as a JSON *string* is a parse failure, not a
    heartbeat: reading it as a number would report a live run as decades stale, and the reverse
    mistake would report a dead one as fresh.
    """
    import time
    from datetime import datetime, timedelta, timezone

    from rl_researcher.units import age_of

    now = datetime.now(timezone.utc)
    assert age_of(None) is None
    assert 0 <= (age_of(time.time() - 30) or -1) < 40                 # epoch float
    assert 0 <= (age_of(int(time.time()) - 30) or -1) < 40            # epoch int
    assert 25 < (age_of((now - timedelta(seconds=30)).isoformat()) or 0) < 40   # ISO with a zone
    naive = (now - timedelta(seconds=30)).replace(tzinfo=None).isoformat()
    assert 25 < (age_of(naive) or 0) < 40, "a stamp with no zone is read as UTC, not discarded"

    assert age_of("1788748565.16") is None, "an epoch in a string is unreadable, not ancient"
    assert age_of("not a date") is None
    assert age_of(True) is None, "bool is an int; `updated: true` is a confused writer"
    assert age_of({"updated": 1}) is None


def test_a_heartbeat_is_never_reported_as_being_from_the_future():
    import time

    from rl_researcher.units import age_of

    assert age_of(time.time() + 3600) == 0.0


def test_a_stamp_is_parsed_before_it_is_printed_or_compared():
    """`Finished 1788749820.0716286` is what a reader gets when stamps are compared as text.

    The twelve committed Phase 4 heartbeats carry `time.time()` floats, from before the ISO
    rule. Sorting those as strings also puts "9..." above "10...", so the newest unit is not
    reliably the one reported.
    """
    from datetime import datetime, timezone

    from rl_researcher.units import stamp_of

    iso = stamp_of("2026-09-06T12:00:00+00:00")
    assert iso == datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    assert stamp_of("2026-09-06T12:00:00") == iso, "a naive stamp is read as UTC"
    assert stamp_of(iso.timestamp()) == iso, "an epoch float names the same instant"
    for junk in (None, True, "not a date", [], {}):
        assert stamp_of(junk) is None
    # ...and it orders by instant, not by digit.
    assert max(stamp_of(9_000_000_000.0), stamp_of(10_000_000_000.0)) == stamp_of(10_000_000_000.0)
