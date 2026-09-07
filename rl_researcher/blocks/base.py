"""What a block is, and the page that composes them.

A block is one piece of an artefact — a headline table, a scorecard, a log tail — that knows how
to render itself twice: as markdown for the file a person reads and diffs, and as HTML for the
page they open. It holds plain values only. No spec, no summary dict, no ``Path`` crosses into
one, because a block that can reach into a spec is a block that can quietly start deciding
things, and the whole point of composing pages out of these is that the deciding happened
upstream, where it is tested.

Three rules, each enforced by a test:

* every CSS class a block emits is one it declares, or one the base stylesheet defines. A page
  ships the CSS of exactly the blocks it used, so an undeclared class renders unstyled.
* markdown output contains no HTML. A markdown table is a markdown table; if a thing cannot be
  said in markdown it belongs in the HTML rendering only, and the markdown says so in words.
* nothing is fetched. No block emits an ``http`` URL for the browser to load.
"""

from __future__ import annotations

import html as html_mod
import math
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence, Tuple

from rl_researcher.style import BASE_CSS, THEME_BUTTONS, THEME_SCRIPT

#: Classes the base stylesheet defines, which any block may use without declaring them.
BASE_CLASSES = frozenset({
    "wrap", "head", "spacer", "chip", "theme", "t-crit", "t-ok", "t-warn", "t-accent", "t-muted",
})

TONES = ("ok", "warn", "crit", "accent", "muted")


def esc(text: Any) -> str:
    return html_mod.escape(str(text), quote=True)


def md_cell(text: Any) -> str:
    """A value safe to put in a markdown table cell: no pipes, no newlines."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def fmt_number(v: Any, places: int = 3) -> str:
    """A number as a report says it: ``n/a`` for missing, ``diverged`` for infinite."""
    if v is None:
        return "n/a"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(f):
        return "n/a"
    if math.isinf(f):
        return "diverged"
    return f"{f:.{places}f}"


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = float(seconds)
    if s < 90:
        return f"{s:.0f}s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"


@dataclass(frozen=True)
class Block:
    """The base. A subclass sets ``css`` and implements ``md`` and ``html``."""

    css: ClassVar[str] = ""

    def declares(self) -> Sequence[str]:
        """The CSS classes this block's own stylesheet defines."""
        return sorted(set(re.findall(r"\.([a-zA-Z][\w-]*)", self.css)))

    def md(self) -> str:
        raise NotImplementedError

    def html(self) -> str:
        raise NotImplementedError

    # ------------------------------------------------------------------ checking

    def emitted_classes(self) -> Sequence[str]:
        """Every class this block actually puts in its HTML."""
        out: set = set()
        for attr in re.findall(r'class="([^"]*)"', self.html()):
            out.update(attr.split())
        return sorted(out)

    def undeclared(self) -> Sequence[str]:
        allowed = set(self.declares()) | BASE_CLASSES
        return sorted(c for c in self.emitted_classes() if c not in allowed)


def tone_of(passed: Optional[bool]) -> str:
    if passed is None:
        return "muted"
    return "ok" if passed else "crit"


def chip(text: str, tone: str = "muted") -> str:
    return f'<span class="chip t-{esc(tone)}">{esc(text)}</span>'


@dataclass(frozen=True)
class Page:
    """A rendered artefact: the base stylesheet plus exactly the blocks used.

    ``refresh`` is the seconds between reloads for a page that is still changing. A finished run
    passes ``None`` and the page stops reloading itself, which is the difference between a
    dashboard that is live and one that is a record.
    """

    kind: str
    title: str
    blocks: Sequence[Block] = field(default_factory=tuple)
    subtitle: str = ""
    chip_text: str = ""
    chip_tone: str = "muted"
    refresh: Optional[int] = None
    extra_css: str = ""
    #: SVG ``<defs>`` the page's blocks refer to by id — the per-seed fill patterns a marker
    #: uses. They are defined once for the document rather than repeated in every drawing.
    defs: str = ""

    def css(self) -> str:
        seen: List[str] = []
        for b in self.blocks:
            if b.css and b.css not in seen:
                seen.append(b.css)
        return BASE_CSS + "".join(seen) + self.extra_css

    def md(self) -> str:
        parts = [f"# {self.title}", ""]
        if self.subtitle:
            parts += [f"_{self.subtitle}_", ""]
        for b in self.blocks:
            text = b.md().strip("\n")
            if text:
                parts += [text, ""]
        return "\n".join(parts).rstrip("\n") + "\n"

    def html(self) -> str:
        meta = f'<meta http-equiv="refresh" content="{int(self.refresh)}">\n' if self.refresh else ""
        badge = chip(self.chip_text, self.chip_tone) if self.chip_text else ""
        sub = f'<div class="sub">{esc(self.subtitle)}</div>' if self.subtitle else ""
        body = "\n".join(b.html() for b in self.blocks)
        return (
            "<!doctype html>\n"
            '<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"{meta}<title>{esc(self.title)}</title>\n"
            f"<style>{self.css()}{PAGE_CSS}</style>\n"
            "</head><body>\n"
            f"{self.defs}"
            f'<div class="wrap">\n'
            f'<div class="head"><h1>{esc(self.title)}</h1>{badge}{sub}'
            f'<span class="spacer"></span>{THEME_BUTTONS}</div>\n'
            f"{body}\n</div>\n"
            f"<script>{THEME_SCRIPT}</script>\n"
            "</body></html>\n"
        )

    def undeclared(self) -> Sequence[str]:
        out: set = set()
        for b in self.blocks:
            out.update(b.undeclared())
        return sorted(out)


PAGE_CSS = """
  .wrap { max-width:1180px }
  .head .sub { color:var(--muted); font-size:12px }
"""


def rows_to_md(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """A markdown table. Empty rows render as a sentence, never as a headerless table."""
    body = [list(r) for r in rows]
    if not body:
        return "_(nothing to show)_"
    out = ["| " + " | ".join(md_cell(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in body:
        out.append("| " + " | ".join(md_cell(c) for c in r) + " |")
    return "\n".join(out)


def rows_to_html(headers: Sequence[str], rows: Iterable[Sequence[Any]], *,
                 cls: str = "t", tones: Optional[Sequence[Sequence[str]]] = None) -> str:
    body = [list(r) for r in rows]
    if not body:
        return f'<div class="{esc(cls)} empty">nothing to show</div>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    lines = [f'<div class="scroll"><table class="{esc(cls)}"><thead><tr>{head}</tr></thead><tbody>']
    for i, r in enumerate(body):
        cells = []
        for j, c in enumerate(r):
            tone = tones[i][j] if tones and i < len(tones) and j < len(tones[i]) else ""
            attr = f' class="v-{esc(tone)}"' if tone else ""
            cells.append(f"<td{attr}>{esc(c)}</td>")
        lines.append("<tr>" + "".join(cells) + "</tr>")
    lines.append("</tbody></table></div>")
    return "\n".join(lines)


TABLE_CSS = """
  .scroll { overflow-x:auto }
  table.t { border-collapse:collapse; font-size:12.5px; width:100% }
  table.t th, table.t td { border-bottom:1px solid var(--line); padding:5px 10px; text-align:left;
    white-space:nowrap }
  table.t th { color:var(--muted); font-weight:600; font-size:11px; text-transform:uppercase;
    letter-spacing:0.05em }
  table.t td.v-ok { color:var(--ok) }
  table.t td.v-crit { color:var(--crit) }
  table.t td.v-warn { color:var(--warn) }
  table.t td.v-muted { color:var(--muted) }
  .t.empty { color:var(--muted); font-size:12.5px; padding:6px 0 }
"""


PANEL_CSS = """
  .panel { background:var(--surface); border:1px solid var(--line); border-radius:10px;
    margin:0 0 16px; overflow:hidden }
  .panel > h2 { font-size:11px; text-transform:uppercase; letter-spacing:0.07em;
    color:var(--muted); margin:0; padding:10px 14px; border-bottom:1px solid var(--line);
    font-weight:600 }
  .panel > .body { padding:12px 14px }
  .panel > .body > :first-child { margin-top:0 }
"""


def panel(title: str, inner: str) -> str:
    head = f"<h2>{esc(title)}</h2>" if title else ""
    return f'<section class="panel">{head}<div class="body">{inner}</div></section>'


def numbers_in(text: str) -> Tuple[str, ...]:
    """Every number in a string, for the test that says a block's HTML states everything its
    markdown states. A page that drops a number when it is rendered is a page that disagrees
    with the file beside it."""
    return tuple(re.findall(r"-?\d+(?:\.\d+)?", text))


def as_dict(block: Block) -> Dict[str, Any]:
    return {k: v for k, v in block.__dict__.items() if k != "css"}
