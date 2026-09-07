"""The blocks that are numbers in rows: the headline table, the units table, the ladder grid."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, Optional, Sequence, Tuple

from rl_researcher.blocks.base import (PANEL_CSS, TABLE_CSS, Block, esc, panel, rows_to_html,
                                       rows_to_md)


@dataclass(frozen=True)
class MetricRow:
    """One registered metric across the arms of a run, already reduced to text.

    ``cells`` are what the report prints (``0.108 ± 0.006``), ``tones`` say which cleared their
    bar. Both are computed upstream by ``artefacts.report``; this block does no arithmetic,
    because a number computed in two places is a number that will one day disagree with itself.
    """

    name: str
    cells: Sequence[str] = ()
    tones: Sequence[str] = ()
    target: str = ""
    baseline: str = ""
    why: str = ""


@dataclass(frozen=True)
class MetricsTable(Block):
    """Registered metrics down the side, arms across the top, the bar in its own column."""

    title: str = ""
    arms: Sequence[str] = field(default_factory=tuple)
    rows: Sequence[MetricRow] = field(default_factory=tuple)
    show_why: bool = False
    css: ClassVar[str] = TABLE_CSS + PANEL_CSS + """
  table.t td.mname { font-weight:600; white-space:normal }
  table.t td.why { color:var(--muted); white-space:normal; font-size:11.5px }
  table.t td.target { color:var(--muted); font-variant-numeric:tabular-nums }
"""

    @property
    def _has_baseline(self) -> bool:
        """The column appears only when some metric declares one. `> 1.000` says what the mark
        is judged by; the baseline says what the number is *against* — `chance = 1/54`,
        `copy-last`, `config floor 0.10` — and a table of targets alone drops that."""
        return any(r.baseline for r in self.rows)

    def _headers(self) -> Sequence[str]:
        mid = ["baseline", "target"] if self._has_baseline else ["target"]
        return ["metric", *mid, *self.arms]

    def _body(self) -> Sequence[Sequence[str]]:
        mid = (lambda r: [r.baseline or "—", r.target]) if self._has_baseline else \
              (lambda r: [r.target])
        return [[r.name, *mid(r), *r.cells] for r in self.rows]

    def md(self) -> str:
        table = rows_to_md(self._headers(), self._body())
        if not self.show_why:
            return table
        notes = [f"- **{r.name}** — {r.why}" for r in self.rows if r.why]
        return table + ("\n\n" + "\n".join(notes) if notes else "")

    def html(self) -> str:
        pad = ["", "", ""] if self._has_baseline else ["", ""]
        tones = [[*pad, *r.tones] for r in self.rows]
        table = rows_to_html(self._headers(), self._body(), tones=tones)
        notes = ""
        if self.show_why:
            items = "".join(f"<li><b>{esc(r.name)}</b> — {esc(r.why)}</li>"
                            for r in self.rows if r.why)
            notes = f"<ul>{items}</ul>" if items else ""
        return panel(self.title, table + notes)


@dataclass(frozen=True)
class UnitsTable(Block):
    """One row per unit: where it got to, how long it took, whether it resumed."""

    title: str = ""
    headers: Sequence[str] = ("unit", "steps", "seconds", "status")
    rows: Sequence[Sequence[Any]] = field(default_factory=tuple)
    tones: Optional[Sequence[Sequence[str]]] = None
    css: ClassVar[str] = TABLE_CSS + PANEL_CSS

    def md(self) -> str:
        return rows_to_md(self.headers, self.rows)

    def html(self) -> str:
        return panel(self.title, rows_to_html(self.headers, self.rows, tones=self.tones))


GRID_CSS = PANEL_CSS + """
  .grid { display:grid; gap:4px; font-size:11.5px }
  .grid .cell { padding:5px 8px; border-radius:6px; border:1px solid var(--line);
    background:var(--surface); white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
  .grid .cell.g-ok { border-color:color-mix(in srgb, var(--ok) 45%, var(--line)); color:var(--ok) }
  .grid .cell.g-crit { border-color:color-mix(in srgb, var(--crit) 45%, var(--line));
    color:var(--crit) }
  .grid .cell.g-warn { border-color:color-mix(in srgb, var(--warn) 45%, var(--line));
    color:var(--warn) }
  .grid .cell.g-muted { color:var(--muted) }
  .grid .colhead, .grid .rowhead { color:var(--muted); text-transform:uppercase;
    letter-spacing:0.05em; font-size:10px; border:0; background:none }
"""


@dataclass(frozen=True)
class Grid(Block):
    """A rectangle of small cells: arms against seeds, or a ladder of conditions.

    In markdown it is a table. In HTML it is a real grid, because forty cells of two words each
    read as a shape and a markdown table of them does not.
    """

    title: str = ""
    columns: Sequence[str] = field(default_factory=tuple)
    rows: Sequence[str] = field(default_factory=tuple)
    cells: Dict[Tuple[str, str], Tuple[str, str]] = field(default_factory=dict)  # (row,col)->(text,tone)
    css: ClassVar[str] = GRID_CSS

    def _text(self, row: str, col: str) -> str:
        return self.cells.get((row, col), ("", "muted"))[0] or "·"

    def _tone(self, row: str, col: str) -> str:
        return self.cells.get((row, col), ("", "muted"))[1]

    def md(self) -> str:
        return rows_to_md(["", *self.columns],
                          [[r, *[self._text(r, c) for c in self.columns]] for r in self.rows])

    def html(self) -> str:
        cols = len(self.columns) + 1
        out = [f'<div class="grid" style="grid-template-columns:repeat({cols},minmax(0,1fr))">']
        out.append('<div class="cell colhead"></div>')
        out += [f'<div class="cell colhead">{esc(c)}</div>' for c in self.columns]
        for r in self.rows:
            out.append(f'<div class="cell rowhead">{esc(r)}</div>')
            for c in self.columns:
                out.append(f'<div class="cell g-{esc(self._tone(r, c))}">{esc(self._text(r, c))}</div>')
        out.append("</div>")
        return panel(self.title, "".join(out))


LOG_CSS = PANEL_CSS + """
  .log { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px;
    line-height:1.55; max-height:19em; overflow:auto; white-space:pre-wrap; word-break:break-word }
  .log .l-crit { color:var(--crit) }
  .log .l-warn { color:var(--warn) }
  .log .l-ok { color:var(--ok) }
  .log .l-step { color:var(--muted) }
  .log .elided { color:var(--muted); font-style:italic }
"""


@dataclass(frozen=True)
class LogTail(Block):
    """The end of the run log, with the notable lines kept.

    A study logs a step line every ``log_every``, so a plain tail is a dozen near-identical
    lines and the one line that says why it stopped has already scrolled off. Thinning happens
    upstream, in the dashboard writer, which knows the kind's vocabulary; this block only draws
    what it is handed.
    """

    title: str = ""
    lines: Sequence[Tuple[str, str]] = field(default_factory=tuple)     # (tone, text)
    css: ClassVar[str] = LOG_CSS

    def md(self) -> str:
        if not self.lines:
            return ""
        body = "\n".join(text for _tone, text in self.lines)
        return f"```\n{body}\n```"

    def html(self) -> str:
        if not self.lines:
            return ""
        body = "\n".join(
            f'<span class="l-{esc(tone)}">{esc(text)}</span>' if tone else esc(text)
            for tone, text in self.lines)
        return panel(self.title, f'<div class="log">{body}</div>')
