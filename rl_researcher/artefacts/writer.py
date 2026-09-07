"""Writing an artefact: compose blocks into the kind's sections, keeping what a person wrote.

One function does the whole job. It builds the file from the kind's layout, puts each section's
blocks inside that section's region, and — this is the part that matters — takes every authored
region's body from the file already on disk, byte for byte. So a re-plot after a styling change
rewrites every table and every figure and does not touch a word of the reading or the decision.

The HTML beside it is rendered from the same markdown, so the two cannot drift: there is no
second code path that could format a number differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from rl_researcher import atomic
from rl_researcher.artefacts.layouts import Layout, assert_layout, layout_for
from rl_researcher.blocks import Block
from rl_researcher.regions import Region, preserve_authored
from rl_researcher.render import md_to_html, strip_regions
from rl_researcher.units import stamp_now


@dataclass
class Artefact:
    """A document being written: its kind, its title, its stamp, and blocks per section."""

    kind: str
    title: str
    header: Dict[str, str] = field(default_factory=dict)
    sections: Dict[str, List[Block]] = field(default_factory=dict)
    #: Sections whose body is plain markdown rather than blocks (a generated prose paragraph).
    text: Dict[str, str] = field(default_factory=dict)
    #: What an authored section says when the file does not exist yet.
    stubs: Dict[str, str] = field(default_factory=dict)

    @property
    def layout(self) -> Layout:
        return layout_for(self.kind)

    def add(self, region: str, *blocks: Block) -> "Artefact":
        self.sections.setdefault(region, []).extend(b for b in blocks if b is not None)
        return self

    def say(self, region: str, markdown: str) -> "Artefact":
        self.text[region] = markdown
        return self

    def stub(self, region: str, markdown: str) -> "Artefact":
        self.stubs[region] = markdown
        return self

    # ------------------------------------------------------------------ rendering

    def body_for(self, region: str, owner: str) -> str:
        if owner == "authored":
            return self.stubs.get(region, "_(write here)_")
        parts = [self.text[region]] if region in self.text else []
        parts += [b.md().strip("\n") for b in self.sections.get(region, [])]
        return "\n\n".join(p for p in parts if p.strip())

    def md(self) -> str:
        stamp = " ".join(f"{k}={v}" for k, v in self.header.items() if v)
        out = [f"<!-- rl: kind={self.kind} {stamp} -->".replace("  ", " "), f"# {self.title}", ""]
        for s in self.layout.sections:
            body = self.body_for(s.region, s.owner)
            out += [f"## {s.heading}", "",
                    Region(kind=s.owner, arg=s.region, body=body).rendered(), ""]
        return "\n".join(out).rstrip("\n") + "\n"

    def blocks(self) -> Sequence[Block]:
        out: List[Block] = []
        for s in self.layout.sections:
            out += self.sections.get(s.region, [])
        return out


def adopt_legacy(old: str, new: str, layout: Layout) -> str:
    """Take authored bodies from a file written before regions existed.

    Every report already on disk has its reading and its decision under a plain ``## Reading``
    or ``## Decision (human)`` heading, with no markers around them. Regenerating such a file
    through :func:`preserve_authored` alone would find no authored region to carry across and
    would replace hours of a person's writing with an empty stub. So the first regeneration of
    an old file reads those sections by heading and adopts them; from then on the markers are
    there and the ordinary path applies.
    """
    from rl_researcher.artefacts.state import section
    from rl_researcher.regions import find, set_region

    have = {r.arg for r in find(old) if r.kind == "authored"}
    for s in layout.sections:
        if s.owner != "authored" or s.region in have:
            continue
        body = section(old, s.heading.split(" (")[0])
        if body and body.strip():
            new = set_region(new, "authored", s.region, body.strip("\n"))
    return new


def write(artefact: Artefact, md_path: Path, *, html: bool = True,
          subtitle: str = "") -> Path:
    """Write the artefact, preserving whatever a person had already written in it.

    Returns the markdown path. The HTML beside it is rendered from the same text with the
    region markers stripped, and figures inlined so the page opens with no network.
    """
    md_path = Path(md_path)
    fresh = artefact.md()
    if md_path.is_file():
        old = md_path.read_text(encoding="utf-8")
        fresh = adopt_legacy(old, preserve_authored(old, fresh), artefact.layout)
    assert_layout(fresh, artefact.kind)
    atomic.write_text(md_path, fresh)
    if html:
        atomic.write_text(
            md_path.with_suffix(".html"),
            md_to_html(strip_regions(fresh), kind=artefact.kind, title=artefact.title,
                       subtitle=subtitle or f"generated {stamp_now()}",
                       embed_images_from=md_path.parent))
    return md_path


def stamp(*, run: str = "", fingerprint: str = "", commit: str = "", data: str = "",
          generated: Optional[str] = None) -> Dict[str, str]:
    """The header comment every artefact opens with: which run, which registration, which code,
    which data, and when it was last written."""
    return {"run": run, "fingerprint": fingerprint, "commit": commit, "data": data,
            "generated": generated or stamp_now()}
