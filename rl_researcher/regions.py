"""Regions: the parts of a document a machine owns and the parts a person owns.

Every artefact is one file with both in it. The numbers, the tables and the provenance are
regenerated from disk on every run; the reading and the decision are written by hand and must
survive that regeneration untouched, byte for byte. Mixing them in one file is what makes the
document worth reading, and marking them is what makes regenerating it safe.

A region is an HTML comment pair, so it is invisible in the rendered page::

    <!-- generated: headline -->
    | metric | ctrl | var5 |
    <!-- /generated -->

    <!-- authored: reading -->
    The variance hinge is doing the work; the covariance term is not. [F0142]
    <!-- /authored -->

    <!-- ledger: touches=D4 -->
    2026-09-06 · Study 4 · action_sensitivity_ratio 1.273 ± 0.027 (n=3) ✓ [F0110]
    <!-- /ledger -->

Three kinds, one grammar: ``<!-- <kind>: <arg> -->`` opens and ``<!-- /<kind> -->`` closes.
``arg`` is a bare name (``headline``) or ``key=value`` pairs (``touches=D4 metric=r2``).

Text outside every region is never touched by anything here. That matters most in
``docs/PLAN.md``, which is mostly prose a person wrote and which a sync must not reflow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Union

KINDS = ("generated", "authored", "ledger")

_OPEN = re.compile(r"<!--\s*(?P<kind>generated|authored|ledger)\s*:\s*(?P<arg>[^>]*?)\s*-->")
_CLOSE_FOR = {k: re.compile(rf"<!--\s*/\s*{k}\s*(?::[^>]*)?-->") for k in KINDS}


class RegionError(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    """One marked span. ``body`` excludes the markers and their surrounding newlines."""

    kind: str
    arg: str
    body: str
    start: int = 0      # offset of the opening marker in the source
    end: int = 0        # offset just past the closing marker

    @property
    def name(self) -> str:
        """The bare name, for a region marked ``<!-- generated: headline -->``."""
        return self.arg.split("=", 1)[0] if "=" not in self.arg else ""

    @property
    def attrs(self) -> Dict[str, str]:
        """``key=value`` pairs, for a region marked ``<!-- ledger: touches=D4 -->``."""
        out: Dict[str, str] = {}
        for tok in self.arg.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    @property
    def key(self) -> "tuple[str, str]":
        return (self.kind, self.arg)

    def rendered(self) -> str:
        return open_marker(self.kind, self.arg) + "\n" + self.body.strip("\n") + "\n" + close_marker(self.kind)


def open_marker(kind: str, arg: str) -> str:
    return f"<!-- {kind}: {arg} -->"


def close_marker(kind: str) -> str:
    return f"<!-- /{kind} -->"


def find(text: str) -> List[Region]:
    """Every region in ``text``, in order. An unclosed region is an error, not a warning: a
    document that half-marks a region would have half of it silently overwritten."""
    out: List[Region] = []
    pos = 0
    while True:
        m = _OPEN.search(text, pos)
        if m is None:
            return out
        kind, arg = m.group("kind"), m.group("arg")
        c = _CLOSE_FOR[kind].search(text, m.end())
        if c is None:
            line = text.count("\n", 0, m.start()) + 1
            raise RegionError(f"line {line}: <!-- {kind}: {arg} --> is never closed "
                              f"(expected {close_marker(kind)})")
        out.append(Region(kind=kind, arg=arg, body=text[m.end():c.start()].strip("\n"),
                          start=m.start(), end=c.end()))
        pos = c.end()


Part = Union[str, Region]


def split(text: str) -> List[Part]:
    """``text`` as an alternating list of plain strings and regions, so a caller can rebuild it
    with :func:`join` after changing only the regions it owns."""
    parts: List[Part] = []
    pos = 0
    for r in find(text):
        if r.start > pos:
            parts.append(text[pos:r.start])
        parts.append(r)
        pos = r.end
    if pos < len(text):
        parts.append(text[pos:])
    return parts


def join(parts: Sequence[Part]) -> str:
    return "".join(p if isinstance(p, str) else p.rendered() for p in parts)


def body_of(text: str, kind: str, arg: str) -> Optional[str]:
    for r in find(text):
        if r.kind == kind and r.arg == arg:
            return r.body
    return None


def set_region(text: str, kind: str, arg: str, body: str, *, append_if_missing: bool = False) -> str:
    """Replace the body of one region. With ``append_if_missing`` a region that is not there
    yet is added at the end; without it, a missing region is an error, because silently
    dropping a table a caller asked to write is how a page ships with a section missing."""
    parts = split(text)
    for i, p in enumerate(parts):
        if isinstance(p, Region) and p.kind == kind and p.arg == arg:
            parts[i] = Region(kind=kind, arg=arg, body=body)
            return join(parts)
    if not append_if_missing:
        raise RegionError(f"no <!-- {kind}: {arg} --> region in this document")
    tail = "" if text.endswith("\n") else "\n"
    return text + tail + "\n" + Region(kind=kind, arg=arg, body=body).rendered() + "\n"


def preserve_authored(old: str, new: str) -> str:
    """``new``, with every authored region's body taken from ``old`` where ``old`` has one.

    This is the whole safety property of regeneration: a report rewritten after a re-plot keeps
    the reading and the decision exactly as they were typed, including their whitespace. An
    authored region that is new in ``new`` keeps whatever ``new`` gives it (usually the empty
    stub), and one that has disappeared from ``new`` is not resurrected.
    """
    kept = {r.arg: r.body for r in find(old) if r.kind == "authored"}
    parts = split(new)
    for i, p in enumerate(parts):
        if isinstance(p, Region) and p.kind == "authored" and p.arg in kept:
            parts[i] = Region(kind="authored", arg=p.arg, body=kept[p.arg])
    return join(parts)


@dataclass
class Document:
    """A markdown artefact as a header comment plus ordered H2 sections.

    ``header`` is the one-line ``<!-- rl: ... -->`` stamp that says what the file is, which run
    it belongs to and when it was generated. It is deliberately the first line of every
    artefact: a page whose provenance is at the bottom gets read without it.
    """

    kind: str
    title: str
    header: Dict[str, str] = field(default_factory=dict)
    sections: List["tuple[str, Region]"] = field(default_factory=list)

    def render(self) -> str:
        stamp = " ".join(f"{k}={v}" for k, v in self.header.items())
        out = [f"<!-- rl: kind={self.kind} {stamp} -->".replace("  ", " "), f"# {self.title}", ""]
        for heading, region in self.sections:
            out += [f"## {heading}", "", region.rendered(), ""]
        return "\n".join(out).rstrip("\n") + "\n"


def header_of(text: str) -> Dict[str, str]:
    """The ``<!-- rl: ... -->`` stamp as a dict, or ``{}`` if the file has none."""
    m = re.search(r"<!--\s*rl:\s*(?P<body>[^>]*?)\s*-->", text)
    if m is None:
        return {}
    out: Dict[str, str] = {}
    for tok in m.group("body").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def headings(text: str) -> List[str]:
    """The H2 headings, in order: what check C12 compares against the kind's layout."""
    return [m.group(1).strip() for m in re.finditer(r"(?m)^##\s+(.+?)\s*$", text)]
