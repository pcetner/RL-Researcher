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

from rl_researcher.regions import Region, split
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


def strip_regions(text: str) -> str:
    """The document with its region markers removed, for rendering: they are bookkeeping, and
    a reader of the page never needs to see them."""
    return re.sub(r"[ \t]*<!--\s*/?(?:generated|authored|ledger|rl)\b[^>]*-->[ \t]*\n?", "", text)


def facts_in(text: str) -> Dict[str, Any]:
    """The ledger ids a document cites, for the lint that says a stated number must have one."""
    return {"ids": sorted(set(re.findall(r"\[(F\d{4})\]", text)))}


#: A markdown task box as markdown-it leaves it: ``<li>[ ] go`` in a tight list, ``<li><p>[ ] go``
#: in a loose one. Commonmark has no task-list extension and this package does not enable one,
#: because the box has to survive as ``- [ ]`` in the file -- that text *is* the input `decide`
#: reads. So the box is put back at render time, here, and only for a served page.
_BOX = re.compile(r"(<li>\s*(?:<p>\s*)?)\[([ xX])\]\s*")


def _checkboxes(html: str) -> str:
    """Every task box in ``html`` as a real checkbox, numbered in document order.

    The number, not the label, is what the box is addressed by: a kind may write its own
    decision stub (``go`` / ``iterate`` / ``stop`` is only the run report's), labels carry
    markup and em-dashes, and two options could reasonably share a word. The nth box here is
    the nth ``- [ ]`` line of the region body, which is the same order
    :func:`artefacts.state.options` reads them in.
    """
    n = 0

    def one(m: "re.Match[str]") -> str:
        nonlocal n
        checked = " checked" if m.group(2).lower() == "x" else ""
        box = f'<input type="checkbox" class="tick" data-option="{n}"{checked}>'
        n += 1
        return m.group(1) + box + " "

    return _BOX.sub(one, html)


def _authored_section(md: Any, region: Region, run: str) -> str:
    """One authored region as an editable block: the rendered text, and the source behind it.

    Both are emitted, and the *source* is what gets submitted. The checkboxes and the prose view
    are a convenience over the textarea, not a second representation of it -- which is why
    nothing on the server has to understand the tick grammar, and why a region whose body is a
    kind's own stub, or free prose, or a table, edits exactly as well as a decision box does.
    """
    body = region.body
    return (
        f'<section class="authored" data-region="{html_mod.escape(region.arg, quote=True)}"'
        f' data-run="{html_mod.escape(run, quote=True)}">\n'
        f'<div class="authored-head"><span class="authored-name">'
        f'{html_mod.escape(region.arg)}</span>'
        f'<span class="authored-hint">yours to write; kept word for word when the run is '
        f'regenerated</span>'
        f'<button type="button" class="authored-edit">edit</button></div>\n'
        f'<div class="authored-view">\n{_checkboxes(md.render(body))}</div>\n'
        f'<textarea class="authored-src" spellcheck="true">'
        f'{html_mod.escape(body)}</textarea>\n'
        f'<div class="authored-actions"><button type="button" class="authored-save">save</button>'
        f'<button type="button" class="authored-revert">revert</button>'
        f'<span class="authored-said"></span></div>\n'
        f"</section>\n"
    )


def editable_article(text: str, *, kind: str, run: str = "",
                     embed_images_from: Optional[Path] = None) -> str:
    """The document as an article in which its authored regions are still addressable.

    The exported page strips every marker (:func:`strip_regions`), which is right for a page
    that is read and wrong for one that is worked in: with the markers gone the page cannot say
    where the decision region starts, so a ticked box is inert text and the only way to decide
    anything is to open the markdown in an editor and count lines. Here the generated and ledger
    regions are still stripped -- a reader never needs them -- and each authored region survives
    as a ``<section data-region=...>`` the page can read back and write to.

    Nothing in the export path calls this. That is the point: an exported page is byte for byte
    what it has always been, and there is no flag on the shared writer that could be set wrongly
    and put an input into an archived report.
    """
    md = _renderer(embed_images_from)
    out = []
    for part in split(text):
        if isinstance(part, Region):
            out.append(_authored_section(md, part, run) if part.kind == "authored"
                       else md.render(part.body))
        else:
            rest = strip_regions(part)
            if rest.strip():
                out.append(md.render(rest))
    return (f'<article class="doc kind-{html_mod.escape(kind)}">\n'
            + "\n".join(out) + "\n</article>\n")
