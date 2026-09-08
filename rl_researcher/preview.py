"""Read-only historical UI preview. Never imports a consuming project's adapters.

Usage: python -m rl_researcher.preview SNAPSHOT --port 7784
The snapshot must contain manifest.json, studies/, and copied docs/ reports.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlparse

from rl_researcher.artefacts.overview import research_summary
from rl_researcher.artefacts.state import options, section, ticked
from rl_researcher.board_view import revision
from rl_researcher.config import Config
from rl_researcher.presentation import document, hypothesis_view
from rl_researcher.units import stamp_of
from rl_researcher.render import editable_article
from rl_researcher.serve import Board, Handler, page
from rl_researcher.spec import RunSpec, MetricSpec, load_toml


class Snapshot:
    def __init__(self, root: Path):
        self.root = root.resolve()
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.state: dict[str, Any] = {
            "project": "Auto-SM64", "generated": manifest["captured_at"],
            "context": {"snapshot": manifest["captured_at"],
                        "goal": "Learn to play SM64 from frames, without task-specific rewards or SM64 demonstrations in training.",
                        "focus": "Resolve the reward formulation: no candidate cleared both error-correlation and plannability screens.",
                        "plan": "docs/PLAN.md"},
            "waiting": [], "running": [], "ready": [], "decided": [],
            "queued": load_toml(root / "queue.toml").get("entry", []), "health": {},
        }
        self.state["findings"] = []
        self.runs: dict[str, Any] = {}
        self.reports: dict[str, tuple[Path, str]] = {}
        for path in sorted((root / "studies").glob("*.toml")):
            raw = load_toml(path)
            spec = RunSpec(name=raw["name"], kind=raw.get("kind", "study"),
                           hypothesis=raw["hypothesis"], seeds=raw["seeds"],
                           metrics=[MetricSpec(**m) for m in raw["metrics"]],
                           conjunction=raw.get("conjunction", []))
            outputs = [root / "docs" / folder / spec.name for folder in ("studies", "loops", "measurements")]
            out = next((p for p in outputs if (p / "results.json").is_file()), None)
            if out is None:
                continue
            summary = json.loads((out / "results.json").read_text(encoding="utf-8"))
            text = (out / "README.md").read_text(encoding="utf-8")
            research = research_summary(spec, summary, provisional=False)
            # Domain warnings are evidence, not a generic aggregate's success criterion.
            warnings = re.findall(r"(?m)^> (.+)$", text)
            research.setdefault("warnings", []).extend(w for w in warnings if "calibrat" in w.lower())
            body = section(text, "Decision") or ""
            total = len(list(out.glob("*/seed*/results.json")))
            self.runs[spec.name] = {
                "run": spec.name, "kind": spec.kind, "question": spec.hypothesis,
                **hypothesis_view(root, spec.name, spec.hypothesis),
                "state": "finished", "done": total, "total": total, "research": research,
                "results": True, "page": (out / "README.md").relative_to(root).as_posix(),
                "spec": path.relative_to(root).as_posix(), "dashboard": "", "authored": [],
                "options": options(body), "ticked": ticked(body), "decision": None,
                "revision": revision(text), "alerts": [], "gated": False, "approved": False,
                "cost": {"wall_seconds": None, "money_usd": None, "basis": "historical", "samples": 0},
                "progress": {}, "resumable": False, "snapshot": manifest["captured_at"],
            }
            reading = re.sub(r"<!--.*?-->", "", section(text, "Reading") or "", flags=re.S).strip()
            if reading and "write here:" not in reading[:30]:
                generated = re.search(r"generated=([^ >]+)", text)
                self.state["findings"].append({"run": spec.name, "date": generated.group(1) if generated else "",
                                               "summary": hypothesis_view(root, spec.name, spec.hypothesis)["finding_summary"] or reading.split("\n\n")[0], "source": "Report reading"})
            progress = [json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*/seed*/progress.json")]
            times = [stamp_of(p.get("updated")) for p in progress]
            finished = max((t for t in times if t is not None), default=None)
            self.reports[spec.name] = (out, text)
            self.state["waiting"].append({"run": spec.name, "outcome": research["headline"],
                                           "options": options(body), "finished": finished.isoformat() if finished else ""})
        self.state["findings"].sort(key=lambda r: r["date"], reverse=True)
        self.state["findings"] = self.state["findings"][:3]

    def api(self, path: str) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(path)
        parts = parsed.path.strip("/").split("/")
        if parts == ["api", "document"]:
            return document(self.root, parse_qs(parsed.query).get("path", [""])[0])
        if parts == ["api", "state"]:
            return 200, self.state
        name = unquote("/".join(parts[2:]))
        if name not in self.runs:
            return 404, {"error": "This run is not included in the historical snapshot."}
        if parts[1] == "run":
            return 200, self.runs[name]
        if parts[1] == "log":
            return 200, {"offset": 0, "text": "Logs were not copied into this snapshot.", "events": []}
        if parts[1] == "content":
            view = parse_qs(parsed.query).get("view", ["results"])[0]
            out, text = self.reports[name]
            if view == "units":
                rows = ["## Trials", "", "| Unit | Status | Steps |", "|---|---|---|"]
                for p in sorted(out.glob("*/seed*/progress.json")):
                    d = json.loads(p.read_text(encoding="utf-8"))
                    rows.append(f"| {p.parent.relative_to(out).as_posix()} | {d.get('status')} | {d.get('step')} / {d.get('max_steps')} |")
                text = "\n".join(rows)
            if view == "results":
                fragments = []
                for piece in re.split(r"(?m)(?=^## )", text):
                    title = re.match(r"## (.+)", piece)
                    if not title or title.group(1).startswith("Decision") or title.group(1) in ("Summary", "Question", "Registered metrics", "Units"):
                        continue
                    rendered = editable_article(piece, kind="report", run=name, embed_images_from=out)
                    if title.group(1) in ("Provenance", "Ledger", "Method"):
                        rendered = f"<details><summary>{title.group(1)}</summary>{rendered}</details>"
                    fragments.append(rendered)
                rendered = "".join(fragments)
            else:
                rendered = editable_article(text, kind="report", run=name, embed_images_from=out)
            return 200, {"html": rendered,
                         "css": "", "revision": revision(text)}
        return 404, {"error": "Unknown preview endpoint."}


class PreviewHandler(Handler):
    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        if self.path.startswith("/api/"):
            code, data = cast(PreviewBoard, self.board).snapshot.api(self.path)
            self._json(code, data)
        elif urlparse(self.path).path in ("/", "/index.html"):
            self._send(200, page(self.board.config).encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._file(urlparse(self.path).path)

    def do_POST(self) -> None:  # noqa: N802
        if self._guard():
            self._json(403, {"error": "Historical preview is read-only. No run or report can be changed."})


class PreviewBoard(Board):
    def __init__(self, snapshot: Snapshot, port: int):
        super().__init__(("127.0.0.1", port), PreviewHandler,
                         Config(root=snapshot.root, name=snapshot.state["project"]))
        self.snapshot = snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--port", type=int, default=7784)
    args = parser.parse_args()
    board = PreviewBoard(Snapshot(args.snapshot), args.port)
    print(f"Historical preview: http://127.0.0.1:{board.server_port}", flush=True)
    board.serve_forever()


if __name__ == "__main__":
    main()
