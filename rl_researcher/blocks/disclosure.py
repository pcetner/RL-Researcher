"""Optional detail stays readable in Markdown and expands for HTML printing."""

from dataclasses import dataclass, field
from typing import ClassVar, Sequence

from rl_researcher.blocks.base import Block, esc
from rl_researcher.render import DOC_CSS, _renderer
from rl_researcher import charts
from rl_researcher.blocks.live import LiveUnit


@dataclass(frozen=True)
class Disclosure(Block):
    title: str = "Details"
    blocks: Sequence[Block] = field(default_factory=tuple)
    opened: bool = False
    css: ClassVar[str] = """
      .disclosure { border:1px solid var(--line); border-radius:8px; margin:14px 0; padding:12px }
      .disclosure > summary { cursor:pointer; font-size:13px; font-weight:600 }
      .disclosure > summary:focus-visible { outline:2px solid var(--accent) }
      .disclosure[open] > summary { margin-bottom:12px }
      @media print { .disclosure { break-inside:auto } }
    """

    def __post_init__(self) -> None:
        object.__setattr__(self, "css", self.css + "".join(dict.fromkeys(b.css for b in self.blocks)))

    def md(self) -> str:
        return "\n\n".join(b.md() for b in self.blocks)

    def html(self) -> str:
        body = "".join(b.html() for b in self.blocks)
        if not body:
            return ""
        return (f'<details class="disclosure" data-disclosure="{esc(self.title)}"'
                + (" open" if self.opened else "") + f'><summary>{esc(self.title)}</summary>'
                + body + "</details>")


@dataclass(frozen=True)
class ResearchOutcome(Block):
    text: str = ""
    css: ClassVar[str] = DOC_CSS

    def md(self) -> str:
        return self.text

    def html(self) -> str:
        return '<section class="doc">' + _renderer(None).render(self.text) + '</section>'


@dataclass(frozen=True)
class LiveTrends(Block):
    """A bounded preview of active curves. The Units view retains every unit."""

    units: Sequence[LiveUnit] = field(default_factory=tuple)
    css: ClassVar[str] = charts.CHART_CSS + """
      .trends { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px }
      .trend { margin:0; padding:14px; border:1px solid var(--line); border-radius:8px; min-width:0 }
      .trend figcaption { font-size:13px; margin-bottom:10px }
      .trend .spark { width:100%; height:100px }
      .trend-note { color:var(--muted); font-size:12px }
    """

    def md(self) -> str:
        return "\n".join(f"- {u.arm}/seed{u.seed}: {u.step}/{u.max_steps} steps"
                         for u in self.units[:3])

    def html(self) -> str:
        cards = []
        for unit in self.units[:3]:
            curve = next((c for c in unit.curves if c[1]), None)
            if curve is None:
                continue
            title, series, _last, floor = curve
            svg = charts.curve(list(series), colour=unit.colour, label=title,
                               steps=unit.step, floor=floor)
            cards.append(f'<figure class="trend"><figcaption>{esc(unit.arm)} · seed {unit.seed}'
                         f' · {esc(title)}</figcaption>{svg}<p class="trend-note">'
                         f'{unit.step:,}/{unit.max_steps:,} steps</p></figure>')
        if not cards:
            return ""
        note = f'<p class="trend-note">Showing {len(cards)} of {len(self.units)} active units.</p>'
        return '<h2>Live trends</h2><div class="trends">' + "".join(cards) + '</div>' + note
