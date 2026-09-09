"""Real HTTP + Chromium workflow acceptance. Run with RL_BROWSER_TESTS=1."""

import os
import threading

import pytest

from rl_researcher import report, run, serve
from rl_researcher.config import load_config
from rl_researcher.regions import set_region

pytestmark = pytest.mark.skipif(
    os.environ.get("RL_BROWSER_TESTS") != "1",
    reason="dedicated browser suite: set RL_BROWSER_TESTS=1",
)


@pytest.fixture
def browser_board(project):
    from playwright.sync_api import sync_playwright

    config = load_config(project)
    server = serve.Board(("127.0.0.1", 0), serve.Handler, config)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        yield config, page, f"http://127.0.0.1:{server.server_address[1]}"
        browser.close()
    server.shutdown()
    server.server_close()
    worker.join(5)


def test_held_unknown_visible_release_and_queue_catalog(browser_board):
    from playwright.sync_api import expect

    config, page, url = browser_board
    config.path("queue").write_text(
        '[[entry]]\nrun="visitation-canary-v1"\nhold=true\nwhy="Review engine launch"',
        encoding="utf-8",
    )
    page.goto(url)
    section = page.locator("section").filter(
        has=page.get_by_role("heading", name="On hold", exact=True)
    )
    expect(section).to_contain_text("visitation-canary-v1")
    expect(section).to_contain_text("No active registration")
    section.get_by_label("Release explanation").fill("The research constraint is resolved")
    section.get_by_role("button", name="Release hold").click()
    expect(section).not_to_contain_text("visitation-canary-v1")
    page.reload()
    expect(page.get_by_role("button", name="Remove from queue")).to_be_visible()
    page.get_by_role("button", name="Remove from queue").click()
    page.get_by_role("link", name="Specifications", exact=True).click()
    with page.expect_response("**/api/queue/add") as saved:
        page.get_by_role("button", name="Add to queue").click()
    assert saved.value.status == 200, saved.value.text()
    page.get_by_role("link", name="Overview", exact=True).last.click()
    next_work = page.locator("section").filter(
        has=page.get_by_role("heading", name="Next work", exact=True)
    )
    expect(next_work).to_contain_text("toy-line-fit")
    next_work.get_by_role("button", name="Remove from queue").click()
    expect(next_work).not_to_contain_text("toy-line-fit")


def test_reviewed_only_report_has_no_redundant_decision(browser_board):
    from playwright.sync_api import expect

    config, page, url = browser_board
    assert run.main(["toy-line-fit", "--max-seconds", "5"]) == 0
    assert report.main(["toy-line-fit"]) == 0
    md = config.out_root("toy") / "toy-line-fit" / "README.md"
    md.write_text(
        set_region(
            md.read_text(encoding="utf-8"),
            "authored",
            "decision",
            "- [ ] **Reviewed** — acknowledge evidence; no experiment approved.",
        ),
        encoding="utf-8",
    )
    page.goto(url + "/#run=toy-line-fit&view=overview")
    expect(page.get_by_role("button", name="Mark reviewed", exact=True)).to_be_visible()
    expect(page.locator("#decision-form")).to_have_count(0)
    page.get_by_role("button", name="Mark reviewed", exact=True).click()
    expect(page.locator(".review-card")).to_contain_text("Reviewed ·")
    page.reload()
    expect(page.get_by_role("button", name="Mark reviewed", exact=True)).to_have_count(0)


@pytest.mark.parametrize("choice", ["go", "Reviewed"])
def test_legacy_resolved_history_is_not_reopened(browser_board, choice):
    from playwright.sync_api import expect
    from rl_researcher.ledger import Finding, open_ledger

    config, page, url = browser_board
    assert run.main(["toy-line-fit", "--max-seconds", "5"]) == 0
    assert report.main(["toy-line-fit"]) == 0
    if choice == "Reviewed":
        md = config.out_root("toy") / "toy-line-fit" / "README.md"
        md.write_text(set_region(md.read_text(encoding="utf-8"), "authored", "decision",
                                 "- [x] Reviewed"), encoding="utf-8")
    open_ledger(config).add(Finding(kind="decision", run="toy-line-fit", choices=[choice]))
    page.goto(url)
    attention = page.locator("section").filter(
        has=page.get_by_role("heading", name="Needs attention", exact=True)
    )
    expect(attention).not_to_contain_text("toy-line-fit")
    history = page.locator("section").filter(
        has=page.get_by_role("heading", name="History", exact=True)
    )
    expect(history).to_contain_text("Evidence revision unknown")
    page.goto(url + "/#run=toy-line-fit&view=overview")
    expect(page.locator(".review-card")).to_contain_text("Evidence revision unknown")
    expect(page.get_by_role("button", name="Mark reviewed", exact=True)).to_have_count(0)
    expect(page.locator("#decision-form")).to_have_count(0)
    if choice == "go":
        expect(page.locator(".decision-card")).to_contain_text("Evidence revision unknown")
        expect(page.locator(".decision-card")).not_to_contain_text("Current evidence revision")


def test_broken_registration_does_not_break_board(browser_board):
    from playwright.sync_api import expect

    config, page, url = browser_board
    (config.path("specs") / "broken.toml").write_text("name=[oops", encoding="utf-8")
    page.goto(url + "/#catalog")
    expect(page.locator('[data-item="broken"]')).to_contain_text("Open specification")
    expect(page.locator('[data-item="toy-line-fit"]')).to_contain_text("Add to queue")
    expect(page.locator("#connection")).to_have_text("Live · 5s")
    page.locator('[data-item="broken"]').get_by_role("link", name="Open specification").click()
    expect(page.locator("#document-body")).to_contain_text("name=[oops")


@pytest.mark.parametrize(
    "failure,label",
    [
        ("transport", "Connection lost"),
        ("http", "Server request failed"),
        ("response", "Invalid server response"),
        ("render", "Could not display updates"),
    ],
)
def test_distinct_failure_keeps_previous_view_and_recovers(browser_board, failure, label):
    from playwright.sync_api import expect

    config, page, url = browser_board
    page.goto(url)
    expect(page.get_by_role("heading", name="On hold", exact=True)).to_be_visible()
    if failure == "render":
        page.evaluate(
            "() => { window.originalPaint = Workflow.paintHome; Workflow.paintHome = () => { throw Error('render fixture'); }; }"
        )
    else:

        def intercept(route):
            if failure == "transport":
                route.abort()
            elif failure == "http":
                route.fulfill(
                    status=500, content_type="application/json", body='{"error":"fixture failure"}'
                )
            else:
                route.fulfill(status=200, content_type="application/json", body='{"wrong":true}')

        page.route("**/api/state", intercept)
    page.evaluate("window.dispatchEvent(new Event('workflowchange'))")
    expect(page.locator("#connection")).to_contain_text(label)
    expect(page.get_by_role("heading", name="On hold", exact=True)).to_be_visible()
    if failure == "render":
        page.evaluate("() => { Workflow.paintHome = window.originalPaint; }")
    else:
        page.unroute("**/api/state")
    page.locator("#reload").click()
    expect(page.locator("#connection")).to_have_text("Live · 5s")


def test_review_leaves_decision_pending_and_new_evidence_requires_reconsideration(browser_board):
    import json
    from playwright.sync_api import expect
    from rl_researcher import atomic

    config, page, url = browser_board
    assert run.main(["toy-line-fit", "--max-seconds", "5"]) == 0
    assert report.main(["toy-line-fit"]) == 0
    page.goto(url + "/#run=toy-line-fit&view=overview")
    page.evaluate("""() => {
        const original = window.fetch;
        const gate = new Promise(resolve => { window.releaseReviewRefresh = resolve; });
        window.fetch = async (...args) => {
            if (args[0] === '/api/state') await gate;
            return original(...args);
        };
    }""")
    page.get_by_role("button", name="Mark reviewed", exact=True).click()
    expect(page.locator(".review-card")).to_contain_text("Reviewed ·")
    expect(page.locator("#decision-form")).to_be_visible()
    page.locator("#reason").fill("The evidence supports this research direction")
    expect(page.locator('[data-choice="0"]')).to_be_disabled()
    page.evaluate("() => window.releaseReviewRefresh()")
    expect(page.locator('[data-choice="0"]')).to_be_enabled()
    page.locator('[data-choice="0"]').click()
    expect(page.locator("#form-slot")).to_contain_text("Decision recorded")
    summary = config.out_root("toy") / "toy-line-fit" / "results.json"
    data = json.loads(summary.read_text(encoding="utf-8"))
    data["new_evidence"] = 1
    atomic.write_json(summary, data)
    page.reload()
    expect(page.locator(".review-card")).to_contain_text("Earlier evidence")
    expect(page.get_by_role("button", name="Mark reviewed", exact=True)).to_be_visible()
    expect(page.locator("#decision-form")).to_be_visible()


def test_release_approval_and_explicit_toy_launch_are_separate(browser_board):
    from playwright.sync_api import expect

    config, page, url = browser_board
    config.gate.ungated_wall_minutes = 0
    config.path("queue").write_text(
        '[[entry]]\nrun="toy-line-fit"\nhold=true\nwhy="Review first"', encoding="utf-8"
    )
    page.goto(url)
    held = page.locator("section").filter(
        has=page.get_by_role("heading", name="On hold", exact=True)
    )
    held.get_by_label("Release explanation").fill("Research review complete")
    with page.expect_response("**/api/queue/release") as released:
        held.get_by_role("button", name="Release hold").click()
    assert released.value.status == 200
    assert not serve._RUNS
    page.goto(url + "/#run=toy-line-fit&view=overview")
    expect(page.get_by_role("button", name="Approve compute", exact=True)).to_be_visible()
    page.get_by_label("Approval note").fill("Approved the small CPU fixture run")
    # Hold the final board refresh so an immediately visible Start control cannot
    # silently swallow a click while the preceding approval is still busy.
    page.evaluate("""() => {
        const original = window.fetch;
        const gate = new Promise(resolve => { window.releaseRefresh = resolve; });
        window.fetch = async (...args) => {
            if (args[0] === '/api/state') await gate;
            return original(...args);
        };
    }""")
    page.get_by_role("button", name="Approve compute", exact=True).click()
    start = page.get_by_role("button", name="Start run", exact=True)
    expect(start).to_be_visible()
    expect(start).to_be_disabled()
    page.evaluate("() => window.releaseRefresh()")
    expect(start).to_be_enabled()
    with page.expect_response("**/api/run") as started:
        page.get_by_role("button", name="Start run", exact=True).click()
    assert started.value.status == 200, started.value.text()
    page.wait_for_function(
        "async () => (await (await fetch('/api/run/toy-line-fit?light=1')).json()).state === 'finished'",
        timeout=60000,
    )


def test_dirty_decision_stays_bound_to_the_evidence_that_was_shown(browser_board):
    import json
    from playwright.sync_api import expect
    from rl_researcher import atomic

    config, page, url = browser_board
    assert run.main(["toy-line-fit", "--max-seconds", "5"]) == 0
    assert report.main(["toy-line-fit"]) == 0
    page.goto(url + "/#run=toy-line-fit&view=overview")
    page.locator("#reason").fill("Reason based on the evidence originally shown")
    summary = config.out_root("toy") / "toy-line-fit" / "results.json"
    data = json.loads(summary.read_text(encoding="utf-8"))
    data["changed_while_typing"] = True
    atomic.write_json(summary, data)
    # Polling can update data, but must never rebind a dirty form to that data.
    with page.expect_response("**/api/run/toy-line-fit?light=1"):
        page.evaluate("window.dispatchEvent(new Event('workflowchange'))")
    with page.expect_response("**/api/decide") as saved:
        page.locator('[data-choice="0"]').click()
    assert saved.value.status == 409
    expect(page.locator("#reason")).to_have_value("Reason based on the evidence originally shown")
    expect(page.get_by_role("button", name="Review latest evidence")).to_be_visible()
