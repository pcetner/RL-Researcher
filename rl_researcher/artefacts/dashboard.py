"""The live page: where a run stands, rewritten on every heartbeat.

A report is written once, when a run finishes. This is the other document — the one a person
opens while the run is going, to answer "is it alive, how far in, and is anything wrong yet".
It is driven from the runner's own heartbeat rather than by a separate watcher process, because
a watcher is one more thing to start and to stop, and a stale one outlives the run it was
watching. Driving it from the heartbeat means a run cannot happen without a page, and the page
cannot outlive the run.

Nothing here reads a file the run does not already write. What it reads it reads through
:func:`rl_researcher.status.run_status`, which is the same function the ``status`` command uses:
one collector, so the page and the exit code cannot disagree about whether a unit is stale.
Auto-SM64's dashboard had a second one, and the two had already drifted — different staleness
arithmetic and different timestamp handling.
"""

from __future__ import annotations

import html
import os
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from rl_researcher import atomic, charts
from rl_researcher import plotstyle as ps
from rl_researcher.kinds import LogVocab, RunKind
from rl_researcher.spec import RunSpec
from rl_researcher.status import RunStatus, run_status
from rl_researcher.style import BASE_CSS, LEGACY_DASHBOARD_CSS, THEME_BUTTONS, THEME_SCRIPT
from rl_researcher.units import UnitState, read_json

REFRESH_SECONDS = 15

#: Read when a kind declares no vocabulary of its own. Every token here is a word the framework's
#: own runner writes, not a project's.
DEFAULT_VOCAB = LogVocab(
    marks={"FAILED": "crit", "Traceback": "crit", "refused": "warn", "check [": "warn",
           "complete after": "ok", "resumed at": "warn", "hot-stopped": "warn",
           "stale lock": "warn", "report ->": "ok", "summary ->": "ok"},
    step_line=r"^(?P<ts>\d\d:\d\d:\d\d)\s+\[(?P<unit>[^\]]+)\]\s+(?:step|decision)\s+"
              r"(?P<step>[\d,]+)/(?P<max>[\d,]+)\s+(?P<rest>.*?)\s*$")


@dataclass
class DashboardData:
    """Everything the page needs, collected once.

    ``status`` carries the per-unit half and is the ``status`` command's own object. The rest
    are run-level rollups, which exist here rather than on ``RunStatus`` because they are about
    drawing a progress bar rather than about deciding an exit code.
    """

    status: RunStatus
    order: List[str] = field(default_factory=list)
    total_steps: int = 0
    done_steps: int = 0
    eta_all: Optional[float] = None
    elapsed_all: float = 0.0
    heartbeat: Optional[float] = None
    device: Optional[str] = None
    log_tail: List[str] = field(default_factory=list)
    summary: Optional[Dict[str, Any]] = None

    @property
    def units(self) -> List[UnitState]:
        return self.status.units

    @property
    def lock(self) -> Optional[Dict[str, Any]]:
        return self.status.lock

    def with_metrics(self) -> List[UnitState]:
        """The units that produced numbers, which is what every table of results draws from."""
        return [u for u in self.units if metrics_of(u)]


def metrics_of(unit: UnitState) -> Dict[str, float]:
    return dict((unit.result or {}).get("metrics") or {})


def history_of(unit: UnitState) -> Dict[str, List[float]]:
    """A unit's curves. A finished unit keeps its full history in the result; a running one
    carries a thinned copy in the heartbeat, so both can be drawn from the same call."""
    got = (unit.result or {}).get("history") or unit.progress.get("history") or {}
    return dict(got)


def last_of(unit: UnitState, keys: Sequence[str]) -> Dict[str, Any]:
    """The most recent value of each named series, off the heartbeat.

    The heartbeat's ``last`` block is where a running unit reports the numbers it has so far;
    a unit that reports them at the top level instead is read there too, because that is what
    every heartbeat written before ``UnitContext.beat`` existed did.
    """
    last = unit.progress.get("last") or {}
    return {k: (last.get(k) if k in last else unit.progress.get(k)) for k in keys}


def collect(spec: RunSpec, kind: RunKind, out: Path) -> DashboardData:
    """Everything the page needs, from the files the run already writes."""
    out = Path(out)
    st = run_status(spec, kind, out)
    total = done = 0
    elapsed = 0.0
    for u in st.units:
        cap = int(u.max_steps or spec.budget.max_steps or 0)
        total += cap
        done += min(int(u.step or 0), cap) if cap else int(u.step or 0)
        elapsed += float(u.elapsed_seconds or 0.0)
    # The projection is seconds per step over the units that finished, times the steps left --
    # a ratio of sums, so one very short unit cannot pull the estimate down the way a mean of
    # per-unit rates would.
    fin = [u for u in st.units if u.done and u.elapsed_seconds and u.step]
    per_step = (sum(float(u.elapsed_seconds or 0) for u in fin)
                / max(sum(int(u.step or 0) for u in fin), 1)) if fin else None
    ages = [u.age for u in st.units if u.age is not None]
    device = next((u.progress.get("device") for u in st.units if u.progress.get("device")), None)
    log_name = getattr(kind, "log_name", "run.log")
    return DashboardData(
        status=st,
        order=list(dict.fromkeys(u.arm for u in st.units)),
        total_steps=total, done_steps=done,
        eta_all=(per_step * (total - done)) if per_step and total > done else None,
        elapsed_all=elapsed,
        heartbeat=min(ages) if ages else None,
        device=device,
        log_tail=tail(out / log_name),
        summary=read_json(out / "results.json"),
    )


def tail(path: Path, keep: int = 200) -> List[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-keep:]
    except OSError:
        return []


def chip(unit: UnitState) -> Tuple[str, str]:
    """One unit's state and the tone a chip shows it in, worst news first.

    A failure outranks staleness because a unit that died is not merely quiet, and staleness
    outranks a result because a stale heartbeat beside a result means something wrote a result
    and then stopped saying so.
    """
    if unit.failed:
        return "failed", "crit"
    if unit.stale:
        return "stale", "crit"
    if unit.done:
        run_status_ = (unit.result or {}).get("status")
        return ("incomplete" if run_status_ == "incomplete" else "done"), "ok"
    if unit.status == "not started":
        return "queued", "muted"
    if unit.status in ("stopped", "incomplete"):
        return unit.status, "warn"
    return unit.status, "accent"


# ── the log tail ──────────────────────────────────────────────────────────────────────────

def vocab_of(kind: Any) -> LogVocab:
    """The kind's log vocabulary, falling back to the framework's own words."""
    got = getattr(kind, "log_vocab", None)
    if got is None:
        return DEFAULT_VOCAB
    try:
        vocab = got()
    except Exception:  # noqa: BLE001 - a page must never take a run down
        return DEFAULT_VOCAB
    if not isinstance(vocab, LogVocab) or not vocab.marks:
        return DEFAULT_VOCAB
    return vocab


def pick_log(lines: Sequence[str], vocab: LogVocab, keep: int = 12) -> List[str]:
    """The lines worth the space: never lose a completion or a failure to a burst of progress.

    A run logs a step line every ``log_every``, so a plain tail is a dozen near-identical rows
    and a unit finishing scrolls off within seconds of doing so. Notable lines are kept first,
    in order, and the remaining room goes to the most recent progress.
    """
    marks = vocab.marks or {}
    notable = [ln for ln in lines if any(tok in ln for tok in marks)]
    plain = [ln for ln in lines if ln not in notable]
    chosen = notable[-keep:]
    room = keep - len(chosen)
    if room > 0:
        chosen += plain[-room:]
    return [ln for ln in lines if ln in chosen][-keep:]


def format_log(lines: Sequence[str], vocab: LogVocab, order: Optional[Sequence[str]] = None,
               keep: int = 12) -> str:
    """The tail, with dim timestamps, lines toned by what they say, and step lines aligned."""
    marks = vocab.marks or {}
    step_re = re.compile(vocab.step_line) if vocab.step_line else None
    out: List[str] = []
    for raw in pick_log(lines, vocab, keep):
        cls = next((tone for token, tone in marks.items() if token in raw), "")
        m = step_re.match(raw) if step_re is not None else None
        if m is not None:
            got = m.groupdict()
            unit = got.get("unit") or ""
            colour = ps.variant_color(unit.rsplit(" seed", 1)[0], list(order or []))
            body = (f'<span class="ts">{html.escape(got.get("ts") or "")}</span>'
                    f'<span class="cellchip" style="color:{colour}">{html.escape(unit)}</span>'
                    f'<span class="stepn">{html.escape(got.get("step") or "")}'
                    f'<span class="of">/{html.escape(got.get("max") or "")}</span></span>'
                    f'<span class="rest">{html.escape(got.get("rest") or "")}</span>')
            out.append(f'<div class="ln step">{body}</div>')
            continue
        line = html.escape(raw)
        stamp = re.match(r"(\d\d:\d\d:\d\d)\s(.*)", line, re.S)
        body = f'<span class="ts">{stamp.group(1)}</span>{stamp.group(2)}' if stamp else line
        out.append(f'<div class="ln {cls}">{body}</div>')
    return "".join(out) or '<div class="ln of">no log yet</div>'


# ── writing it ────────────────────────────────────────────────────────────────────────────

Renderer = Callable[..., str]


def write_page(render: Renderer, target: Path, *, refresh: bool = True) -> Path:
    """Render and write atomically.

    The page is rewritten while a run is going and refreshes itself every
    ``REFRESH_SECONDS``, so a plain write gives a browser a real chance of reading half of one.
    A failed render also leaves the previous page standing rather than a truncated one.
    """
    return atomic.write_text(Path(target), render(refresh=refresh))


def periodic_writer(render: Renderer, target: Path, *, log: Optional[Callable[[str], None]] = None,
                    interval: float = REFRESH_SECONDS,
                    clock: Callable[[], float] = time.time,
                    also: Optional[Callable[[], None]] = None) -> Callable[..., None]:
    """A throttled page writer for the runner to call from its heartbeat.

    Measured against a finished twelve-unit study, one write is ~25 ms to collect and ~7 ms to
    render (the first render is ~1 s while ``mathtex`` typesets the formulas, after which they
    are memoised), so at a fifteen-second floor this is a fifth of a percent of a run. That is
    cheap enough to stay on the run's own thread; a background thread would isolate a cost that
    does not need isolating and add a lifetime to get wrong.

    A dashboard must never take a run down, so a failure disables the writer after one logged
    line rather than raising, or repeating itself every fifteen seconds for eight hours.
    ``also`` is anything else to rewrite on the same tick, such as a cross-run index.
    """
    state: Dict[str, Any] = {"last": 0.0, "on": True}

    def write(*, force: bool = False, refresh: bool = True) -> None:
        if not state["on"]:
            return
        now = clock()
        if not force and now - float(state["last"]) < interval:
            return
        state["last"] = now
        try:
            write_page(render, target, refresh=refresh)
            if also is not None:
                also()
        except Exception as exc:  # noqa: BLE001 - the run outranks its own dashboard
            state["on"] = False
            if log is not None:
                log(f"dashboard: {type(exc).__name__}: {exc} — not written again this run "
                    f"(the run itself is unaffected)")

    return write


# ── the panels ────────────────────────────────────────────────────────────────────────────
#
# Every one of these is generic over ``(spec, kind)``. What used to make them a study's is that
# they reached for that project's metric titles, definitions and formulas directly; they ask the
# kind's ``registry`` for those now, and its ``curves(spec)`` for which series to draw. A kind
# that declares neither still gets a page: names fall back to the metric's own, and the curve
# columns simply do not appear.

LOG_NOTE = '<span class="logs">log scale</span>'


def _title(kind: RunKind, name: str) -> str:
    reg = getattr(kind, "registry", None)
    return reg.title(name) if reg is not None else name


def duration(seconds: Optional[float]) -> str:
    if seconds is None or (isinstance(seconds, float) and seconds != seconds):
        return "—"
    s = int(max(seconds, 0))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def bubble(kind: RunKind, metric: Any, *, up: bool = False) -> str:
    """What a metric is, the way a reference would put it: expression, definition, reason.

    The definition comes from the kind's registry and the reason from the spec's own ``why`` --
    both already written, neither invented for the page -- and the expression is typeset rather
    than spelled out, because a ratio written in slashes and pipes is a sentence pretending to
    be maths.
    """
    from rl_researcher.mathtex import formula_svg

    reg = getattr(kind, "registry", None)
    parts = [f'<b>{html.escape(_title(kind, metric.name))}</b>']
    tex = formula_svg(reg.formula(metric.name), scale=1.15) if reg is not None else ""
    if tex:
        parts.append(f'<span class="bmath">{tex}</span>')
    target = charts.target_label(metric.bar, metric.direction)
    if target:
        parts.append(f'<span class="t">{target}</span>')
    what = html.escape(reg.description(metric.name) if reg is not None else "")
    if what:
        parts.append(f'<span class="w">{what}</span>')
    if getattr(metric, "why", ""):
        parts.append(f'<span class="y">{html.escape(metric.why)}</span>')
    return f'<span class="bubble{" up" if up else ""}">{"".join(parts)}</span>'


def metric_rows(spec: RunSpec, kind: RunKind, units: Sequence[UnitState],
                order: Sequence[str]) -> str:
    """The scorecard: one row per registered metric, its lane drawn as a small SVG.

    Rendered even before a unit finishes -- names, targets and empty lanes -- because a panel
    that does not exist yet cannot tell a reader what is being measured.
    """
    rows = []
    total = len(spec.metrics)
    for i, m in enumerate(spec.metrics):
        values = [(u.arm, float(metrics_of(u).get(m.name, float("nan"))),
                   f"{u.arm} seed {u.seed}: "
                   f'{charts.fmt(float(metrics_of(u).get(m.name, float("nan"))))}')
                  for u in units]
        seeds = [int(u.seed) for u in units]
        # A diverged seed is excluded from the mean and counted instead: averaging it in gave
        # 9.2e19, which is not this metric's central value in any sense a reader could use.
        finite = [v for _, v, _ in values if math.isfinite(v)]
        gone = sum(1 for _, v, _ in values if math.isinf(v))
        mean = charts.fmt(sum(finite) / len(finite)) if finite else "—"
        if gone:
            mean += f'<span class="sp">{gone} diverged</span>'
        log = charts.is_log([v for _, v, _ in values], m.bar)
        # The last rows would push a downward bubble past the panel, which clips it.
        up = i >= max(total - 4, total // 2)
        title = _title(kind, m.name)
        rows.append(
            f'<div class="mrow">'
            f'<div class="mname">{html.escape(title)}{bubble(kind, m, up=up)}</div>'
            f'<div class="mtarget">{charts.target_label(m.bar, m.direction) or "Reported"}'
            f'{LOG_NOTE if log else ""}</div>'
            f'<div class="mtrack">'
            f'{charts.track(values, m.bar, m.direction, list(order), label=title, seeds=seeds)}'
            f'</div>'
            f'<div class="mval">{mean}</div>'
            f'</div>')
    return f'<div class="mrows">{"".join(rows)}</div>'


def arm_table(spec: RunSpec, kind: RunKind, agg: Dict[str, Any], order: Sequence[str]) -> str:
    """Arms down the side, metrics across the top: mean, spread, pass mark, best marked.

    This is not a second set of tracks. The scorecard above places every unit by position, so a
    positional chart of the same numbers aggregated would be the same picture twice. A table
    makes a different kind of statement -- exact values, side by side, comparable down a
    column -- which is the one thing the scorecard cannot do.
    """
    from rl_researcher.artefacts.report import judge

    metrics = [m for m in spec.metrics if m.bar is not None]
    present = [a for a in order if a in agg]
    if not metrics or not present:
        return ""

    def value(name: str, metric: Any) -> Optional[Tuple[float, float, int, int]]:
        mean, std, n, div = agg[name].get(metric.name, (float("nan"), float("nan"), 0, 0))
        return (mean, std, n, div) if n and not math.isnan(mean) else None

    best: Dict[str, str] = {}
    for m in metrics:
        # An arm with a diverged seed is not eligible to be the best at anything: its mean is
        # over the seeds that survived, which is not the same quantity the others report.
        scored = [(a, got[0]) for a in present for got in [value(a, m)] if got and not got[3]]
        # Two, not one. Early in a run a single arm has finished units and every column crowned
        # it -- best-of-one is not a comparison, and it reads like a result.
        if len(scored) > 1:
            pick = (min if m.direction == "lower" else max)(scored, key=lambda kv: kv[1])
            best[m.name] = pick[0]

    heads = "".join(
        f'<th class="mid">{html.escape(_title(kind, m.name))}'
        f'<span class="th2">{charts.target_label(m.bar, m.direction) or "Reported"}</span></th>'
        for m in metrics)
    rows = []
    for name in present:
        cells, seeds = [], 0
        for m in metrics:
            got = value(name, m)
            if not got:
                cells.append('<td class="num mid of">not computed</td>')
                continue
            mean, std, n, div = got
            seeds = max(seeds, n + div)
            ok = judge(m, mean, div, arm=name)
            spread = (f'<span class="sp">± {charts.fmt(std)}</span>'
                      if n > 1 and not math.isnan(std) else "")
            if div:
                spread += f'<span class="sp">{div} of {n + div} diverged</span>'
            crown = '<span class="crown">best</span>' if best.get(m.name) == name else ""
            # Three outcomes, not two. `None` is the reference arm of a comparison, which is
            # never judged against itself; printing a cross there marks a number that was
            # never on trial.
            tone, mark = ("ok", "✓") if ok else (("no", "✗") if ok is False else ("of", ""))
            cells.append(f'<td class="num mid {tone}">{charts.fmt(mean)}'
                         f'<span class="mark">{mark}</span>{spread}{crown}</td>')
        rows.append(f'<tr><td class="cell">{charts.glyph(name, list(order))}{html.escape(name)}'
                    f'<span class="seed">{seeds} seed{"" if seeds == 1 else "s"}</span></td>'
                    f'{"".join(cells)}</tr>')
    return f'<table class="vtable"><tr><th>arm</th>{heads}</tr>{"".join(rows)}</table>'


def identity(unit: UnitState, order: Sequence[str], *, best: bool = False) -> str:
    star = '<span class="best">best</span>' if best else ""
    return (f'<td class="cell">{charts.glyph(unit.arm, list(order), seed=int(unit.seed))}'
            f'{html.escape(unit.arm)}<span class="seed">seed {unit.seed}</span>{star}</td>')


def state_cell(unit: UnitState) -> str:
    label, tone = chip(unit)
    return f'<td><span class="chip t-{tone}">{label}</span></td>'


def notes(unit: UnitState) -> str:
    out = []
    if unit.error:
        out.append(html.escape(str(unit.error)))
    if unit.resumable and unit.checkpoint_step is not None:
        out.append(f"resumable from {unit.checkpoint_step:,}")
    if unit.resumed_from_step:
        out.append(f"resumed at {unit.resumed_from_step:,}")
    if unit.age is not None and not unit.done:
        out.append(f"updated {duration(unit.age)} ago")
    return f'<td class="note">{"<br>".join(out)}</td>'


def curves_of(spec: RunSpec, kind: RunKind) -> List[Any]:
    try:
        return list(kind.curves(spec))
    except Exception:  # noqa: BLE001 - a page must never take a run down
        return []


def floors_of(spec: RunSpec, kind: RunKind) -> Dict[str, Optional[float]]:
    """Each curve's floor line, taken from the metric the kind says the curve is bounded by.

    The study page hardcoded one metric name here. A curve drawn with its floor shows a plateau
    under the bar as a decision to make rather than as a line going along, and which metric that
    is, is the kind's to say -- ``CurveSpec.floor_metric`` is on the protocol for exactly this.
    """
    bars = {m.name: m.bar for m in spec.metrics}
    return {c.key: (bars.get(c.floor_metric) if c.floor_metric else None)
            for c in curves_of(spec, kind)}


def running_table(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    """Units still going: progress, rate, and each curve under its own name.

    Failed units are excluded and get :func:`failed_panel`. One sat here reading ``0.0 steps/s``
    under a heading that said it was in progress, with the reason for the failure in the notes
    column, off past the horizontal scroll at any normal width.
    """
    order = data.order
    live = [u for u in data.units if not u.done and u.status not in ("not started", "failed")]
    if not live:
        return ""
    curves = curves_of(spec, kind)
    floors = floors_of(spec, kind)
    heads = "".join(f'<th class="mid">{html.escape(c.title)}</th>' for c in curves)
    rows = []
    for u in live:
        colour = ps.variant_color(u.arm, list(order))
        step, cap = int(u.step or 0), int(u.max_steps or spec.budget.max_steps or 1)
        pct = 100.0 * step / max(cap, 1)
        history = history_of(u)
        last = last_of(u, [c.key for c in curves])
        cols = []
        for c in curves:
            series = history.get(c.key, [])
            svg = charts.curve(series, colour=colour, label=c.title, steps=step,
                               floor=floors.get(c.key))
            got = last.get(c.key)
            shown = "—" if got is None or float(got) != float(got) else charts.fmt(float(got))
            first = f"{charts.fmt(float(series[0]))} → " if len(series) >= 2 else ""
            cols.append(f'<td class="curve">{svg}'
                        f'<span class="range">{first}<b>{shown}</b></span></td>')
        rows.append(f"""
    <tr>{identity(u, order)}{state_cell(u)}
      <td class="num">{step:,}<span class="of"> / {cap:,}</span>
        <div class="track"><i style="width:{pct:.1f}%;background:{colour}"></i></div></td>
      <td class="num">{float(u.rate or 0.0):.1f}<span class="of"> steps/s</span></td>
      <td class="num">{duration(u.eta_seconds)}</td>
      {''.join(cols)}{notes(u)}
    </tr>""")
    return (f'<div class="panel scroll"><h2>in progress</h2><table>'
            f'<tr><th>unit</th><th>state</th><th>step</th><th>rate</th><th>time left</th>'
            f'{heads}<th></th></tr>{"".join(rows)}</table></div>')


def finished_table(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    """Units that finished: one column per registered bar-metric, with its pass mark."""
    from rl_researcher.artefacts.report import judge

    order = data.order
    done = data.with_metrics()
    if not done:
        return ""
    bars = [m for m in spec.metrics if m.bar is not None]
    primary = bars[0] if bars else None
    if primary is not None:
        # Best first on the primary metric, so the answer is the top line.
        def rank(u: UnitState) -> float:
            v = float(metrics_of(u).get(primary.name, float("nan")))
            if v != v:
                return float("inf")
            return v if primary.direction == "lower" else -v
        done = sorted(done, key=rank)
    curves = curves_of(spec, kind)
    floors = floors_of(spec, kind)
    curve_heads = "".join(f'<th class="mid">{html.escape(c.title)}</th>' for c in curves)
    heads = "".join(f'<th class="mid">{html.escape(_title(kind, m.name))}'
                    f'<span class="th2">{charts.target_label(m.bar, m.direction)}</span></th>'
                    for m in bars)
    rows = []
    for idx, u in enumerate(done):
        colour = ps.variant_color(u.arm, list(order))
        history = history_of(u)
        drawn = "".join(
            f'<td class="curve">'
            f"{charts.curve(history.get(c.key, []), colour=colour, label=c.title, floor=floors.get(c.key), steps=int(u.step or 0))}"
            f'</td>' for c in curves)
        cols = []
        for m in bars:
            v = float(metrics_of(u).get(m.name, float("nan")))
            if v != v:
                cols.append('<td class="num mid of">n/a</td>')
                continue
            # One unit, so a non-finite value is this unit's own divergence rather than a count
            # across seeds. charts.fmt renders it "diverged"; judge refuses it either way.
            ok = judge(m, v, 0 if math.isfinite(v) else 1, arm=u.arm)
            tone, mark = ("ok", "✓") if ok else (("no", "✗") if ok is False else ("of", ""))
            cols.append(f'<td class="num mid {tone}">{charts.fmt(v)}'
                        f'<span class="mark">{mark}</span></td>')
        top = idx == 0 and len(done) > 1
        rows.append(f"""
    <tr{' class="bestrow"' if top else ""}>{identity(u, order, best=top)}{state_cell(u)}
      <td class="num">{duration(u.elapsed_seconds)}</td>{''.join(cols)}{drawn}{notes(u)}
    </tr>""")
    return (f'<div class="panel scroll"><h2>finished</h2><table>'
            f'<tr><th>unit</th><th>state</th><th>time</th>{heads}{curve_heads}<th></th></tr>'
            f'{"".join(rows)}</table></div>')


def failed_panel(data: DashboardData) -> str:
    """Units that died, with the reason first.

    Its own panel, in the reading order between what is running and what is queued -- a failure
    has to be noticed without the page dropping everything else for it. The whole-run chip at
    the top already says FAILED; this says which unit, why, and where to resume from.
    """
    order = data.order
    dead = [u for u in data.units if u.failed]
    if not dead:
        return ""
    rows = []
    for u in dead:
        label, tone = chip(u)
        where = [f"stopped at {int(u.step or 0):,} of {int(u.max_steps or 0):,}"]
        if u.resumable and u.checkpoint_step is not None:
            where.append(f"resumable from {u.checkpoint_step:,}")
        if u.age is not None:
            where.append(f"updated {duration(u.age)} ago")
        rows.append(
            f'<div class="failrow">'
            f'<div class="failwho">{charts.glyph(u.arm, list(order), seed=int(u.seed))}'
            f'{html.escape(u.arm)}<span class="seed">seed {u.seed}</span>'
            f'<span class="chip t-{tone}">{label}</span></div>'
            f'<div class="failmsg">{html.escape(str(u.error or "no reason recorded"))}</div>'
            f'<div class="failnote">{" · ".join(where)}</div></div>')
    plural = "" if len(dead) == 1 else "s"
    return (f'<div class="panel"><h2>failed · {len(dead)} unit{plural}</h2>'
            f'<div class="fails">{"".join(rows)}</div></div>')


def queued_panel(data: DashboardData) -> str:
    """Units not started yet — their own box, so the in-progress table ends cleanly."""
    order = data.order
    queued = [u for u in data.units if u.status == "not started"]
    if not queued:
        return ""
    chips = "".join(f'<span class="qchip">{charts.glyph(u.arm, list(order))}'
                    f'{html.escape(u.arm)}<i>seed {u.seed}</i></span>' for u in queued)
    return (f'<div class="panel"><h2>queued · {len(queued)}</h2>'
            f'<div class="queued">{chips}</div></div>')


def _rung(spec: RunSpec, kind: RunKind, unit: UnitState) -> str:
    """What one cell of the ladder says under its state chip.

    Every branch answers the same question in the terms that state makes available: a finished
    unit by its primary number, a failed one by its reason, a live one by where it is and when
    it will be done, a queued one by how much work it is.
    """
    state, _tone = chip(unit)
    if state == "failed":
        return html.escape(str(unit.error or "no reason recorded").splitlines()[0][:70])
    if state in ("done", "incomplete"):
        primary = next((m.name for m in spec.metrics if m.bar is not None),
                       spec.metrics[0].name if spec.metrics else "")
        got = metrics_of(unit).get(primary)
        bits = [f"{_title(kind, primary)} {charts.fmt(float(got))}"] if got is not None else []
        if state == "incomplete":
            bits.insert(0, f"{int(unit.step or 0):,}/{int(unit.max_steps or 0):,}")
        bits.append(duration(unit.elapsed_seconds))
        return html.escape(" · ".join(b for b in bits if b))
    if state in ("running", "stale"):
        bits = [f"{int(unit.step or 0):,}/{int(unit.max_steps or 0):,}"]
        if unit.rate:
            bits.append(f"{float(unit.rate):.1f}/s")
        bits.append(f"quiet {duration(unit.age)}" if state == "stale"
                    else f"eta {duration(unit.eta_seconds)}")
        return html.escape(" · ".join(bits))
    steps = int(unit.max_steps or spec.budget.max_steps or 0)
    return html.escape(f"{steps:,} {getattr(kind, 'step_noun', 'step')}s")


def ladder(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    """Every unit as a grid: one row per arm, one column per seed.

    The shape of a run is the shape of the argument it makes, so a page can draw it that way
    instead of as twelve rows of a table. A reader sees at a glance whether a whole arm is
    missing -- which withholds a comparison -- or one seed of each, which does not.

    Not in the default page: it says the same things the running, failed, queued and finished
    tables say, more compactly and with less room for each. A kind names one or the other.
    """
    seeds = list(spec.seeds)
    by_key = {(u.arm, int(u.seed)): u for u in data.units}
    # Sized from the seed count rather than `auto-fit`, which let the columns fall below the
    # width their contents need and clipped them. Below `min-width` the panel scrolls sideways,
    # which is the honest failure: a number pushed off-screen is worse than one you scroll to.
    cols = (f'grid-template-columns:minmax(112px,168px) '
            f'repeat({len(seeds)},minmax(124px,1fr));'
            f'min-width:{112 + 128 * len(seeds)}px')
    head = "".join(f'<div class="lhead">seed {s}</div>' for s in seeds)
    rows = [f'<div class="lrow" style="{cols}"><div class="lhead">arm</div>{head}</div>']
    for arm in data.order:
        cells = []
        for seed in seeds:
            unit = by_key.get((arm, int(seed)))
            if unit is None:
                continue
            state, tone = chip(unit)
            cells.append(f'<div class="lcell{" q" if state == "queued" else ""}">'
                         f'<span class="chip t-{tone}">{html.escape(state)}</span>'
                         f'<span class="lwhat">{_rung(spec, kind, unit)}</span></div>')
        rows.append(f'<div class="lrow" style="{cols}"><div class="larm">'
                    f'{charts.glyph(arm, list(data.order))}{html.escape(arm)}</div>'
                    f'{"".join(cells)}</div>')
    return (f'<div class="panel"><h2>ladder · {len(data.order)} arms × {len(seeds)} seeds</h2>'
            f'<div class="ladder scroll">{"".join(rows)}</div></div>')

def headline(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    """The best finished unit, as chips rather than a sentence.

    Four numbers and two pass marks do not belong in prose. The floor travels with the win: a
    unit over the primary bar but under a collapse floor is not a winner, and the header must
    not read like it is. Which metric that floor is, is the kind's to say, through the
    ``floor_metric`` of the curves it declares -- the study page had one name hardcoded here.
    """
    from rl_researcher.artefacts.report import judge

    primary = next((m for m in spec.metrics if m.bar is not None), None)
    if primary is None:
        return ""
    # isfinite, not "not isnan": a diverged unit reports inf, and on a "higher is better"
    # primary metric max() would have crowned it the headline of the whole run.
    done = [u for u in data.with_metrics()
            if math.isfinite(float(metrics_of(u).get(primary.name, float("nan"))))]
    if not done:
        return ""
    best = (min if primary.direction == "lower" else max)(
        done, key=lambda u: float(metrics_of(u)[primary.name]))

    floor_names = {c.floor_metric for c in curves_of(spec, kind) if c.floor_metric}
    floor = next((m for m in spec.metrics if m.name in floor_names), None)
    chips = []
    for m in [primary] + ([floor] if floor is not None and floor is not primary else []):
        if m is None or m.bar is None:
            continue
        v = float(metrics_of(best).get(m.name, float("nan")))
        if not math.isfinite(v):
            continue
        ok = judge(m, v, arm=best.arm)
        chips.append(f'<span class="stat {"ok" if ok else "no"}">'
                     f'<span class="n">{html.escape(_title(kind, m.name))}</span>'
                     f'<b>{v:.3f}</b>'
                     f'<span class="n">{charts.target_label(m.bar, m.direction)}</span></span>')
    who = (f'<span class="k">Current best</span>'
           f'<span class="who">{charts.glyph(best.arm, list(data.order))}'
           f'{html.escape(best.arm)}<i>seed {best.seed}</i></span>')
    return who + "".join(chips)


# ── the page ──────────────────────────────────────────────────────────────────────────────
#
# A page is an ordered list of named sections and nothing else. That shape exists because the
# two dashboards this replaces were each a single 60-line f-string, so the only way to give a
# kind a panel of its own was to write a second page — which is how there came to be two, with
# two log formatters, two page writers and two collectors between them.
#
# A kind names the sections it wants (``page_sections``) and may contribute its own
# (``page_extras``). Everything else — the shell, the head, the tiles, the progress bar, the
# foot — is the framework's, because none of it was ever project-specific.

DASHBOARD_CSS = BASE_CSS + LEGACY_DASHBOARD_CSS

#: Every section the framework itself draws, in the order a reader wants them: what the run
#: found, what it is measuring, how the arms compare, what is happening now, what went wrong,
#: what has not started, what has finished, and the log underneath all of it.
DEFAULT_SECTIONS: Tuple[str, ...] = (
    "headline", "metrics", "arms", "running", "failed", "queued", "finished", "log")


def _section_headline(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    lead = headline(spec, kind, data)
    return f'<div class="lead">{lead}</div>' if lead else ""


def _section_metrics(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    finished = data.with_metrics()
    counts = {arm: (sum(1 for u in finished if u.arm == arm), len(spec.seeds))
              for arm in data.order}
    return (f'<div class="panel"><h2>registered metrics · hover for details</h2>'
            f'{charts.legend(list(data.order), counts)}'
            f'{metric_rows(spec, kind, finished, data.order)}</div>')


def _section_arms(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    from rl_researcher.artefacts.report import aggregate

    finished = data.with_metrics()
    if not finished:
        return ""
    agg = aggregate([u.result or {} for u in finished], [m.name for m in spec.metrics])
    table = arm_table(spec, kind, agg, data.order)
    return (f'<div class="panel scroll"><h2>by arm · mean and spread across seeds</h2>'
            f'{table}</div>') if table else ""


def _section_log(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    body = format_log(data.log_tail, vocab_of(kind), data.order)
    return f'<div class="panel"><h2>log</h2><div class="log">{body}</div></div>'


SECTIONS: Dict[str, Callable[[RunSpec, RunKind, DashboardData], str]] = {
    "headline": _section_headline,
    "metrics": _section_metrics,
    "arms": _section_arms,
    "running": lambda spec, kind, data: running_table(spec, kind, data),
    "failed": lambda spec, kind, data: failed_panel(data),
    "queued": lambda spec, kind, data: queued_panel(data),
    "finished": lambda spec, kind, data: finished_table(spec, kind, data),
    "log": _section_log,
    "ladder": ladder,
}


def sections_of(kind: RunKind) -> Tuple[str, ...]:
    """The section names this kind's page draws, in order.

    A kind that says nothing gets :data:`DEFAULT_SECTIONS`. A kind that declares
    ``page_sections`` gets exactly what it names, which is how it drops a panel it has no data
    for and slots its own between two of the framework's.
    """
    named = getattr(kind, "page_sections", None)
    return tuple(str(n) for n in named) if named else DEFAULT_SECTIONS


def section(name: str, spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    """One named section's HTML, the kind's own before the framework's.

    An unknown name renders as nothing rather than raising. A page is the artefact a person
    opens *because* something has gone wrong; it must not be the second thing to break.
    """
    extras = getattr(kind, "page_extras", None) or {}
    fn = extras.get(name) or SECTIONS.get(name)
    if fn is None:
        return ""
    return fn(spec, kind, data)


def tiles(pairs: Sequence[Tuple[str, str]]) -> str:
    """The four big numbers across the top: value, then what it is."""
    return ('<div class="tiles">'
            + "".join(f'<div class="tile"><b>{v}</b><span>{html.escape(k)}</span></div>'
                      for v, k in pairs)
            + "</div>")


def page_tiles(spec: RunSpec, kind: RunKind, data: DashboardData) -> str:
    unit_noun = getattr(kind, "unit_noun", "unit")
    step_noun = getattr(kind, "step_noun", "step")
    pct = 100.0 * data.done_steps / max(data.total_steps, 1)
    done, total = data.status.done, len(data.units)
    return tiles([
        (f'{done}<span class="of">/{total}</span>', f"{unit_noun}s done"),
        (f"{pct:.0f}%", f"of {data.total_steps:,} {step_noun}s"),
        (duration(data.elapsed_all), "compute spent"),
        (duration(data.eta_all), "projected left"),
    ])


def page_foot(spec: RunSpec, kind: RunKind, data: DashboardData, *, refresh: bool) -> str:
    e = html.escape
    extra = getattr(kind, "page_note", None)
    mid = f" · {extra(spec, data)}" if callable(extra) else ""
    when = time.strftime("%H:%M:%S")
    tail_note = (f"refreshing every {REFRESH_SECONDS}s" if refresh
                 else "rendered once, the run has ended")
    return (f'<div class="foot">{e(spec.name)} · {e(str(data.device or "cpu"))}{mid} · '
            f'<code>status</code> is the authority on liveness, not this page · '
            f'{when}, {tail_note}</div>')


def shell(title: str, body: str, *, refresh: bool = True, extra_css: str = "",
          defs: str = "") -> str:
    """The whole document around a body: no request leaves it, ever.

    Not one URL, font import or script src — the page has to open from a file and from a
    share, on a machine with no network, which is exactly where a run tends to be.
    """
    meta = f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">' if refresh else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{meta}
<title>{html.escape(title)}</title>
<style>{DASHBOARD_CSS}{extra_css}</style>
<script>{THEME_SCRIPT}</script></head>
<body>{defs}<div class="wrap">
{body}
</div></body></html>
"""


def render(spec: RunSpec, kind: RunKind, data: DashboardData, *, refresh: bool = True) -> str:
    """The live page for one run."""
    e = html.escape
    pct = 100.0 * data.done_steps / max(data.total_steps, 1)
    beat = (f' · updated {duration(data.heartbeat)} ago'
            if data.heartbeat is not None else "")
    defs = charts.seed_defs([(ps.variant_color(u.arm, list(data.order)), int(u.seed))
                             for u in data.units] + [(charts.SEED_KEY_COLOUR, 1)])
    head = (f'<div class="head"><h1>{e(spec.name)}</h1>'
            f'<span class="chip t-{data.status.tone}">{e(data.status.state)}{beat}</span>'
            f'<span class="spacer"></span>{THEME_BUTTONS}</div>')
    track = (f'<div class="track bigtrack">'
             f'<i style="width:{pct:.1f}%;background:{ps.ACCENT}"></i></div>')
    body = "\n".join([head, page_tiles(spec, kind, data), track]
                     + [section(n, spec, kind, data) for n in sections_of(kind)]
                     + [page_foot(spec, kind, data, refresh=refresh)])
    return shell(f"{spec.name} · {kind.name} dashboard", body, refresh=refresh,
                 extra_css=str(getattr(kind, "page_css", "")), defs=defs)


def write_dashboard(spec: RunSpec, kind: RunKind, out: Path, path: Optional[Path] = None,
                    *, refresh: bool = True) -> Path:
    out = Path(out)
    target = Path(path) if path else out / "dashboard.html"
    return write_page(
        lambda *, refresh=refresh: render(spec, kind, collect(spec, kind, out), refresh=refresh),
        target, refresh=refresh)


# ── the index over every run ──────────────────────────────────────────────────────────────
#
# One index, every kind. It was per-project and per-kind because `survey` loaded one kind's
# spec loader directly; it dispatches through `kind_for` now, so a study and an engine loop
# appear in the same table and a third kind needs no change here at all.

#: Worst news first. A finished run is the least urgent thing on the page; a failed one is the
#: most, and a stale one is a failure that has not admitted it yet.
STATE_RANK = {"FAILED": 0, "STALE": 1, "running": 2, "stopped": 3, "hot-stopped": 3,
              "finished": 4, "not started": 5}


@dataclass
class IndexRow:
    """One run's line on the index."""

    name: str
    kind: str
    spec: Optional[RunSpec] = None
    out: Optional[Path] = None
    data: Optional[DashboardData] = None
    state: str = "not started"
    tone: str = "muted"
    headline: str = ""
    error: str = ""


def survey(config: Any) -> List[IndexRow]:
    """One row per spec under ``[paths].specs``, whatever kind each one names."""
    from rl_researcher.config import kind_for, out_dir_for

    rows: List[IndexRow] = []
    for path in sorted(Path(config.path("specs")).glob("*.toml")):
        try:
            kind = kind_for(path, config)
            spec = kind.load(path)
        except Exception as exc:  # noqa: BLE001 - one broken spec must not hide the others
            # Named, not skipped. A spec that stopped parsing is a thing to fix, and the index
            # is where a person would look for it; dropping the row hides the only symptom.
            rows.append(IndexRow(name=path.stem, kind="?", state="unreadable", tone="crit",
                                 error=f"{type(exc).__name__}: {exc}"))
            continue
        out = out_dir_for(spec, config)
        data = collect(spec, kind, out) if out.is_dir() else None
        rows.append(IndexRow(
            name=spec.name, kind=kind.name, spec=spec, out=out, data=data,
            state=data.status.state if data else "not started",
            tone=data.status.tone if data else "muted",
            headline=headline(spec, kind, data) if data else ""))
    rows.sort(key=lambda r: (STATE_RANK.get(r.state, 9), r.name))
    return rows


def index_row(row: IndexRow, base: Path) -> str:
    """One line, its link relative to wherever the index itself is written.

    Different kinds have different output roots, so a link built by joining the run's name to
    the index's own directory resolves only for the kind whose root the index happens to sit
    in. Every other row's link is dead, which the page has no way to show.
    """
    e = html.escape
    d = row.data
    if row.spec is None:
        return (f'<tr><td class="cell">{e(row.name)}</td>'
                f'<td><span class="chip t-crit">{e(row.state)}</span></td>'
                f'<td colspan="3" class="note">{e(row.error)}</td></tr>')
    done = f"{d.status.done}/{len(d.units)}" if d else "—"
    pct = (100.0 * d.done_steps / max(d.total_steps, 1)) if d else 0.0
    href = ""
    if d is not None and row.out is not None:
        href = os.path.relpath(row.out / "dashboard.html", base).replace(os.sep, "/")
    link = f'<a href="{e(href)}">{e(row.name)}</a>' if href else e(row.name)
    return f"""
    <tr>
      <td class="cell">{link}<span class="seed">{e(row.kind)}</span></td>
      <td><span class="chip t-{row.tone}">{e(row.state)}</span></td>
      <td class="num">{done}<span class="of"> units</span>
        <div class="track"><i style="width:{pct:.1f}%;background:{ps.ACCENT}"></i></div></td>
      <td class="num">{duration(d.eta_all) if d else '—'}</td>
      <td class="metrics">{row.headline or '<span class="of">no finished unit yet</span>'}</td>
    </tr>"""


def render_index(rows: Sequence[IndexRow], base: Path, *, title: str = "runs",
                 refresh: bool = True) -> str:
    body = (f'<div class="head"><h1>{html.escape(title)}</h1>'
            f'<span class="chip t-muted">{len(rows)} registered</span>'
            f'<span class="spacer"></span>{THEME_BUTTONS}</div>'
            f'<div class="panel scroll"><table>'
            f'<tr><th>run</th><th>state</th><th>units</th><th>left</th><th>best so far</th></tr>'
            f'{"".join(index_row(r, base) for r in rows)}</table></div>'
            f'<div class="foot">Failed and stale first, then running, then finished. '
            f'Refreshes every {REFRESH_SECONDS}s. '
            f'Rendered {time.strftime("%H:%M:%S")}.</div>')
    return shell(title, body, refresh=refresh)


def write_index(config: Any, path: Optional[Path] = None, *, refresh: bool = True) -> Path:
    """The index beside the run directories, so a relative link to one of them resolves."""
    target = Path(path) if path else config.path("state") / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    return write_page(
        lambda *, refresh=refresh: render_index(survey(config), target.parent,
                                                title=config.name, refresh=refresh),
        target, refresh=refresh)


def page_writer_factory(spec: RunSpec, kind: RunKind, out: Path,
                        log: Callable[[str], None]) -> Callable[..., None]:
    """What ``rl_researcher.run`` hands the runner to drive from its heartbeat.

    The config is found by walking up from the run's own output directory rather than passed
    in, because the runner's ``page_writer`` seam is deliberately narrow: a page is not
    allowed to be a reason the runner's signature grows. If there is no config above ``out``
    -- a run driven straight from the library, in a temp directory -- the index is skipped and
    the run's own page is still written.
    """
    from rl_researcher.config import load_config

    also: Optional[Callable[[], None]] = None
    try:
        config = load_config(out, required=True)
    except (FileNotFoundError, OSError):
        pass
    else:
        def also() -> None:
            """The index always refreshes: other runs on it may still be going."""
            write_index(config)
    return periodic_writer(
        lambda *, refresh=True: render(spec, kind, collect(spec, kind, out), refresh=refresh),
        Path(out) / "dashboard.html", log=log, also=also)
