"""The findings ledger: every number this project has claimed, with where it came from.

The failure it exists to stop: knowledge gets retyped as prose. A result is measured, written
into a report, summarised into a plan, restated in a later plan amendment, and by the fourth
retelling the number has drifted, the caveat has been dropped, and nothing links back to the
run that produced it. `docs/PLAN.md` accumulating stacked amendment paragraphs is that failure
in its finished form.

So a number is written once, here, as a row with its provenance: which run, which unit, which
metric, measured by what, at which commit, on which data, under which budget, and which
decisions of the plan it bears on. Everything downstream cites the row's id. A page that states
a registered number and does not cite one is a lint error, not a style preference.

The file is `docs/ledger/findings.jsonl`, append-only. Nothing is ever edited or deleted: a
reading that turns out to be wrong is superseded by a new row that names the old one, and both
stay, because "we believed X until Y" is itself a finding. Regenerating a report re-derives the
same rows and writes none of them twice, because a registered row's identity is
``(run, unit, metric, fingerprint, commit)``.

Four kinds of row:

* ``registered`` - a pre-registered metric of one arm of one run. Written by the report writer.
* ``post-hoc``   - measured after seeing the result. Written by the measurement and diagnosis
  writers, and marked, always, because the distinction is the point of pre-registering.
* ``decision``   - what the human decided, from the ticked box and their notes.
* ``lesson``     - a rule that came out of an incident.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from rl_researcher import atomic
from rl_researcher.units import stamp_now

LEDGER_NAME = "findings.jsonl"
KINDS = ("registered", "post-hoc", "decision", "lesson")


class LedgerError(ValueError):
    pass


def touches_match(touches: Sequence[str], key: str) -> bool:
    """Does one of ``touches`` name the decision ``key``?

    A spec writes the decision the way a person says it, with the label and then what it is
    about: ``"D4 (reconstruction-free representation)"``. A query asks for ``D4``. So the label
    matches when it is the whole entry or is followed by something that is not part of a label,
    which is what keeps ``D1`` from matching ``D10 (masked-latent objective)``.
    """
    for t in touches:
        if t == key:
            return True
        if t.startswith(key) and not t[len(key)].isalnum():
            return True
    return False


@dataclass
class Finding:
    """One claim. Every field is either provenance or the claim itself; there is no commentary
    field beyond ``note``, because commentary belongs in an artefact that cites the id."""

    id: str = ""
    kind: str = "registered"
    date: str = ""
    run: str = ""
    unit: str = ""                      # the arm, or "<arm>/seed<N>" for a single-seed claim
    metric: str = ""
    value: Optional[float] = None
    spread: Optional[float] = None      # standard deviation across seeds
    n: int = 0
    diverged: int = 0
    bar: Optional[float] = None
    direction: str = "report"
    compare_to: Optional[str] = None    # the arm this was judged against, if any
    reference: Optional[float] = None   # that arm's value at the time
    passed: Optional[bool] = None       # None = nothing to pass
    estimator: str = ""                 # the function that computed it
    commit: str = ""
    fingerprint: str = ""
    data: str = ""                      # "<snapshot>@<digest[:8]>"
    budget: str = ""
    artefact: str = ""                  # repo-relative path to the page that states it
    touches: List[str] = field(default_factory=list)     # plan decisions, e.g. ["D4", "§8.4"]
    supersedes: List[str] = field(default_factory=list)
    note: str = ""

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        # `pass` is the word this reads as everywhere else; it is only `passed` in Python
        # because `pass` is a keyword.
        d["pass"] = d.pop("passed")
        return d

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "Finding":
        d = dict(d)
        d["passed"] = d.pop("pass", d.pop("passed", None))
        known = {f for f in Finding.__dataclass_fields__}
        return Finding(**{k: v for k, v in d.items() if k in known})

    @property
    def identity(self) -> "tuple[str, str, str, str, str, str]":
        """What makes two rows the same claim. Regenerating a report produces rows with this
        identity already present, and they are not written again."""
        return (self.kind, self.run, self.unit, self.metric, self.fingerprint, self.commit)

    def line(self) -> str:
        """One chronological line, the form a PLAN evidence region is made of."""
        bits = [self.date, self.run or "-"]
        if self.unit:
            bits.append(self.unit)
        val = "" if self.value is None else f"{self.value:.3f}"
        if self.spread is not None and self.n > 1:
            val += f" ± {self.spread:.3f}"
        if self.n:
            val += f" (n={self.n})"
        mark = "" if self.passed is None else (" ✓" if self.passed else " ✗")
        bits.append(f"{self.metric} {val}{mark}".strip() if self.metric else self.note)
        text = " · ".join(b for b in bits if b)
        text += f" [{self.id}]"
        if self.supersedes:
            text += "; supersedes " + ", ".join(f"[{s}]" for s in self.supersedes)
        return text


class Ledger:
    """The rows on disk, read once and appended to."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._rows: Optional[List[Finding]] = None

    # ---------------------------------------------------------------- reading

    @property
    def rows(self) -> List[Finding]:
        if self._rows is None:
            self._rows = self._read()
        return self._rows

    def _read(self) -> List[Finding]:
        if not self.path.is_file():
            return []
        out: List[Finding] = []
        for i, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Finding.from_json(json.loads(line)))
            except (ValueError, TypeError) as exc:
                raise LedgerError(f"{self.path}:{i}: not a finding: {exc}") from None
        return out

    def by_id(self, fid: str) -> Optional[Finding]:
        return next((r for r in self.rows if r.id == fid), None)

    @property
    def superseded(self) -> "set[str]":
        """Ids that a later row has replaced. They are still shown, struck through: a claim
        that was believed and then withdrawn is part of the record."""
        out: set = set()
        for r in self.rows:
            out.update(r.supersedes)
        return out

    def query(self, *, touches: Optional[str] = None, metric: Optional[str] = None,
              run: Optional[str] = None, data: Optional[str] = None, kind: Optional[str] = None,
              unit: Optional[str] = None, include_superseded: bool = True) -> List[Finding]:
        """The rows a hypothesis cites. Filters are ANDed; ``touches`` matches any one of a
        row's decisions."""
        out = list(self.rows)
        if touches is not None:
            out = [r for r in out if touches_match(r.touches, touches)]
        if metric is not None:
            out = [r for r in out if r.metric == metric]
        if run is not None:
            out = [r for r in out if r.run == run]
        if data is not None:
            out = [r for r in out if r.data == data or r.data.startswith(f"{data}@")]
        if kind is not None:
            out = [r for r in out if r.kind == kind]
        if unit is not None:
            out = [r for r in out if r.unit == unit]
        if not include_superseded:
            gone = self.superseded
            out = [r for r in out if r.id not in gone]
        return out

    def mixed(self, rows: Sequence[Finding]) -> List[str]:
        """Why a table of these rows needs a banner over it: they are not all comparable.

        Two numbers measured on different data, or under different step budgets, do not belong
        in one column without being told apart. The report says so rather than quietly putting
        them side by side.
        """
        notes = []
        for label, values in (("data", {r.data for r in rows if r.data}),
                              ("budget", {r.budget for r in rows if r.budget}),
                              ("commit", {r.commit for r in rows if r.commit})):
            if len(values) > 1 and label != "commit":
                notes.append(f"these rows span {len(values)} different {label} values "
                             f"({', '.join(sorted(values))}); they are not directly comparable")
        return notes

    # ---------------------------------------------------------------- writing

    def next_id(self) -> str:
        highest = 0
        for r in self.rows:
            if r.id.startswith("F") and r.id[1:].isdigit():
                highest = max(highest, int(r.id[1:]))
        return f"F{highest + 1:04d}"

    def add(self, finding: Finding) -> Finding:
        """Append one row, giving it an id and a date if it has none. A row whose identity is
        already on file is returned unchanged and not written again."""
        if finding.kind not in KINDS:
            raise LedgerError(f"unknown finding kind {finding.kind!r}; known: {list(KINDS)}")
        existing = next((r for r in self.rows if r.identity == finding.identity), None)
        if existing is not None:
            return existing
        if not finding.id:
            finding.id = self.next_id()
        if not finding.date:
            finding.date = stamp_now()[:10]
        for old in finding.supersedes:
            if self.by_id(old) is None:
                raise LedgerError(f"{finding.id} supersedes {old}, which is not in the ledger")
        self.rows.append(finding)
        self._append_line(finding)
        return finding

    def extend(self, findings: Iterable[Finding]) -> List[Finding]:
        return [self.add(f) for f in findings]

    def supersede(self, old_id: str, finding: Finding) -> Finding:
        """Record a corrected claim without deleting the wrong one.

        The wrong number stays visible and struck through. A ledger that quietly loses its
        mistakes cannot be used to check whether a plan was written on good evidence, which is
        the only reason to keep one.
        """
        if self.by_id(old_id) is None:
            raise LedgerError(f"cannot supersede {old_id}: no such finding")
        if old_id not in finding.supersedes:
            finding.supersedes = [*finding.supersedes, old_id]
        return self.add(finding)

    def _append_line(self, finding: Finding) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(finding.to_json(), default=str) + "\n")
            fh.flush()

    def rewrite(self) -> None:
        """Write every row back, atomically. Only for a migration; ordinary use appends."""
        text = "".join(json.dumps(r.to_json(), default=str) + "\n" for r in self.rows)
        atomic.write_text(self.path, text)


def open_ledger(config: Any) -> Ledger:
    return Ledger(Path(config.path("ledger")) / LEDGER_NAME)


def findings_from_summary(summary: Dict[str, Any], spec: Any, *, artefact: str = "",
                          estimator: str = "", data: str = "", date: str = "") -> List[Finding]:
    """One ``registered`` row per (arm, metric) of a finished run.

    The value is the mean over the arm's seeds and the spread is their standard deviation, both
    from :func:`rl_researcher.artefacts.report.aggregate`, so the ledger and the report's
    headline table cannot disagree: they are the same call.
    """
    from rl_researcher.artefacts.report import aggregate, judge

    runs = summary.get("runs") or []
    names = [m.name for m in spec.metrics]
    agg = aggregate(runs, names)
    budget = summary.get("budget") or {}
    budget_text = f"{budget.get('max_steps', '?')} steps"
    out: List[Finding] = []
    for arm, per_metric in agg.items():
        for m in spec.metrics:
            mean, spread, n, diverged = per_metric.get(m.name, (float("nan"), float("nan"), 0, 0))
            reference = None
            if m.compare_to:
                ref_stats = agg.get(m.compare_to, {}).get(m.name)
                reference = None if ref_stats is None else ref_stats[0]
            out.append(Finding(
                kind="registered",
                date=date,
                run=str(summary.get("run") or summary.get("study") or spec.name),
                unit=arm, metric=m.name,
                value=None if mean != mean else round(float(mean), 6),
                spread=None if spread != spread else round(float(spread), 6),
                n=int(n), diverged=int(diverged),
                bar=m.bar, direction=m.direction,
                compare_to=m.compare_to,
                reference=None if reference is None else round(float(reference), 6),
                # Judged through the same rule the report prints, and with the arm, so the
                # reference of a comparison is not recorded as having failed to beat itself.
                passed=judge(m, mean, diverged, reference=reference, arm=arm),
                estimator=estimator,
                commit=str(summary.get("git_sha", ""))[:12],
                fingerprint=str(summary.get("fingerprint", "")),
                data=data or _data_tag(summary),
                budget=budget_text,
                artefact=artefact,
                touches=list(getattr(spec, "decision_touches", []) or []),
            ))
    return out


def _data_tag(summary: Dict[str, Any]) -> str:
    """``"<snapshot dir name>@<digest[:8]>"``: enough to tell two studies apart, short enough
    to sit in a table cell."""
    snap = summary.get("snapshot") or {}
    name = str(snap.get("dir", "")).rstrip("/").rsplit("/", 1)[-1]
    digest = str(snap.get("sha256", ""))[:8]
    if not name:
        return ""
    return f"{name}@{digest}" if digest else name
