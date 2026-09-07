"""Markdown to one self-contained HTML page.

The markdown file is the artefact; the HTML beside it is the same content in a fixed layout,
so every report looks like every other report and a reader learns the shape once. The page has
to survive being opened from a file:// path with no network, so every image is inlined as a
data URI and nothing is fetched: no CDN, no web font, no script from anywhere.

The rendering is deliberately narrow. Raw HTML in the markdown is escaped rather than passed
through, because an artefact that can inject markup is an artefact whose layout is not the
kind's layout any more. Tables and strikethrough are on, because the ledger uses both.
"""

from __future__ import annotations

import base64
import html as html_mod
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Optional

from rl_researcher import atomic
from rl_researcher.style import BASE_CSS, THEME_BUTTONS, THEME_SCRIPT

#: Over this, a PNG is re-encoded as JPEG before being inlined. A report with twenty rollout
#: strips in it is otherwise a 40 MB file that a browser will not open twice.
JPEG_OVER_BYTES = 150_000

DOC_CSS = """
.page-head { display:flex; align-items:center; gap:12px; flex-wrap:wrap;
             max-width: 62rem; margin: 0 auto; padding: 1rem 1.25rem 0; }
.page-head > div { flex:1 }
.page-head .sub { margin:.15rem 0 0; color:var(--muted); font-size:.85rem }
.missing-figure { color: var(--crit); font-size: .9em }
.doc { max-width: 62rem; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
.doc h1 { font-size: 1.6rem; margin: 0 0 .2rem; letter-spacing: -0.01em; }
.doc h2 { font-size: 1.15rem; margin: 2rem 0 .6rem; padding-bottom: .3rem;
          border-bottom: 1px solid var(--line); }
.doc h3 { font-size: 1rem; margin: 1.4rem 0 .4rem; color: var(--muted); }
.doc p, .doc li { line-height: 1.6; }
.doc code { background: var(--code); padding: .1em .35em; border-radius: 3px; font-size: .9em; }
.doc pre { background: var(--code); padding: .75rem 1rem; border-radius: 6px; overflow-x: auto; }
.doc pre code { background: none; padding: 0; }
.doc blockquote { margin: 1rem 0; padding: .1rem 1rem; border-left: 3px solid var(--line);
                  color: var(--muted); }
.doc table { border-collapse: collapse; margin: .8rem 0; font-size: .92rem; }
.doc th, .doc td { border: 1px solid var(--line); padding: .35rem .6rem; text-align: left; }
.doc th { background: var(--code); font-weight: 600; }
.doc tr:nth-child(even) td { background: color-mix(in srgb, var(--code) 45%, transparent); }
.doc img { max-width: 100%; height: auto; border-radius: 4px; }
.doc figure { margin: 1.2rem 0; }
.doc figcaption { color: var(--muted); font-size: .86rem; margin-top: .35rem; }
.doc del { color: var(--muted); }
.doc hr { border: 0; border-top: 1px solid var(--line); margin: 2rem 0; }
/* A table wide enough to need it scrolls inside itself; the page never scrolls sideways. */
.doc .table-wrap { overflow-x: auto; }
"""


def _data_uri(path: Path) -> Optional[str]:
    """``path`` as a ``data:`` URI, or ``None`` if it cannot be read.

    A missing image is not fatal: a report is still worth reading with a figure missing, and
    failing the whole render over one absent PNG would take the numbers down with it.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime == "image/png" and len(raw) > JPEG_OVER_BYTES:
        converted = _to_jpeg(raw)
        if converted is not None:
            raw, mime = converted, "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _to_jpeg(raw: bytes) -> Optional[bytes]:
    try:
        import io

        from PIL import Image
    except ImportError:
        return None
    try:
        img: Any = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - an image we cannot convert is inlined as it is
        return None


def _renderer(base: Optional[Path]):
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable("table")
    md.enable("strikethrough")
    # markdown-it-py types the renderer as a protocol without `rules`, but every concrete
    # renderer has it and overriding a rule is the documented way to change one tag.
    rules: Any = md.renderer.rules  # type: ignore[attr-defined]

    default_image = rules.get("image")

    def image(tokens, idx, options, env):
        token = tokens[idx]
        src = token.attrGet("src") or ""
        if base is not None and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", src):
            uri = _data_uri((base / src).resolve())
            if uri is not None:
                token.attrSet("src", uri)
            else:
                alt = html_mod.escape(token.content or src)
                return f'<span class="missing-figure">[missing figure: {alt}]</span>'
        return default_image(tokens, idx, options, env) if default_image else ""

    rules["image"] = image

    default_table_open = rules.get("table_open")
    default_table_close = rules.get("table_close")

    def table_open(tokens, idx, options, env):
        inner = default_table_open(tokens, idx, options, env) if default_table_open else "<table>\n"
        return '<div class="table-wrap">' + inner

    def table_close(tokens, idx, options, env):
        inner = default_table_close(tokens, idx, options, env) if default_table_close else "</table>\n"
        return inner + "</div>\n"

    rules["table_open"] = table_open
    rules["table_close"] = table_close
    return md


def md_to_html(markdown: str, *, kind: str, title: str, embed_images_from: Optional[Path] = None,
               subtitle: str = "", extra_css: str = "") -> str:
    """One page: the base stylesheet, the document rules, and the markdown as an article.

    ``kind`` becomes a class on the article (``kind-report``, ``kind-state``), so a per-kind
    rule can exist without a per-kind stylesheet.
    """
    body = _renderer(embed_images_from).render(markdown)
    head = html_mod.escape(title)
    sub = f'<p class="sub">{html_mod.escape(subtitle)}</p>' if subtitle else ""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{head}</title>\n"
        f"<style>{BASE_CSS}{DOC_CSS}{extra_css}</style>\n"
        "</head><body>\n"
        f'<header class="page-head"><div><strong>{head}</strong>{sub}</div>{THEME_BUTTONS}</header>\n'
        f'<article class="doc kind-{html_mod.escape(kind)}">\n{body}\n</article>\n'
        f"<script>{THEME_SCRIPT}</script>\n"
        "</body></html>\n"
    )


def write_html(md_path: Path, html_path: Optional[Path] = None, *, kind: str = "report",
               title: Optional[str] = None, subtitle: str = "") -> Path:
    """Render a markdown artefact to the HTML file beside it. Images resolve relative to the
    markdown file, which is where the run wrote them."""
    md_path = Path(md_path)
    html_path = Path(html_path) if html_path is not None else md_path.with_suffix(".html")
    text = md_path.read_text(encoding="utf-8")
    if title is None:
        m = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        title = m.group(1) if m else md_path.stem
    atomic.write_text(html_path, md_to_html(text, kind=kind, title=title, subtitle=subtitle,
                                            embed_images_from=md_path.parent))
    return html_path


def strip_regions(text: str) -> str:
    """The document with its region markers removed, for rendering: they are bookkeeping, and
    a reader of the page never needs to see them."""
    return re.sub(r"[ \t]*<!--\s*/?(?:generated|authored|ledger|rl)\b[^>]*-->[ \t]*\n?", "", text)


def render_artefact(md_path: Path, *, kind: str, subtitle: str = "") -> Path:
    """``write_html`` with the region markers taken out first."""
    md_path = Path(md_path)
    text = strip_regions(md_path.read_text(encoding="utf-8"))
    m = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    title = m.group(1) if m else md_path.stem
    html_path = md_path.with_suffix(".html")
    atomic.write_text(html_path, md_to_html(text, kind=kind, title=title, subtitle=subtitle,
                                            embed_images_from=md_path.parent))
    return html_path


def facts_in(text: str) -> Dict[str, Any]:
    """The ledger ids a document cites, for the lint that says a stated number must have one."""
    return {"ids": sorted(set(re.findall(r"\[(F\d{4})\]", text)))}
