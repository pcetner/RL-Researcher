"""The block inventory: every piece an artefact is built from.

Import from here, not from the submodules. A page is a list of these plus a title, and the
stylesheet it ships is the union of the CSS of exactly the blocks in that list.
"""

from rl_researcher.blocks.base import (BASE_CLASSES, Block, Page, chip, esc, fmt_duration,
                                       fmt_number, numbers_in, panel, rows_to_html, rows_to_md,
                                       tone_of)
from rl_researcher.blocks.tables import Grid, LogTail, MetricRow, MetricsTable, UnitsTable
from rl_researcher.blocks.text import (Banner, Claims, Figure, Footer, Gallery, Header, KV,
                                       Notices, Prose, Provenance, Stub)
from rl_researcher.blocks.viz import Curve, Curves, Lane, Mark, Progress, Scorecard, Tiles

#: Every concrete block, for the tests that hold all of them to the same rules.
ALL_BLOCKS = (
    Banner, Claims, Curves, Figure, Footer, Gallery, Grid, Header, KV, LogTail, MetricsTable,
    Notices, Progress, Prose, Scorecard, Stub, Tiles, UnitsTable,
)

__all__ = [
    "BASE_CLASSES", "ALL_BLOCKS", "Block", "Page", "chip", "esc", "fmt_duration", "fmt_number",
    "numbers_in", "panel", "rows_to_html", "rows_to_md", "tone_of",
    "Banner", "Claims", "Curve", "Curves", "Figure", "Footer", "Gallery", "Grid", "Header", "KV",
    "Lane", "LogTail", "Mark", "MetricRow", "MetricsTable", "Notices", "Progress", "Prose",
    "Provenance", "Scorecard", "Stub", "Tiles", "UnitsTable",
]
