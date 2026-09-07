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
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from rl_researcher import atomic
from rl_researcher import plotstyle as ps
from rl_researcher.kinds import LogVocab, RunKind
from rl_researcher.spec import RunSpec
from rl_researcher.status import RunStatus, run_status
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
