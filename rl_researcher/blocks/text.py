"""The blocks that are mostly words: banners, prose, provenance, notices, the decision stub."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional, Sequence, Tuple

from rl_researcher.blocks.base import (PANEL_CSS, TABLE_CSS, Block, esc, panel, rows_to_html,
                                       rows_to_md)


@dataclass(frozen=True)
class Prose(Block):
    """A paragraph or several. The one block whose content is words rather than values."""

    text: str = ""
    css: ClassVar[str] = ""

    def md(self) -> str:
        return self.text.strip()

    def html(self) -> str:
        paras = [p.strip() for p in self.text.strip().split("\n\n") if p.strip()]
        return "".join(f"<p>{esc(p)}</p>" for p in paras)


BANNER_CSS = """
  .banner { border-left:3px solid var(--warn); background:color-mix(in srgb, var(--warn) 8%,
    transparent); padding:8px 12px; border-radius:0 8px 8px 0; margin:0 0 14px; font-size:13px }
  .banner.b-crit { border-left-color:var(--crit);
    background:color-mix(in srgb, var(--crit) 8%, transparent) }
  .banner.b-accent { border-left-color:var(--accent);
    background:color-mix(in srgb, var(--accent) 8%, transparent) }
  .banner b { display:block; margin-bottom:2px }
"""


@dataclass(frozen=True)
class Banner(Block):
    """A statement about the whole document that a reader must not be able to skip.

    Three of them exist and each is load-bearing: a diagnosis was written *after* seeing the
    result and cannot be read as a prediction; a run is incomplete and its numbers are about a
    smaller budget than registered; a screening run has one seed and settles nothing.
    """

    label: str = ""
    text: str = ""
    tone: str = "warn"
    css: ClassVar[str] = BANNER_CSS

    def md(self) -> str:
        return f"> **{self.label}** {self.text}".strip()

    def html(self) -> str:
        cls = "banner" if self.tone == "warn" else f"banner b-{esc(self.tone)}"
        return f'<div class="{cls}"><b>{esc(self.label)}</b>{esc(self.text)}</div>'


@dataclass(frozen=True)
class KV(Block):
    """Label-and-value pairs. Provenance is the one that matters: what a number was measured
    with, on what data, at what commit."""

    title: str = ""
    pairs: Sequence[Tuple[str, str]] = field(default_factory=tuple)
    css: ClassVar[str] = PANEL_CSS + """
  .kv { display:grid; grid-template-columns:auto 1fr; gap:3px 14px; font-size:12.5px }
  .kv dt { color:var(--muted) }
  .kv dd { margin:0; font-variant-numeric:tabular-nums }
"""

    def md(self) -> str:
        if not self.pairs:
            return ""
        lines = [f"**{k}.** {v}" for k, v in self.pairs]
        return "\n".join(f"- {line}" for line in lines)

    def html(self) -> str:
        inner = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in self.pairs)
        return panel(self.title, f'<dl class="kv">{inner}</dl>')


Provenance = KV


NOTICE_CSS = """
  .notices { display:flex; flex-direction:column; gap:6px; font-size:12.5px }
  .notice { display:flex; gap:10px; align-items:baseline; padding:6px 10px; border-radius:8px;
    border:1px solid var(--line); background:var(--surface) }
  .notice .who { font-weight:600; white-space:nowrap }
  .notice.n-crit { border-color:color-mix(in srgb, var(--crit) 40%, var(--line)) }
  .notice.n-crit .who { color:var(--crit) }
  .notice.n-warn .who { color:var(--warn) }
  .notice .msg { color:var(--muted) }
"""


@dataclass(frozen=True)
class Notices(Block):
    """Things that went wrong, named: which unit, what the error said, where to resume from."""

    title: str = ""
    items: Sequence[Tuple[str, str, str]] = field(default_factory=tuple)   # (tone, who, message)
    css: ClassVar[str] = NOTICE_CSS + PANEL_CSS

    def md(self) -> str:
        if not self.items:
            return ""
        return "\n".join(f"- **{who}** — {msg}" for _tone, who, msg in self.items)

    def html(self) -> str:
        if not self.items:
            return ""
        rows = "".join(
            f'<div class="notice n-{esc(tone)}"><span class="who">{esc(who)}</span>'
            f'<span class="msg">{esc(msg)}</span></div>'
            for tone, who, msg in self.items)
        return panel(self.title, f'<div class="notices">{rows}</div>')


STUB_CSS = PANEL_CSS + """
  .stub { font-size:13px }
  .stub label { display:block; padding:3px 0 }
  .stub .box { font-family:ui-monospace,monospace; color:var(--muted); margin-right:6px }
  .stub .box.on { color:var(--ok); font-weight:700 }
  .stub .notes { margin-top:10px; padding-top:10px; border-top:1px solid var(--line);
    color:var(--muted); white-space:pre-wrap }
"""


@dataclass(frozen=True)
class Stub(Block):
    """The decision checklist. A ticked box is an input the state page reads, which is why it
    lives in the file rather than in a chat: the next session can see it."""

    options: Sequence[Tuple[str, str]] = field(default_factory=tuple)   # (label, what it means)
    ticked: Sequence[str] = field(default_factory=tuple)
    notes: str = ""
    css: ClassVar[str] = STUB_CSS

    def md(self) -> str:
        lines = []
        for label, meaning in self.options:
            mark = "x" if label in self.ticked else " "
            lines.append(f"- [{mark}] **{label}**" + (f" — {meaning}" if meaning else ""))
        lines += ["", "Reviewer notes:", "", self.notes or "_(write here)_"]
        return "\n".join(lines)

    def html(self) -> str:
        out = []
        for label, meaning in self.options:
            on = " on" if label in self.ticked else ""
            box = "[x]" if label in self.ticked else "[ ]"
            out.append(f'<label><span class="box{on}">{box}</span><b>{esc(label)}</b>'
                       + (f" — {esc(meaning)}" if meaning else "") + "</label>")
        notes = f'<div class="notes">{esc(self.notes)}</div>' if self.notes else ""
        return panel("Decision (human)", f'<div class="stub">{"".join(out)}{notes}</div>')


@dataclass(frozen=True)
class Claims(Block):
    """The ledger ids this document wrote, so a reader can follow any number back to its run."""

    title: str = "Ledger"
    rows: Sequence[Tuple[str, str, str]] = field(default_factory=tuple)   # (id, what, value)
    css: ClassVar[str] = TABLE_CSS + PANEL_CSS

    def md(self) -> str:
        return rows_to_md(["id", "claim", "value"], self.rows)

    def html(self) -> str:
        return panel(self.title, rows_to_html(["id", "claim", "value"], self.rows))


FOOTER_CSS = """
  .foot { color:var(--muted); font-size:11.5px; margin-top:22px; padding-top:10px;
    border-top:1px solid var(--line) }
  .foot code { background:var(--code); padding:1px 5px; border-radius:3px }
"""


@dataclass(frozen=True)
class Footer(Block):
    """What produced this page and the command that produces it again.

    A page nobody can regenerate is a page nobody can trust, and the way that goes wrong is
    never a decision to hide it: it is that the command was in someone's shell history.
    """

    text: str = ""
    command: str = ""
    css: ClassVar[str] = FOOTER_CSS

    def md(self) -> str:
        cmd = f" Regenerate with `{self.command}`." if self.command else ""
        return f"---\n\n_{self.text}{cmd}_"

    def html(self) -> str:
        cmd = f" Regenerate with <code>{esc(self.command)}</code>." if self.command else ""
        return f'<div class="foot">{esc(self.text)}{cmd}</div>'


@dataclass(frozen=True)
class Figure(Block):
    """One image with its caption. The caption says what to look for, not what it is."""

    src: str = ""
    caption: str = ""
    alt: str = ""
    css: ClassVar[str] = """
  figure.fig { margin:0 0 16px }
  figure.fig img { max-width:100%; border:1px solid var(--line); border-radius:8px;
    background:var(--surface) }
  figure.fig figcaption { color:var(--muted); font-size:12px; margin-top:6px }
"""

    def md(self) -> str:
        alt = self.alt or self.caption or "figure"
        cap = f"\n\n_{self.caption}_" if self.caption else ""
        return f"![{alt}]({self.src}){cap}"

    def html(self) -> str:
        cap = f"<figcaption>{esc(self.caption)}</figcaption>" if self.caption else ""
        return (f'<figure class="fig"><img src="{esc(self.src)}" '
                f'alt="{esc(self.alt or self.caption)}">{cap}</figure>')


@dataclass(frozen=True)
class Gallery(Block):
    """Per-unit evidence, many small images.

    By default markdown gets a table of links, because a markdown file with forty inlined
    images is not a file anyone opens twice. ``inline`` overrides that for a kind whose
    per-unit evidence *is* the argument -- a study's rollout strips are the one thing a
    reviewer can check against their own eyes -- and inlining them in the markdown is also what
    puts them in the HTML beside it, which is rendered from that markdown and embeds what it
    finds. Links alone leave the reviewer's page self-contained for the figures and not for the
    evidence, which is the half a reviewer actually argues with.
    """

    title: str = ""
    items: Sequence[Tuple[str, str]] = field(default_factory=tuple)      # (label, src)
    note: str = ""
    inline: bool = False
    css: ClassVar[str] = PANEL_CSS + """
  .gal { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:12px }
  .gal figure { margin:0 }
  .gal img { width:100%; border:1px solid var(--line); border-radius:6px }
  .gal figcaption { color:var(--muted); font-size:11.5px; margin-top:4px }
"""

    def md(self) -> str:
        if not self.items:
            return ""
        note = f"{self.note}\n\n" if self.note else ""
        if self.inline:
            return note + "\n\n".join(f"![{label}]({src})" for label, src in self.items)
        return note + rows_to_md(["unit", "evidence"],
                                 [(label, f"[{src.rsplit('/', 1)[-1]}]({src})") for label, src in self.items])

    def html(self) -> str:
        if not self.items:
            return ""
        figs = "".join(f'<figure><img src="{esc(src)}" alt="{esc(label)}">'
                       f"<figcaption>{esc(label)}</figcaption></figure>"
                       for label, src in self.items)
        return panel(self.title, f'<div class="gal">{figs}</div>')


@dataclass(frozen=True)
class Header(Block):
    """The run's identity line: what it is, what state it is in, how fresh the heartbeat is.

    Only used on pages that are not already titled by :class:`~rl_researcher.blocks.base.Page`;
    a dashboard's own header is the page's.
    """

    title: str = ""
    state: str = ""
    tone: str = "muted"
    heartbeat: Optional[str] = None
    css: ClassVar[str] = """
  .rhead { display:flex; gap:10px; align-items:baseline; margin:0 0 12px; flex-wrap:wrap }
  .rhead .age { color:var(--muted); font-size:12px }
"""

    def md(self) -> str:
        bits = [f"**{self.title}**", self.state]
        if self.heartbeat:
            bits.append(f"heartbeat {self.heartbeat} ago")
        return " · ".join(b for b in bits if b)

    def html(self) -> str:
        age = f'<span class="age">heartbeat {esc(self.heartbeat)} ago</span>' if self.heartbeat else ""
        badge = f'<span class="chip t-{esc(self.tone)}">{esc(self.state)}</span>' if self.state else ""
        return f'<div class="rhead"><b>{esc(self.title)}</b>{badge}{age}</div>'
