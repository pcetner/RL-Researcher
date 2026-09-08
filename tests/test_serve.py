"""The dashboard: what it may write, what it refuses, and who is allowed to ask.

Driven through :func:`serve.api` with no socket in the way, the same way every other command in
this package is tested -- call it, read what came back, then look at the files. That is not only
convenience: it is the shape the module is written in, because a server whose behaviour can only
be reached through a port is a server whose behaviour is only ever checked by hand.

The property under all of it is the one the package already had and must not lose: the markdown
on disk is the source of truth, and this is an editor over it rather than a second copy of it.
"""

import json
import os
import platform

import pytest

pytest.importorskip("matplotlib")
pytestmark = [pytest.mark.tier1]

from pathlib import Path  # noqa: E402

from rl_researcher import regions, report, run, serve  # noqa: E402
from rl_researcher.config import load_config  # noqa: E402
from rl_researcher.ledger import open_ledger  # noqa: E402

SPEC = "studies/toy-line-fit.toml"
RUN = "toy-line-fit"


@pytest.fixture
def reported(project):
    """A project with the toy run finished and its report on disk."""
    assert run.main([SPEC, "--max-seconds", "5"]) == 0
    assert report.main([RUN]) == 0
    return load_config(), project / "docs" / "toy" / RUN


def _post(config, path, payload, runs=None):
    return serve.api(config, "POST", path, payload, runs=runs if runs is not None else {})


def _get(config, path, runs=None):
    return serve.api(config, "GET", path, runs=runs if runs is not None else {})


# --------------------------------------------------------------------------- who may ask


def test_a_page_on_another_site_cannot_decide_anything_on_your_behalf():
    """The reason this check exists: two of these endpoints assert a human did something."""
    assert serve.allowed("127.0.0.1:7777", "", port=7777) is None
    assert serve.allowed("localhost:7777", "http://localhost:7777", port=7777) is None

    cross = serve.allowed("127.0.0.1:7777", "https://example.com", port=7777)
    assert cross and "example.com" in cross

    other_port = serve.allowed("localhost:7777", "http://localhost:3000", port=7777)
    assert other_port and "not this dashboard" in other_port


def test_a_name_on_the_internet_that_resolves_here_is_still_not_here():
    """Rebinding: the address is local, the Host header is not."""
    rebound = serve.allowed("evil.example:7777", "", port=7777)
    assert rebound and "localhost only" in rebound
    assert serve.allowed("[::1]:7777", "", port=7777) is None


# --------------------------------------------------------------------------- reading


def test_the_board_is_the_same_view_the_state_page_is_built_from(reported):
    config, _out = reported
    code, board = _get(config, "/api/state")
    assert code == 200
    assert board["project"] == config.name
    assert {"waiting", "running", "queued", "decided", "health"} <= set(board)


def test_a_run_arrives_with_its_authored_regions_addressable(reported):
    config, out = reported
    code, view = _get(config, f"/api/run/{RUN}")
    assert code == 200
    assert view["run"] == RUN and view["state"] == "finished"
    assert "decision" in view["authored"] and "reading" in view["authored"]
    assert 'data-region="decision"' in view["article"]
    assert 'type="checkbox"' in view["article"]
    assert view["options"] and view["ticked"] == []
    assert view["page"].endswith("report.html")


def test_a_name_that_is_not_a_run_is_a_refusal_and_not_a_traceback(reported):
    config, _out = reported
    code, said = _get(config, "/api/run/not-a-run")
    assert code == 404 and "not-a-run" in said["error"]


# --------------------------------------------------------------------------- writing


def test_a_region_write_touches_that_region_and_nothing_else(reported):
    """The whole safety property, stated as bytes: everything outside the region is identical."""
    config, out = reported
    md = out / "README.md"
    before = md.read_text(encoding="utf-8")

    code, said = _post(config, "/api/region",
                       {"run": RUN, "region": "reading", "body": "The hinge held.  [F0001]"})
    assert code == 200 and said["changed"] is True

    after = md.read_text(encoding="utf-8")
    was = {p.key: p.body for p in regions.split(before) if isinstance(p, regions.Region)}
    now = {p.key: p.body for p in regions.split(after) if isinstance(p, regions.Region)}
    assert now[("authored", "reading")] == "The hinge held.  [F0001]"
    assert {k for k in was if was[k] != now.get(k)} == {("authored", "reading")}

    outside_before = [p for p in regions.split(before) if isinstance(p, str)]
    outside_after = [p for p in regions.split(after) if isinstance(p, str)]
    assert outside_before == outside_after, "text outside every region is never touched"


def test_the_page_beside_it_is_brought_back_into_step_and_keeps_its_subtitle(reported):
    """A stale page next to a fresh document is the drift this package exists to prevent."""
    config, out = reported
    page = out / "report.html"
    before = serve._subtitle_of(page)
    assert before, "the report writer puts the run and its data snapshot here"

    _post(config, "/api/region", {"run": RUN, "region": "reading", "body": "Something new."})
    assert "Something new." in page.read_text(encoding="utf-8")
    assert serve._subtitle_of(page) == before


def test_a_region_the_document_does_not_have_is_refused_rather_than_appended(reported):
    config, out = reported
    before = (out / "README.md").read_text(encoding="utf-8")
    code, said = _post(config, "/api/region",
                       {"run": RUN, "region": "invented", "body": "hello"})
    assert code == 400 and "invented" in said["error"]
    assert (out / "README.md").read_text(encoding="utf-8") == before


def test_nothing_is_written_into_a_directory_a_run_still_owns(reported):
    """A run rewrites this file when it finishes, so an edit made underneath it would vanish."""
    config, out = reported
    _hold(out)
    before = (out / "README.md").read_text(encoding="utf-8")

    for path, payload in (("/api/region", {"run": RUN, "region": "reading", "body": "x"}),
                          ("/api/decide", {"run": RUN}),
                          ("/api/report", {"run": RUN})):
        code, said = _post(config, path, payload)
        assert code == 409 and "is running" in said["error"], path
    assert (out / "README.md").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- deciding


def test_the_dashboard_refuses_an_unticked_box_in_the_commands_own_words(reported):
    config, _out = reported
    code, said = _post(config, "/api/decide", {"run": RUN, "note": "go on then"})
    assert code == 409 and said["ok"] is False
    assert "no box is ticked" in said["message"]
    assert said["options"] and "A decision has to be made by a person." in said["message"]
    assert not open_ledger(config).query(kind="decision")


def test_ticking_a_box_and_recording_it_is_two_writes_to_the_same_file(reported):
    """Exactly the two steps a person takes by hand, with the file still in the middle."""
    config, out = reported
    body = regions.body_of((out / "README.md").read_text(encoding="utf-8"), "authored", "decision")
    ticked_body = body.replace("- [ ] **go**", "- [x] **go**", 1)

    code, said = _post(config, "/api/region",
                       {"run": RUN, "region": "decision", "body": ticked_body})
    assert code == 200 and said["ticked"], said

    code, said = _post(config, "/api/decide",
                       {"run": RUN, "note": "the variance hinge is doing the work"})
    assert code == 200 and said["ok"] is True and said["finding"]

    rows = open_ledger(config).query(kind="decision")
    assert len(rows) == 1
    assert rows[0].via == "dashboard"
    assert rows[0].note == "the variance hinge is doing the work"
    assert rows[0].commit and rows[0].fingerprint, "provenance is the run's, not the caller's"

    # And the board has moved it out of the waiting list, because the file says so.
    _code, board = _get(config, "/api/state")
    assert RUN not in [w["run"] for w in board["waiting"]]


def test_the_dashboard_never_ticks_a_box_itself(reported):
    """A note is not a decision. Only the file decides."""
    config, _out = reported
    code, _said = _post(config, "/api/decide", {"run": RUN, "note": "obviously go"})
    assert code == 409
    _code, view = _get(config, f"/api/run/{RUN}")
    assert view["ticked"] == []


# --------------------------------------------------------------------------- approving


def test_an_approval_needs_the_quote_and_writes_nothing_without_it(project):
    config = load_config()
    code, said = _post(config, "/api/approve", {"run": RUN, "quote": "   "})
    assert code == 400 and "quote of what the human said" in said["error"]
    assert not list((project / "studies" / "approvals").glob("*.toml"))


def test_an_approval_is_written_with_the_sentence_the_human_said(project):
    config = load_config()
    code, said = _post(config, "/api/approve",
                       {"run": RUN, "quote": "yes, worth it to settle the comparison"})
    assert code == 200 and said["gated"] is True, said
    written = Path(project) / said["approval"]
    assert written.is_file()
    text = written.read_text(encoding="utf-8")
    assert "yes, worth it to settle the comparison" in text
    assert "the human, at the dashboard" in text

    # And the run may now start, which is the only thing an approval is for.
    _code, view = _get(config, f"/api/run/{RUN}")
    assert view["gated"] is True and view["approved"] is True


def test_a_run_under_the_line_gets_told_so_rather_than_given_a_file(project):
    from tests.conftest import chdir, make_project

    root = make_project(project.parent / "cheap", ungated_minutes=600)
    with chdir(root):
        config = load_config()
        code, said = _post(config, "/api/approve", {"run": RUN, "quote": "go on"})
        assert code == 200 and said["gated"] is False
        assert "no approval needed, nothing written" in said["message"]
        assert not list((root / "studies" / "approvals").glob("*.toml"))


# --------------------------------------------------------------------------- the page itself


def test_the_dashboard_fetches_nothing_and_carries_one_program(project):
    html = serve.page(load_config())
    for reach in ("http://", "https://", "//fonts.", "<link", "@import", "<script src"):
        assert reach not in html, reach
    assert html.count("<script") == 1 and html.count("</script>") == 1
    assert html.count("<style") == 1


def test_the_project_bar_is_outside_the_pane_the_script_replaces(project):
    """`paintRun` writes over the whole pane; the theme switch has to survive that."""
    html = serve.page(load_config())
    shell = html.split('<div class="board">')[0]
    assert 'id="reload"' in shell and 'id="stamp"' in shell
    assert html.count('id="pane"') == 1 and 'id="pane"' not in shell


# --------------------------------------------------------------------------- starting, stopping


def _hold(out, pid=None, host=None):
    """Write a run lock into ``out``, as a running process would."""
    (out / ".study-lock.json").write_text(json.dumps(
        {"pid": pid if pid is not None else os.getpid(),
         "host": host if host is not None else platform.node(),
         "run": RUN, "started": "now"}), encoding="utf-8")


def test_two_runs_are_never_started_in_one_directory(reported):
    """The same refusal `run` gives at the command line, before anything is spawned."""
    config, out = reported
    _hold(out)
    code, said = _post(config, "/api/run", {"run": RUN})
    assert code == 409 and "already running" in said["error"]


def test_a_run_this_page_started_is_not_started_twice(reported):
    config, _out = reported
    alive = serve.Launch(proc=type("P", (), {"pid": 4242, "poll": staticmethod(lambda: None)})())
    code, said = _post(config, "/api/run", {"run": RUN}, runs={RUN: alive})
    assert code == 409 and "4242" in said["error"]


def test_stopping_signals_the_process_that_holds_the_directory(reported, monkeypatch):
    import signal

    config, out = reported
    _hold(out)
    sent = []
    monkeypatch.setattr(serve, "_windows", lambda: False)
    monkeypatch.setattr(serve.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    code, said = _post(config, "/api/stop", {"run": RUN})
    assert code == 200 and sent == [(os.getpid(), signal.SIGTERM)]
    assert "checkpoint and stop" in said["message"]


def test_on_windows_stopping_says_what_it_costs_before_it_does_it(reported, monkeypatch):
    """There is no clean stop there, so the page must not offer one as though there were."""
    config, out = reported
    _hold(out)
    sent = []
    monkeypatch.setattr(serve, "_windows", lambda: True)
    monkeypatch.setattr(serve.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    code, said = _post(config, "/api/stop", {"run": RUN})
    assert code == 409 and said["needs"] == "kill" and not sent
    assert "no clean stop on Windows" in said["error"]

    code, said = _post(config, "/api/stop", {"run": RUN, "kill": True})
    assert code == 200 and len(sent) == 1
    assert "last checkpoint survives" in said["message"]


def test_a_lock_left_by_a_dead_process_is_not_something_to_signal(reported, monkeypatch):
    config, out = reported
    _hold(out, pid=999_999)
    monkeypatch.setattr(serve.os, "kill", lambda pid, sig: pytest.fail("signalled a dead pid"))
    code, said = _post(config, "/api/stop", {"run": RUN})
    assert code == 409 and "stale" in said["error"]


def test_a_run_on_another_machine_is_not_ours_to_signal(reported, monkeypatch):
    config, out = reported
    _hold(out, host="some-other-box")
    monkeypatch.setattr(serve.os, "kill", lambda pid, sig: pytest.fail("signalled another host"))
    code, said = _post(config, "/api/stop", {"run": RUN})
    assert code == 409 and "some-other-box" in said["error"]


def test_nothing_running_is_not_a_thing_to_stop(reported):
    config, _out = reported
    code, said = _post(config, "/api/stop", {"run": RUN})
    assert code == 409 and "nothing holds" in said["error"]


def test_the_log_is_read_by_byte_offset_so_the_panel_appends(reported):
    config, out = reported
    code, first = _get(config, f"/api/log/{RUN}")
    assert code == 200 and first["text"] and first["offset"] > 0

    code, again = _get(config, f"/api/log/{RUN}?offset={first['offset']}")
    assert code == 200 and again["text"] == "" and again["offset"] == first["offset"]

    # An offset past the end means the log was replaced, not that there is nothing new.
    code, restarted = _get(config, f"/api/log/{RUN}?offset={first['offset'] + 10_000}")
    assert restarted["restarted"] is True and restarted["text"] == first["text"]


def test_a_spec_nobody_has_run_is_still_on_the_board(project):
    """Otherwise the one thing you cannot do from the dashboard is start a study."""
    config = load_config()
    _code, board = _get(config, "/api/state")
    assert [r["run"] for r in board["ready"]] == [RUN]
    assert board["waiting"] == [] and board["running"] == []
    _code, view = _get(config, f"/api/run/{RUN}")
    assert view["state"] == "not started" and view["article"] == ""
