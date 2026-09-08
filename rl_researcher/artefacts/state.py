"""The state page: one file that says what needs a human, worst news first.

Every other artefact is about one run. This one is about the project, and it is the page a
session opens before it does anything else. It answers, in this order: what is waiting on you,
what is running right now, what is queued, what was decided lately, and whether the machinery
itself is healthy.

It is entirely generated. Nothing is typed into it, because an input that lives only in a page
is an input that gets stale; the human's inputs are files instead (a ticked decision box, an
edited queue, an approval, a killed process), and the next `state` reads them.

Order is the design. A run that finished at three in the morning and has been sitting unread
is the most expensive thing in the project, so it goes at the top; the health section, which is
usually boring, goes at the bottom.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from rl_researcher import atomic
from rl_researcher.artefacts.writer import Artefact, write
from rl_researcher.blocks import KV, Notices, UnitsTable
from rl_researcher.config import Config
from rl_researcher.ledger import Ledger, open_ledger
from rl_researcher.regions import find
from rl_researcher.spec import load_toml
from rl_researcher.status import RunStatus, run_status
from rl_researcher.units import stamp_now, stamp_of

STATE_MD = "STATE.md"
STATE_HTML = "state.html"
STATE_JSON = "state.json"


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    seconds = float(seconds)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


@dataclass
class Waiting:
    """A run whose result is on disk and whose decision box is still empty."""

    run: str
    kind: str
    artefact: str
    outcome: str = ""
    headline: List[str] = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    finished: str = ""


@dataclass
class StateView:
    project: str
    generated: str
    waiting: List[Waiting] = field(default_factory=list)
    running: List[Dict[str, Any]] = field(default_factory=list)
    queued: List[Dict[str, Any]] = field(default_factory=list)
    decided: List[Dict[str, Any]] = field(default_factory=list)
    #: Registered and never started. Not on the page -- a spec nobody has run is not news, and
    #: this page is what needs a human -- but in the JSON, because a reader of the JSON that
    #: cannot see a spec until it has been run has no way to offer to run it.
    ready: List[Dict[str, Any]] = field(default_factory=list)
    health: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    findings: List[Dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "project": self.project, "generated": self.generated, "context": self.context, "findings": self.findings,
            "waiting": [w.__dict__ for w in self.waiting],
            "running": self.running, "queued": self.queued,
            "decided": self.decided, "ready": self.ready, "health": self.health,
        }


# --------------------------------------------------------------------------- gathering


def specs_in(config: Config) -> List[Path]:
    d = config.path("specs")
    return sorted(p for p in d.glob("*.toml") if p.is_file()) if d.is_dir() else []


def _decision_region(artefact: Path) -> Optional[str]:
    """The body of the report's decision stub, or ``None`` if it has none.

    Preferred form is an authored region. Reports written before regions existed carry a plain
    ``## Decision`` section instead, and those are read too: a study that was decided months ago
    must not reappear in "Awaiting you" because the file it was decided in has an older shape.
    """
    if not artefact.is_file():
        return None
    try:
        text = artefact.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        for r in find(text):
            if r.kind == "authored" and r.arg.startswith("decision"):
                return r.body
    except ValueError:
        pass
    return section(text, "Decision")


#: An H2 whose title starts with ``name``, and everything under it up to the next H2.
_SECTION = r"(?ms)^##[ \t]+{name}[^\r\n]*[\r\n]+(?P<body>.*?)(?=^##[ \t]|\Z)"


def section(text: str, name: str) -> Optional[str]:
    m = re.search(_SECTION.format(name=re.escape(name)), text)
    return m.group("body") if m else None


def ticked(body: Optional[str]) -> List[str]:
    """The options ticked in a decision stub, as ``- [x] iterate`` lines."""
    if not body:
        return []
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.lower().startswith(("- [x]", "* [x]")):
            out.append(s[5:].strip())
    return out


def options(body: Optional[str]) -> List[str]:
    if not body:
        return []
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.lower().startswith(("- [ ]", "- [x]", "* [ ]", "* [x]")):
            out.append(s[5:].strip())
    return out


def _outcome_line(spec: Any, summary: Dict[str, Any]) -> str:
    """The registered outcome as the spec's own conjunction, per arm."""
    from rl_researcher.artefacts.report import aggregate, judge

    names = [m.name for m in spec.metrics]
    agg = aggregate(summary.get("runs") or [], names)
    wanted = list(spec.conjunction) or [m.name for m in spec.metrics if m.bar is not None or m.compare_to]
    if not wanted:
        return "no bars registered; this run reports rather than decides"
    cleared: List[str] = []
    missed: List[str] = []
    for arm, per in agg.items():
        bad = []
        for name in wanted:
            m = spec.metric(name)
            if m is None:
                continue
            mean, _spread, _n, diverged = per.get(name, (float("nan"), float("nan"), 0, 0))
            ref_stats = agg.get(m.compare_to, {}).get(name) if m.compare_to else None
            ref = None if ref_stats is None else ref_stats[0]
            if judge(m, mean, diverged, reference=ref, arm=arm) is False:
                bad.append(name)
        (missed if bad else cleared).append(arm if not bad else f"{arm} (missed {', '.join(bad)})")
    parts = []
    if cleared:
        parts.append("cleared every bar: " + ", ".join(sorted(cleared)))
    if missed:
        parts.append("missed: " + "; ".join(sorted(missed)))
    return " · ".join(parts)


def _headline(spec: Any, summary: Dict[str, Any], limit: int = 4) -> List[str]:
    from rl_researcher.artefacts.report import aggregate, bar_mark, fmt

    names = [m.name for m in spec.metrics if m.bar is not None or m.compare_to][:limit]
    if not names:
        names = [m.name for m in spec.metrics][:limit]
    agg = aggregate(summary.get("runs") or [], names)
    arms = list(agg)
    if not arms:
        return []
    rows = ["| metric | " + " | ".join(arms) + " |",
            "|---" * (len(arms) + 1) + "|"]
    for name in names:
        m = spec.metric(name)
        cells = []
        for arm in arms:
            mean, spread, n, diverged = agg[arm].get(name, (float("nan"), float("nan"), 0, 0))
            ref = None
            if m is not None and m.compare_to:
                r = agg.get(m.compare_to, {}).get(name)
                ref = None if r is None else r[0]
            mark = bar_mark(m, mean, diverged, reference=ref, arm=arm) if m is not None else ""
            cells.append(fmt(mean, spread, n, diverged) + mark)
        rows.append(f"| {name} | " + " | ".join(cells) + " |")
    return rows


def build_state(config: Config, *, ledger: Optional[Ledger] = None,
                history_limit: Optional[int] = 5) -> StateView:
    """Read every spec, every run's status and every decision stub, and assemble the view.

    Every spec ends up in exactly one of the five lists, including the ones that have never been
    run: their status is worked out here anyway, and dropping them meant the JSON view of the
    project could not see a study until after somebody had started it.
    """
    from rl_researcher.kinds import load_kind

    ledger = ledger or open_ledger(config)
    view = StateView(project=config.name, generated=stamp_now())

    for spec_path in specs_in(config):
        try:
            kind_name = _kind_name(spec_path)
            kind = load_kind(config.kind_entry(kind_name))
            spec = kind.load(spec_path)
        except Exception as exc:  # noqa: BLE001 - one broken spec must not blank the page
            view.health.setdefault("unreadable_specs", []).append(f"{spec_path.name}: {exc}")
            continue
        out = config.out_root(kind_name) / spec.name
        st = run_status(spec, kind, out, stale_factor=float(config.watcher.stale_factor))
        summary_path = out / "results.json"
        artefact = out / "README.md"
        if st.finished and summary_path.is_file():
            body = _decision_region(artefact)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            reading = section(artefact.read_text(encoding="utf-8"), "Reading") if artefact.is_file() else None
            reading = re.sub(r"<!--.*?-->", "", reading or "", flags=re.S).strip()
            if "write here:" in reading[:30]:
                reading = ""
            from rl_researcher.presentation import hypothesis_view
            finding_copy = hypothesis_view(config.root, spec.name, spec.hypothesis)["finding_summary"]
            view.findings.append({"run": spec.name, "date": _finished_at(st),
                                  "summary": finding_copy or (reading.split("\n\n")[0] if reading else _outcome_line(spec, summary)),
                                  "source": "Report reading" if reading else "Registered outcome"})
            recorded = [r for r in ledger.query(kind="decision", run=spec.name)
                        if r.fingerprint == str(summary.get("fingerprint", ""))
                        and r.commit == str(summary.get("git_sha", ""))[:12]]
            if not recorded:
                view.waiting.append(Waiting(
                    run=spec.name, kind=kind_name,
                    artefact=_rel(config, artefact),
                    outcome=_outcome_line(spec, summary),
                    headline=_headline(spec, summary),
                    options=options(body),
                    finished=_finished_at(st),
                ))
            else:
                view.decided.append({"run": spec.name, "chose": recorded[-1].choices or ticked(body),
                                     "id": recorded[-1].id,
                                     "date": recorded[-1].date,
                                     "artefact": _rel(config, artefact)})
        elif st.state != "not started":
            view.running.append(_running_row(config, spec, st, out))
        else:
            from rl_researcher.gate import decide as gate_decide
            gate = gate_decide(spec, kind, config, out=out)
            view.ready.append({"run": spec.name, "kind": kind_name,
                               "spec": _rel(config, spec_path),
                               "units": len(st.units), "approval_needed": gate.gated and not gate.approved,
                               "wall_seconds": gate.cost.wall_seconds})

    plan = config.path("plan").resolve()
    view.context = {"goal": config.goal, "focus": config.focus,
                    "plan": plan.relative_to(config.root.resolve()).as_posix()
                    if plan.is_relative_to(config.root.resolve()) and plan.is_file() else ""}
    view.findings.sort(key=lambda item: item["date"], reverse=True)
    view.findings = view.findings[:3]
    view.queued = _queue(config)
    view.decided = _recent_decisions(ledger, view.decided, limit=history_limit)
    view.health.update(_health(config, ledger))
    return view


def _kind_name(spec_path: Path) -> str:
    from rl_researcher.spec import kind_of

    return kind_of(spec_path)


def _rel(config: Config, p: Path) -> str:
    try:
        return p.relative_to(config.root).as_posix()
    except ValueError:
        return p.as_posix()


def _finished_at(st: RunStatus) -> str:
    """When the last unit stopped beating, as a stamp a person can read.

    Every stamp is parsed before being compared, rather than compared as text. Units written
    before the ISO rule carry a `time.time()` float, and `max` over the *strings* both prints
    an epoch at the reader and orders "9..." above "10...".
    """
    stamps = [stamp_of(u.progress.get("updated")) for u in st.units]
    real = [s for s in stamps if s is not None]
    return max(real).isoformat(timespec="minutes") if real else ""


def _running_row(config: Config, spec: Any, st: RunStatus, out: Path) -> Dict[str, Any]:
    live = [u for u in st.units if u.live]
    ages = [u.age for u in live if u.age is not None]
    etas = [u.eta_seconds for u in live if u.eta_seconds is not None]
    attention_times = [stamp_of(u.progress.get("updated")) for u in st.units
                       if u.failed or u.stale or u.status in ("stopped", "incomplete")]
    known_times = [stamp for stamp in attention_times if stamp is not None]
    return {
        "run": spec.name,
        "state": st.state,
        "since": min(known_times).isoformat() if known_times else "",
        "done": st.done, "total": len(st.units),
        "heartbeat_age": _fmt_duration(min(ages) if ages else None),
        "eta": _fmt_duration(max(etas) if etas else None),
        "stale": [u.unit for u in st.units if u.stale],
        "failed": [u.unit for u in st.units if u.failed],
        "resumable": [u.unit for u in st.units if u.resumable and not u.done],
        "dashboard": _rel(config, out / "dashboard.html"),
    }


def _queue(config: Config) -> List[Dict[str, Any]]:
    p = config.path("queue")
    if not p.is_file():
        return []
    try:
        data = load_toml(p)
    except Exception as exc:  # noqa: BLE001
        return [{"run": f"(queue unreadable: {exc})", "hold": True}]
    return [dict(e) for e in data.get("entry", [])]


def _recent_decisions(ledger: Ledger, from_stubs: List[Dict[str, Any]],
                      limit: Optional[int] = 5) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = [
        {"run": r.run, "chose": r.choices or ([r.note] if r.note else []), "date": r.date, "id": r.id}
        for r in reversed(ledger.query(kind="decision"))]
    rows.sort(key=lambda r: str(r["date"]), reverse=True)
    current = {d["run"]: d for d in from_stubs}
    unique: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["run"], current.get(row["run"], row))
    for row in from_stubs:
        unique.setdefault(row["run"], row)
    return list(unique.values())[:limit]


def _health(config: Config, ledger: Ledger) -> Dict[str, Any]:
    from rl_researcher.canary import read_canary
    from rl_researcher.cost import read_throughput

    # Through the one reader, so the page and C09 cannot disagree about what an unreadable or
    # absent canary result means.
    canary = read_canary(config)
    watcher = {}
    tick = config.path("logs") / "watcher.json"
    if tick.is_file():
        try:
            watcher = json.loads(tick.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            watcher = {}
    rows = read_throughput(config)
    devices = sorted({str(r.get("device_name") or r.get("device")) for r in rows if r.get("device")})
    return {
        "findings": len(ledger.rows),
        "throughput_rows": len(rows),
        "throughput_devices": devices,
        "canary": canary,
        "watcher_last_tick": watcher.get("tick", ""),
        "approvals": sorted(p.name for p in config.path("approvals").glob("*.toml"))
        if config.path("approvals").is_dir() else [],
    }


# --------------------------------------------------------------------------- rendering


def build_artefact(view: StateView, state_root: str = "docs") -> Artefact:
    """The state page as an :class:`Artefact`, in the order its layout declares.

    Through the same writer every other artefact uses, rather than a list of strings assembled
    here. The `STATE` layout declared five regions that nothing emitted, so this page was the
    one document whose shape was never checked against its own kind.
    """
    art = Artefact(
        kind="state",
        title=f"{view.project}: state",
        header={"project": view.project, "generated": view.generated},
        preamble="_Generated by `python -m rl_researcher.state`. Nothing here is typed by hand; "
                 "to change it, tick a decision box, edit the queue, write an approval, or "
                 "start a run._")

    waiting: List[str] = []
    for w in view.waiting:
        waiting.append(f"### {w.run}\n\n**Registered outcome.** {w.outcome}")
        if w.headline:
            waiting.append("\n".join(w.headline))
        if w.options:
            waiting.append("**Options on the stub.** " + " · ".join(w.options))
        waiting.append(
            f"Finished {w.finished or 'at an unrecorded time'}. Read "
            f"[{w.artefact}]({_link(w.artefact, state_root)}); tick a box in its decision "
            f"region, then run `python -m rl_researcher.decide {w.run}`.")
    art.say("waiting", "\n\n".join(waiting) if waiting else "Nothing is waiting on a decision.")

    if view.running:
        art.add("running", UnitsTable(
            headers=("run", "units", "heartbeat", "eta", "trouble"),
            rows=[[r["run"], f"{r['done']}/{r['total']}", f"{r['heartbeat_age']} ago", r["eta"],
                   ", ".join((["stale: " + ", ".join(r["stale"])] if r["stale"] else [])
                             + (["FAILED: " + ", ".join(r["failed"])] if r["failed"] else []))
                   or "-"]
                  for r in view.running],
            tones=[["", "", "", "", "crit" if (r["stale"] or r["failed"]) else ""]
                   for r in view.running]))
    else:
        art.say("running", "Nothing is running.")

    if view.queued:
        art.add("queued", UnitsTable(
            headers=("run", "estimate", "gate", "approval", "hold"),
            rows=[[q.get("run", "?"), q.get("estimate", "-"), q.get("gate", "-"),
                   q.get("approval", "-"), "yes" if q.get("hold") else ""]
                  for q in view.queued]))
    else:
        art.say("queued", "The queue is empty.")

    if view.decided:
        art.add("decided", Notices(items=[
            ("muted", str(d["run"]),
             (", ".join(d.get("chose") or []) or "(no choice recorded)")
             + (f" [{d['id']}]" if d.get("id") else ""))
            for d in view.decided]))
    else:
        art.say("decided", "No decisions recorded yet.")

    h = view.health
    pairs = [
        ("Findings", f"{h.get('findings', 0)} on file"),
        ("Throughput", f"{h.get('throughput_rows', 0)} rows across "
                       f"{', '.join(h.get('throughput_devices') or ['no devices'])}"),
        ("Watcher", f"last tick {h.get('watcher_last_tick') or 'never (not installed)'}"),
        ("Canary", str(h.get("canary", {}).get("commit", "never run"))
                   + (f" ({h['canary'].get('date', '')})" if h.get("canary") else "")),
        ("Approvals", ", ".join(h.get("approvals") or ["none"])),
    ]
    art.add("health", KV(pairs=pairs))
    if h.get("unreadable_specs"):
        art.add("health", Notices(title="A spec cannot be read", items=[
            ("crit", "spec", str(bad)) for bad in h["unreadable_specs"]]))
    return art


def _link(rel: str, root: str = "docs") -> str:
    """A repo-relative path, rewritten relative to the directory the state page sits in.

    `[paths] state` is configurable and this assumed `docs/`, so a project that put the page
    anywhere else got links that resolved nowhere.
    """
    prefix = root.rstrip("/") + "/"
    return rel[len(prefix):] if rel.startswith(prefix) else "../" + rel


def write_state(config: Config, *, ledger: Optional[Ledger] = None) -> Path:
    """Write ``STATE.md``, ``state.json`` and ``state.html``; return the markdown path."""
    view = build_state(config, ledger=ledger)
    root = config.path("state")
    root.mkdir(parents=True, exist_ok=True)
    md_path = write(build_artefact(view, str(config.paths.state)), root / STATE_MD,
                    subtitle=f"generated {view.generated}", html_path=root / STATE_HTML)
    atomic.write_text(root / STATE_JSON, json.dumps(view.to_json(), indent=2, default=str))
    return md_path
