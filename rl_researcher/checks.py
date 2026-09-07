"""The checks: one question each, asked at the stage where the answer can still change a plan.

Every check here is a lesson that cost this project something. The lesson is named on the check,
and `rl_researcher/lessons.md` names the check back, so neither list can drift from the other
without a test noticing — that is what C11 is for.

A check is not a gate on quality. It is a gate on the small number of things that make a run
*worthless*: a bar nothing can reach, a comparison nothing can win, one seed read as a
difference, a number with no estimator. Each of those has happened, and each cost a night of
compute or a headline that did not replicate.

Stages, and why each check is asked where it is:

    load    the spec is being read; the answer is in the spec alone
    check   before anything expensive; `check` reports, `run` refuses on an error
    pin     the data is being hashed, which is the last moment before a registration is fixed
    run     the machine is about to start; the question is about this machine, now
    lint    a document is on disk and can be compared with what would be generated
    ci      nothing to do with a run: the tooling checking itself

An error refuses; a warn is printed and passes. The rule for choosing: error if acting on the
finding after the run would mean discarding the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from rl_researcher.kinds import Finding, RunKind
from rl_researcher.spec import RunSpec

STAGES = ("load", "check", "pin", "run", "lint", "ci")


@dataclass
class Context:
    """What a check may look at. Anything not here is something a check has no business
    reading — a check that boots an engine or trains a model is not a check."""

    spec: Optional[RunSpec] = None
    kind: Optional[RunKind] = None
    config: Any = None
    out: Optional[Path] = None
    stage: str = "check"
    root: Optional[Path] = None          # the project root, for the ci and lint stages

    def path(self, key: str) -> Optional[Path]:
        try:
            return Path(self.config.path(key))
        except Exception:  # noqa: BLE001 - a check without a config is a check that says nothing
            return None


CheckFn = Callable[[Context], Iterable[Finding]]


@dataclass(frozen=True)
class Check:
    id: str
    stage: str
    lesson: str          # the id in rl_researcher/lessons.md this exists because of
    what: str            # one line, in the terms a person would use
    fn: CheckFn


REGISTRY: Dict[str, Check] = {}


def check(id: str, *, stage: str, lesson: str, what: str) -> Callable[[CheckFn], CheckFn]:
    """Register a check. Two with one id is a mistake worth failing at import."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; known: {STAGES}")

    def wrap(fn: CheckFn) -> CheckFn:
        if id in REGISTRY:
            raise ValueError(f"check {id} is registered twice")
        REGISTRY[id] = Check(id=id, stage=stage, lesson=lesson, what=what, fn=fn)
        return fn

    return wrap


def run_checks(stage: str, spec: Optional[RunSpec] = None, kind: Optional[RunKind] = None,
               config: Any = None, out: Optional[Path] = None,
               root: Optional[Path] = None) -> List[Finding]:
    """Every check registered for a stage, in id order.

    A check that raises is reported as a warn rather than propagating. A broken check must not
    be able to refuse a run: that turns a bug in the tooling into a night of lost compute, which
    is the exact failure the checks exist to prevent.
    """
    ctx = Context(spec=spec, kind=kind, config=config, out=out, stage=stage, root=root)
    found: List[Finding] = []
    for c in sorted((c for c in REGISTRY.values() if c.stage == stage), key=lambda c: c.id):
        try:
            found += list(c.fn(ctx) or [])
        except Exception as exc:  # noqa: BLE001 - see the docstring
            found.append(Finding(check=c.id, level="warn",
                                 message=f"the check itself failed: {type(exc).__name__}: {exc}"))
    return found


def _err(c: str, message: str) -> Finding:
    return Finding(check=c, level="error", message=message)


def _warn(c: str, message: str) -> Finding:
    return Finding(check=c, level="warn", message=message)


# ── available with nothing but the spec ───────────────────────────────────────────────────

@check("C08", stage="load", lesson="L008",
       what="the heartbeat and checkpoint cadence are set, and inside bounds")
def c08_cadence(ctx: Context) -> Iterable[Finding]:
    """A run with no heartbeat is a silent run, and a run with no checkpoint cannot be stopped.
    Both were the study spec's own validation; every kind is under the same rule now."""
    if ctx.spec is None:
        return []
    cad = ctx.spec.cadence
    out: List[Finding] = []
    if cad.heartbeat_seconds <= 0 or cad.heartbeat_seconds > 60:
        out.append(_err("C08", f"heartbeat every {cad.heartbeat_seconds:g}s; a run is never "
                               f"silent for more than a minute"))
    if cad.checkpoint_every_steps <= 0 and cad.checkpoint_seconds <= 0:
        out.append(_err("C08", "no checkpoint cadence; this run could not be hot-stopped"))
    budget = ctx.spec.budget.max_seconds
    if cad.checkpoint_seconds > 0 and budget > 0 and cad.checkpoint_seconds > budget / 4:
        out.append(_warn("C08", f"checkpoint every {cad.checkpoint_seconds:.0f}s against a "
                                f"{budget:.0f}s budget; a stop could cost a quarter of the run"))
    return out


@check("C13", stage="load", lesson="L013",
       what="every registered metric has a definition and a reason")
def c13_metrics_are_defined(ctx: Context) -> Iterable[Finding]:
    """A metric with no definition renders as its own variable name; a metric with no `why` is
    one nobody has said out loud why they are measuring, which is how a metric list grows."""
    if ctx.spec is None or ctx.kind is None:
        return []
    registry = getattr(ctx.kind, "registry", None)
    out: List[Finding] = []
    for m in ctx.spec.metrics:
        if registry is not None and m.name not in registry:
            out.append(_err("C13", f"{m.name} is not in {ctx.kind.name}'s registry, so nothing "
                                   f"can say what it means"))
        elif not (m.why or "").strip():
            out.append(_warn("C13", f"{m.name} has no `why`; the report will show the metric "
                                    f"with no reason it was registered"))
    return out


@check("C06", stage="run", lesson="L006",
       what="no path under a live run's directory is staged for commit")
def c06_not_committing_a_live_run(ctx: Context) -> Iterable[Finding]:
    """Committing a run's outputs while it is running captures a half-written state and, worse,
    invites a `git add -A` that stages a lock file. The pre-commit hook asks the same question;
    this is the copy the runner asks, because the hook is not installed everywhere."""
    from rl_researcher.lock import lock_holder

    out = ctx.out
    if out is None or not Path(out).is_dir():
        return []
    holder = lock_holder(Path(out))
    if holder and staged_under(Path(out), ctx.root):
        return [_err("C06", f"{out} is locked by {holder} and has staged changes; committing a "
                            f"run's outputs while it runs captures a half-written state")]
    return []


def staged_under(path: Path, root: Optional[Path] = None) -> List[str]:
    """Paths staged for commit under ``path``. Empty when git is not there to ask."""
    import subprocess

    try:
        done = subprocess.run(["git", "diff", "--cached", "--name-only"],
                              cwd=str(root or path), capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    here = Path(path).resolve()
    staged = []
    for line in done.stdout.splitlines():
        try:
            if (Path(root or path).resolve() / line).resolve().is_relative_to(here):
                staged.append(line)
        except (OSError, ValueError):
            continue
    return staged


# ── available with the spec and the ledger ────────────────────────────────────────────────

@check("C05", stage="check", lesson="L005",
       what="three seeds, unless the spec says it is a screening pass")
def c05_seeds(ctx: Context) -> Iterable[Finding]:
    """A single seed is not a difference. A screening pass may have one, and its report says so
    at the top; what this refuses is one seed with nothing declaring it a screen."""
    if ctx.spec is None:
        return []
    if not getattr(ctx.kind, "compares_seeds", True):
        return []                       # deterministic given its data; there is no spread
    screening_seeds = int(getattr(getattr(ctx.config, "gate", None), "screening_seeds", 1) or 1)
    want = screening_seeds if getattr(ctx.spec, "screening", False) else 3
    n = len(ctx.spec.seeds)
    if getattr(ctx.spec, "screening", False):
        return ([] if n >= want else
                [_err("C05", f"a screening pass still needs {want} seed(s); this has {n}")])
    if n < want:
        return [_err("C05", f"{n} seed(s) and `screening` is not set. One seed read as a "
                            f"difference is the commonest way a study says nothing; declare "
                            f"`screening = true` if that is what this is")]
    return []


@check("C02", stage="check", lesson="L002",
       what="a compare_to metric can move in the direction it registered")
def c02_comparison_can_be_won(ctx: Context) -> Iterable[Finding]:
    """Phase 4 registered `parked_fraction` against the random arm; every arm scored 0.000 and
    the three treatment arms were marked ✗ for not being *strictly* below zero. A comparison
    nothing can win discriminates as little as a bar nothing can reach, and marking the tie as
    a failure is worse than no mark, because it reads as evidence against the treatment."""
    if ctx.spec is None or ctx.kind is None:
        return []
    arms = set(_arms(ctx.kind, ctx.spec))
    out: List[Finding] = []
    for m in ctx.spec.metrics:
        ref = getattr(m, "compare_to", None)
        if not ref:
            continue
        if ref not in arms:
            out.append(_err("C02", f"{m.name} compares against `{ref}`, which is not an arm of "
                                   f"this run ({', '.join(sorted(arms)) or 'none'})"))
        elif m.direction == "report":
            out.append(_err("C02", f"{m.name} names a reference arm but registers no direction, "
                                   f"so no arm can win or lose the comparison"))
        elif not (getattr(m, "baseline", "") or "").strip():
            out.append(_warn("C02", f"{m.name} compares against `{ref}` without saying what "
                                    f"`{ref}` is expected to score. Registering a comparison "
                                    f"means asking whether a difference is possible from there"))
    return out


@check("C04", stage="check", lesson="L004",
       what="an arm declared as the anchor of an earlier cell is in this spec")
def c04_anchor_is_present(ctx: Context) -> Iterable[Finding]:
    """The run-to-run spread on the action metrics is two to three times the seed spread, so a
    new arm compared against an old study's number is compared against noise. Re-run the anchor
    inside the new study; this checks that the spec actually contains it."""
    if ctx.spec is None or ctx.kind is None:
        return []
    arms = set(_arms(ctx.kind, ctx.spec))
    out = []
    for m in ctx.spec.metrics:
        anchor = getattr(m, "anchor_of", None)
        if not anchor:
            continue
        arm = str(anchor).split("/")[-1]
        if arm not in arms:
            out.append(_err("C04", f"{m.name} anchors on `{anchor}` but `{arm}` is not an arm "
                                   f"here. An anchor compared across studies is compared "
                                   f"against a number this run cannot reproduce"))
    return out


@check("C03", stage="check", lesson="L003",
       what="a bar is at or under what the data has been measured to carry")
def c03_bar_is_reachable(ctx: Context) -> Iterable[Finding]:
    """Six cells failing a bar by the same distance discriminate nothing. Where an instrument
    measurement exists for a metric, its ledger rows say what has been reached; a bar above all
    of them is a bar the run cannot answer."""
    if ctx.spec is None or ctx.kind is None or ctx.config is None:
        return []
    reached = _instrument_ceilings(ctx)
    out = []
    for m in ctx.spec.metrics:
        if m.bar is None or m.direction != "higher":
            continue
        best = reached.get(m.name)
        if best is not None and best < m.bar:
            out.append(_err("C03", f"{m.name} registers a bar of {m.bar:g}, and the instrument "
                                   f"measurement for it has never seen above {best:g}. Every "
                                   f"arm would fail by about the same distance"))
    return out


@check("C01", stage="pin", lesson="L001",
       what="an instrument measurement for the primary metric exists on this data")
def c01_instrument_exists(ctx: Context) -> Iterable[Finding]:
    """Before a study can register a metric about the action, something has to say the action is
    visible in the frames; before one about effective rank, something has to say the rank is
    reachable. `pin` is where this is asked, because pinning is the last moment before a
    registration is fixed."""
    if ctx.spec is None or ctx.kind is None or ctx.config is None:
        return []
    instrument_for = getattr(ctx.kind, "instrument_for", None)
    if not callable(instrument_for):
        return []
    rows = _ledger_rows(ctx)
    have = {str(r.run) for r in rows}
    out = []
    for m in ctx.spec.metrics:
        if m.bar is None:
            continue
        want = instrument_for(m.name)
        if want and want not in have:
            out.append(_warn("C01", f"{m.name} registers a bar, and `{want}` — the measurement "
                                    f"that says whether it is reachable on this data — has no "
                                    f"ledger row. Run it before pinning"))
    return out


@check("C09", stage="run", lesson="L009",
       what="the canary passed at a commit no older than the last change to the runner")
def c09_canary_is_fresh(ctx: Context) -> Iterable[Finding]:
    """A gated run is one worth hours. The canary is what says the machinery still works, and a
    canary from before the last change to the machinery says nothing about it."""
    import json

    if ctx.config is None or ctx.spec is None:
        return []
    watched = list(getattr(getattr(ctx.config, "canary", None), "watched", []) or [])
    canary_name = getattr(getattr(ctx.config, "canary", None), "spec", None)
    if not canary_name or ctx.spec.name == canary_name:
        return []
    path = ctx.path("ledger")
    blob: Dict[str, Any] = {}
    if path is not None and (path / "canary.json").is_file():
        try:
            blob = json.loads((path / "canary.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            blob = {}
    if not blob.get("commit"):
        return [_warn("C09", f"no canary result on file; `{canary_name}` is what says the "
                             f"machinery still works before an expensive run")]
    stale = _changed_since(ctx, str(blob["commit"]), watched)
    if stale:
        return [_warn("C09", f"the canary last passed at {str(blob['commit'])[:12]}, and "
                             f"{', '.join(stale)} changed after it")]
    return []


# ── available once a document is on disk ──────────────────────────────────────────────────

@check("C07", stage="lint", lesson="L007",
       what="a number in an authored region cites the ledger id it came from")
def c07_authored_numbers_cite_a_finding(ctx: Context) -> Iterable[Finding]:
    """A number in a reading with no id is a number nobody can trace to an estimator. The rule
    is narrow on purpose: it fires on a line that names a registered metric *and* carries a
    decimal, which is what a claim looks like."""
    from rl_researcher.regions import find

    out: List[Finding] = []
    for path, text in _documents(ctx):
        names = _metric_names(text)
        if not names:
            continue
        for region in find(text):
            if region.kind != "authored":
                continue
            for i, line in enumerate(region.body.splitlines(), 1):
                if not re.search(r"\d+\.\d+", line):
                    continue
                if not any(n in line for n in names):
                    continue
                if re.search(r"\[F\d{3,}\]", line):
                    continue
                out.append(_warn("C07", f"{path.name}:{region.arg} line {i} states a number "
                                        f"about a registered metric with no `[F####]`: "
                                        f"{line.strip()[:90]}"))
    return out


@check("C12", stage="lint", lesson="L012",
       what="a diagnosis carries its post-hoc banner")
def c12_diagnosis_is_labelled(ctx: Context) -> Iterable[Finding]:
    """A document written after seeing a result and laid out like one written before it is the
    single most misleading artefact this project can produce."""
    out: List[Finding] = []
    for path, text in _documents(ctx):
        if "kind=diagnosis" not in text.split("\n", 1)[0]:
            continue
        if "post-hoc" not in text.lower():
            out.append(_err("C12", f"{path.name} is a diagnosis and never says `post-hoc`. It "
                                   f"was written after seeing the result it is about"))
    return out


# ── the tooling checking itself ───────────────────────────────────────────────────────────

INVARIANTS_MARK = "<!-- invariants -->"


@check("C10", stage="ci", lesson="L010",
       what="the invariants block is byte-identical across the skill files")
def c10_invariants_agree(ctx: Context) -> Iterable[Finding]:
    """Four skills, one set of hard rules. Four copies of a rule is four rules, and the one
    that drifts is the one being read when it matters."""
    found = _skill_files(ctx)
    if not found:
        # An empty set compares equal to itself, so this used to pass loudest of all: installed
        # from a wheel that shipped no skills, the one check saying four copies of a rule had
        # not drifted was answering a question it had never asked.
        return [_err("C10", "no shipped skill files found, so nothing checked that the four "
                            "invariants blocks still agree. They ship inside the package; a "
                            "tree or an install without them cannot ask this")]
    blocks = {}
    for path in found:
        text = path.read_text(encoding="utf-8", errors="replace")
        parts = text.split(INVARIANTS_MARK)
        # Keyed by the directory, because every one of these files is called SKILL.md — keying
        # by the filename collapsed four skills into one entry and the check never fired.
        name = path.parent.name
        if len(parts) < 3:
            return [_err("C10", f"{name} has no `{INVARIANTS_MARK}` block")]
        blocks[name] = parts[1]
    if len(set(blocks.values())) > 1:
        odd = sorted(blocks)
        return [_err("C10", f"the invariants block differs across {', '.join(odd)}; four copies "
                            f"of a rule is four rules")]
    return []


@check("C11", stage="ci", lesson="L011",
       what="every check names a lesson that exists, and every lesson its check")
def c11_checks_and_lessons_agree(ctx: Context) -> Iterable[Finding]:
    """The two lists are each other's index. A check whose lesson was deleted is a rule with no
    reason on file, and a lesson naming a check that does not exist is a promise the tooling
    does not keep."""
    # The checks and the lessons are the package's own, so the package's copy is the one that
    # has to agree with them. A project may keep its own file too; this asks about this list.
    path = _lessons_file(ctx)
    if path is None:
        return [_warn("C11", "no lessons file; every check here exists because something went "
                             "wrong once, and nothing records what")]
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    have = set(re.findall(r"^### (L\d{3})", text, flags=re.M))
    named = set(re.findall(r"Check: (C\d{2})", text))
    out = []
    for c in sorted(REGISTRY.values(), key=lambda c: c.id):
        if c.lesson not in have:
            out.append(_err("C11", f"{c.id} names lesson {c.lesson}, which is not in "
                                   f"{Path(path).name}"))
    for cid in sorted(named - set(REGISTRY)):
        out.append(_err("C11", f"{Path(path).name} names check {cid}, which is not registered"))
    return out


# ── the small amount of reading the checks share ──────────────────────────────────────────

def _arms(kind: RunKind, spec: RunSpec) -> Sequence[str]:
    try:
        return list(kind.arms(spec))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - a kind that cannot list its arms answers no arms
        return []


def _ledger_rows(ctx: Context) -> List[Any]:
    from rl_researcher.ledger import open_ledger

    try:
        return list(open_ledger(ctx.config).rows)
    except Exception:  # noqa: BLE001 - no ledger is no evidence, not a failure
        return []


def _instrument_ceilings(ctx: Context) -> Dict[str, float]:
    """The largest value each metric's instrument measurement has ever recorded."""
    instrument_for = getattr(ctx.kind, "instrument_for", None)
    if not callable(instrument_for):
        return {}
    wanted = {m.name: instrument_for(m.name) for m in (ctx.spec.metrics if ctx.spec else [])}
    best: Dict[str, float] = {}
    for row in _ledger_rows(ctx):
        for name, run in wanted.items():
            if not run or str(row.run) != str(run) or row.value is None:
                continue
            if str(row.metric) in (name, f"best_{name}", "best_trained"):
                best[name] = max(best.get(name, float("-inf")), float(row.value))
    return best


def _documents(ctx: Context) -> List[Any]:
    """Every generated document under the project's output roots, with its text."""
    roots: List[Path] = []
    if ctx.out is not None:
        roots.append(Path(ctx.out))
    elif ctx.config is not None:
        for key in ("state", "diagnoses"):
            got = ctx.path(key)
            if got is not None:
                roots.append(got)
        for kind in getattr(ctx.config, "out", {}) or {}:
            try:
                roots.append(Path(ctx.config.out_root(kind)))
            except Exception:  # noqa: BLE001
                continue
    seen: Dict[Path, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if path in seen:
                continue
            try:
                seen[path] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return list(seen.items())


def _metric_names(text: str) -> List[str]:
    """The registered metric names this document itself lists, from its generated table."""
    body = text.split("<!-- generated: metrics -->", 1)
    if len(body) < 2:
        return []
    rows = body[1].split("<!-- /generated -->", 1)[0]
    return sorted({m for m in re.findall(r"^\| ([a-z][a-z0-9_]{2,}) \|", rows, flags=re.M)})


def _lessons_file(ctx: Context) -> Optional[Path]:
    """The lessons the registry's ids point into: the package's, or a project's if it has one
    that carries them."""
    here = Path(__file__).resolve().parent / "lessons.md"
    if here.is_file():
        return here
    got = ctx.path("lessons") if ctx.config is not None else None
    return got if got is not None and Path(got).is_file() else None


#: Only the skills this package ships carry the invariants block. A project may keep its own
#: skill beside them -- Auto-SM64 has one for its instruments and its engine -- and that one
#: extends the four rather than repeating their rules, so it is not asked for a copy.
SHIPPED_PREFIX = "rl-"


def _skill_files(ctx: Context) -> List[Path]:
    """Every copy of a shipped skill this project can see: its own, its installed ones, and the
    package's. All of them, because the one being read is whichever is installed."""
    roots = [Path(__file__).resolve().parent / "skills"]
    if ctx.root:
        roots = [Path(ctx.root) / "skills", Path(ctx.root) / ".claude" / "skills"] + roots
    found: List[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        found += [p for p in sorted(root.rglob("SKILL.md"))
                  if p.parent.name.startswith(SHIPPED_PREFIX)]
        if found:
            break                       # the project's own copies are the ones being read
    return found


def _changed_since(ctx: Context, commit: str, watched: Sequence[str]) -> List[str]:
    """Which watched paths changed after a commit. Empty when git cannot answer."""
    import subprocess

    if not watched:
        return []
    root = Path(ctx.root) if ctx.root else Path(getattr(ctx.config, "root", "."))
    try:
        done = subprocess.run(["git", "diff", "--name-only", commit, "--", *watched],
                              cwd=str(root), capture_output=True, text=True, timeout=15,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    return sorted({line.split("/")[-1] for line in done.stdout.splitlines() if line.strip()})


def catalogue() -> List[Check]:
    """Every registered check, in id order. What `lint --list` and the docs read."""
    return sorted(REGISTRY.values(), key=lambda c: c.id)
