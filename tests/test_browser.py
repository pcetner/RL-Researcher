"""Rendered HTTP acceptance suite for CI; local interactive checks use the Browser plugin."""

import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from rl_researcher.serve import create_server

pytestmark = pytest.mark.skipif(
    os.environ.get("RESEARCH_BROWSER_TESTS") != "1",
    reason="Set RESEARCH_BROWSER_TESTS=1 in the browser acceptance environment",
)


def test_rendered_controls_refresh_and_connection(tmp_path):
    from playwright.sync_api import sync_playwright, expect

    example = Path(__file__).parents[1] / "examples/deterministic"
    for name in ("research.json", "definition.json", "experiment.py"):
        shutil.copy2(example / name, tmp_path / name)
    definition = json.loads((tmp_path / "definition.json").read_text())
    for trial in definition["trials"]:
        trial["delay"] = 0.1
    (tmp_path / "definition.json").write_text(json.dumps(definition))
    server = create_server(tmp_path, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/#experiment=deterministic")
            start = page.get_by_role("button", name="Start experiment", exact=True)
            expect(start).to_be_disabled()
            page.get_by_role("button", name="Validate configuration", exact=True).click()
            expect(start).to_be_enabled(timeout=10000)
            start.click()
            stop = page.get_by_role("button", name="Stop safely", exact=True)
            expect(stop).to_be_enabled(timeout=10000)
            expect(page.get_by_role("progressbar", name="Study decisions completed")).to_be_visible()
            expect(page.get_by_text("Resume unavailable: Execution is Running.", exact=True)).to_have_count(0)
            page.wait_for_function(
                "/Saved through decision [1-9]/.test(document.querySelector('#app').textContent)"
            )
            stop.click()
            resume = page.get_by_role("button", name="Resume", exact=True)
            expect(resume).to_be_enabled(timeout=15000)
            page.get_by_role("button", name="First calculation", exact=True).click()
            page.locator("#diagnostics > summary").click()
            page.reload()
            expect(page.locator("#diagnostics")).to_have_attribute("open", "")
            expect(page.locator("#trial")).to_have_value("first")
            resume.click()
            expect(page.locator(".tag")).to_have_text("Completed", timeout=45000)
            expect(page.get_by_role("cell", name="5050", exact=True)).to_have_count(2)
            page.route("**/api/catalog", lambda route: route.abort())
            expect(page.locator("#connection")).to_contain_text("cached", timeout=10000)
            page.unroute("**/api/catalog")
            expect(page.locator("#connection")).to_contain_text("Connected", timeout=10000)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
