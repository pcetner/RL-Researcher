"""Loopback-only webpage and operational HTTP service. Worker lifetime is independent."""

import argparse
import html
import json
import mimetypes
import secrets
import socket
import os
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from . import board_view, config, experiment, runner


def create_server(root, port):
    root = Path(root).resolve()
    token = secrets.token_urlsafe(32)
    ui = Path(__file__).parent / "ui"
    try:
        from .campaigns import Campaigns
        campaigns = Campaigns(root)
        campaign_error = None
    except ImportError:
        campaigns = None
        campaign_error = "Install the companion haws-core package to use campaign controls"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, content_type="application/json", policy=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Cache-Control", "no-store" if content_type == "application/json" else "no-cache"
            )
            self.send_header(
                "Content-Security-Policy",
                policy
                or "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def directory(self, identity):
            p = (config.store(root) / identity).resolve()
            if p.parent != config.store(root).resolve() or not (p / "manifest.json").is_file():
                raise ValueError("Unknown execution")
            return p

        def do_GET(self):
            try:
                host = self.headers.get("Host", "").split(":", 1)[0]
                if host not in {"127.0.0.1", "localhost"}:
                    return self.reply(403, {"error": "Invalid local host"})
                uri = urllib.parse.urlparse(self.path)
                args = urllib.parse.parse_qs(uri.query)
                path = uri.path
                if path == "/api/campaigns":
                    data = campaigns.inspect() if campaigns else {"campaigns": [], "runtime": {"ready": False, "blockers": [campaign_error]}}
                    return self.reply(200, dict(data, token=token))
                if path.startswith("/legacy/"):
                    p = (root / urllib.parse.unquote(path[len("/legacy/") :])).resolve()
                    if not p.is_relative_to(root / "docs"):
                        raise ValueError("Historical resources must be inside project docs")
                    return self.reply(
                        200,
                        p.read_bytes(),
                        mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                        "sandbox; default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'",
                    )
                if path == "/api/catalog":
                    return self.reply(
                        200, dict(board_view.catalog(root), token=token, project=str(root))
                    )
                if path == "/api/execution":
                    return self.reply(
                        200,
                        board_view.execution(
                            self.directory(args["id"][0]), int(args.get("after", ["0"])[0])
                        ),
                    )
                if path == "/api/samples":
                    return self.reply(
                        200, board_view.samples(self.directory(args["id"][0]), args["trial"][0])
                    )
                if path == "/api/artifact":
                    directory = self.directory(args["id"][0])
                    p = (directory / args["path"][0]).resolve()
                    if not p.is_relative_to(directory):
                        raise ValueError("Invalid artifact path")
                    # HTML is rendered as text so project-authored documents cannot operate the application.
                    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    if mime in ("text/html", "image/svg+xml", "application/javascript"):
                        mime = "text/plain; charset=utf-8"
                    return self.reply(200, p.read_bytes(), mime)
                if path == "/api/history":
                    records = config.load(root).get("history", [])
                    return self.reply(200, records)
                if path == "/history":
                    records = config.load(root).get("history", [])
                    body = "<h1>Historical reports</h1><p>Preserved evidence. Former controls are inactive.</p>"
                    for i, r in enumerate(records):
                        body += (
                            '<p><a href="/historical?index='
                            + str(i)
                            + '">'
                            + html.escape(r["label"])
                            + "</a></p>"
                        )
                    return self.reply(200, body.encode(), "text/html; charset=utf-8")
                if path == "/historical":
                    item = config.load(root)["history"][int(args["index"][0])]
                    p = (root / item["path"]).resolve()
                    if not p.is_relative_to(root):
                        raise ValueError("Invalid historical path")
                    # Serve preserved HTML in an empty sandbox; scripts, forms, navigation and same-origin access are disabled.
                    body = (
                        "<!doctype html><title>Historical evidence</title><h1>"
                        + html.escape(item["label"])
                        + '</h1><p>Historical report — controls inactive.</p><iframe sandbox src="/legacy/'
                        + urllib.parse.quote(item["path"])
                        + '" width="100%" height="900"></iframe>'
                    )
                    return self.reply(200, body.encode(), "text/html; charset=utf-8")
                filename = "index.html" if path == "/" else ("campaign.html" if path == "/campaign" else path.lstrip("/"))
                p = (ui / filename).resolve()
                if not p.is_relative_to(ui.resolve()):
                    raise ValueError("Invalid path")
                return self.reply(
                    200,
                    p.read_bytes(),
                    mimetypes.guess_type(p.name)[0] or "application/octet-stream",
                )
            except (ValueError, KeyError, IndexError, OSError) as e:
                self.reply(400, {"error": str(e)})

        def do_POST(self):
            try:
                if self.headers.get("Host", "").split(":", 1)[0] not in {"127.0.0.1", "localhost"}:
                    raise ValueError("Invalid local host")
                if self.headers.get("X-Research-Token") != token:
                    raise ValueError("Reload the page to reconnect to the local server")
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + self.headers.get("Host", ""):
                    raise ValueError("Cross-origin operation refused")
                size = int(self.headers.get("Content-Length", 0))
                if size > 65536:
                    raise ValueError("Request too large")
                body = json.loads(self.rfile.read(size))
                action = self.path
                if action == "/api/campaign-command":
                    if not campaigns:
                        raise ValueError(campaign_error)
                    return self.reply(200, campaigns.command(body))
                if action == "/api/validate":
                    result = experiment.validate(root, body["experiment"])
                    return self.reply(200, {"revision": result["revision"]})
                if action == "/api/start":
                    if campaigns and any(s['status'] in {'active','closing'} for s in campaigns.store.inspect(campaigns.owner)):
                        raise ValueError('Campaign owns the compute slot; use campaign controls')
                    identity = runner.start(
                        root, body["experiment"], body["revision"], body["request_id"]
                    )
                    return self.reply(200, {"id": identity})
                directory = self.directory(body["id"])
                if action == "/api/stop":
                    runner.request_stop(directory)
                elif action == "/api/force":
                    runner.request_stop(directory, True)
                elif action == "/api/resume":
                    if campaigns and any(s['status'] in {'active','closing'} for s in campaigns.store.inspect(campaigns.owner)):
                        raise ValueError('Campaign owns the compute slot; use campaign controls')
                    runner.resume(directory)
                else:
                    raise ValueError("Unknown operation")
                self.reply(200, {"id": directory.name})
            except Exception as e:
                self.reply(409, {"error": str(e)})

    class LocalServer(ThreadingHTTPServer):
        # Windows SO_REUSEADDR permits two live listeners on the same port.
        # Exclusive binding makes launcher reopening reuse the existing server.
        allow_reuse_address = False

        def server_bind(self):
            if os.name == "nt":
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    server = LocalServer(("127.0.0.1", port), Handler)

    campaign_shutdown = threading.Event()
    original_close = server.server_close
    def close():
        campaign_shutdown.set()
        original_close()
    server.server_close = close

    def reconcile_campaigns():
        while not campaign_shutdown.is_set():
            if campaigns:
                campaigns.refresh()
            campaign_shutdown.wait(2)

    threading.Thread(target=reconcile_campaigns,daemon=True).start()

    def research_cycles():
        from .research import refresh_all
        while not campaign_shutdown.is_set():
            if campaigns:
                refresh_all(campaigns)
            campaign_shutdown.wait(2)
    if campaigns:
        threading.Thread(target=research_cycles,daemon=True).start()

    def reconcile():
        while True:
            try:
                runner.recover_orphans(root)
                for directory in config.store(root).iterdir():
                    if (directory / "manifest.json").is_file():
                        board_view.prune_previews(directory)
            except OSError:
                pass
            time.sleep(10)

    threading.Thread(target=reconcile, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser(description="Internal local application startup")
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=7792)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    try:
        server = create_server(args.root, args.port)
    except OSError:
        from urllib.request import urlopen

        existing = json.load(urlopen(url + "api/catalog", timeout=3))
        if Path(existing["project"]).resolve() != Path(args.root).resolve():
            raise RuntimeError("Port belongs to a different project")
        if args.open:
            webbrowser.open(url)
        return
    if args.open:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
