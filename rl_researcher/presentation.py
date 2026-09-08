"""Display summaries and project documents, separate from registered research data."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from rl_researcher.render import _renderer, strip_regions


def hypothesis_view(root: Path, name: str, text: str) -> dict[str, str]:
    # Optional editorial copy lives outside the registered spec and its fingerprint.
    try:
        notes = json.loads((root / "research-ui.json").read_text(encoding="utf-8")).get(name, {})
    except (OSError, ValueError, AttributeError):
        notes = {}
    if not isinstance(notes, dict):
        notes = {}
    flat = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z`*])", flat)
    prediction = next((s for s in sentences if s.startswith("Prediction:")), sentences[0] if sentences else "")
    detail = [s for s in sentences if s != prediction]
    # An extract, not a generated scientific interpretation. Original remains in the document panel.
    return {"question_summary": str(notes.get("question", prediction.removeprefix("Prediction:").strip())),
            "hypothesis_summary": str(notes.get("hypothesis", " ".join(detail[:3]))),
            "finding_summary": str(notes.get("finding", ""))}


def document(root: Path, requested: str) -> tuple[int, dict[str, Any]]:
    root = root.resolve()
    path = (root / requested.lstrip("/")).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return 404, {"error": "Document not found in this project."}
    if path.suffix.lower() not in (".md", ".markdown", ".toml", ".txt", ".json"):
        return 400, {"error": "This file cannot be displayed as a document."}
    if path.stat().st_size > 2_000_000:
        return 413, {"error": "This document is too large to display."}
    text = path.read_text(encoding="utf-8")
    md = _renderer(None)
    if path.suffix.lower() in (".md", ".markdown"):
        tokens = md.parse(strip_regions(text))
        for block in tokens:
            for token in block.children or []:
                attribute = "src" if token.type == "image" else "href" if token.type == "link_open" else ""
                if not attribute:
                    continue
                value = token.attrGet(attribute) or ""
                parsed = urlsplit(value)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = (root / unquote(parsed.path).lstrip("/") if parsed.path.startswith("/")
                          else path.parent / unquote(parsed.path)).resolve()
                if not target.is_relative_to(root):
                    token.attrSet(attribute, "#")
                    continue
                relative = target.relative_to(root).as_posix()
                token.attrSet(attribute, "/" + quote(relative, safe="/") + ("#" + parsed.fragment if parsed.fragment else ""))
                if attribute == "href" and target.suffix.lower() in (".md", ".markdown", ".toml", ".txt", ".json"):
                    token.attrSet("data-document", relative)
        rendered = md.renderer.render(tokens, md.options, {})
    else:
        import html
        rendered = "<pre><code>" + html.escape(text) + "</code></pre>"
    return 200, {"title": path.name, "path": path.relative_to(root).as_posix(),
                 "html": '<article class="doc">' + rendered + "</article>"}
