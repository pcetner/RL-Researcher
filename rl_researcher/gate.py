"""The human gate: a run over the cost line needs an approval bound to its fingerprint.

The line is in the project's ``rl-researcher.toml`` ``[gate]`` table, in wall-clock minutes
and dollars so it means the same on every machine. Below it the LLM proceeds. Above it
``run`` refuses (exit 3) unless ``<approvals>/<run>.<fingerprint>.toml`` exists. The approval
is written after the human says yes, quoting what they said; editing the spec changes the
fingerprint and voids it without any further rule.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rl_researcher.config import Config
from rl_researcher.cost import Cost, estimate
from rl_researcher.kinds import DeviceInfo, RunKind
from rl_researcher.spec import RunSpec, load_toml, spec_fingerprint


class GateRefused(RuntimeError):
    exit_code = 3


@dataclass
class GateDecision:
    cost: Cost
    gated: bool
    approved: bool
    approval_path: Optional[Path] = None
    approval: Optional[Dict[str, Any]] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def may_run(self) -> bool:
        return not self.gated or self.approved


def assess(cost: Cost, config: Config) -> Cost:
    """Mark ``cost`` as gated or not, with the reasons, against the project's thresholds."""
    g = config.gate
    reasons: List[str] = []
    if cost.basis == "nothing-to-run":
        cost.gated, cost.reasons = False, []
        return cost
    if cost.wall_seconds > g.ungated_wall_minutes * 60:
        reasons.append(f"estimated {cost.wall_seconds / 60:.0f} min of wall time is over the "
                       f"{g.ungated_wall_minutes:.0f} min line")
    if cost.money_usd > g.ungated_money_usd:
        reasons.append(f"estimated ${cost.money_usd:.2f} is over the ${g.ungated_money_usd:.2f} line")
    if cost.new_data_minutes > g.ungated_new_data_minutes:
        reasons.append(f"{cost.new_data_minutes:.0f} min of new data collection is over the "
                       f"{g.ungated_new_data_minutes:.0f} min line")
    if cost.device.kind in g.always_gated:
        reasons.append(f"device kind {cost.device.kind!r} is always gated")
    for tag in cost.tags:
        if tag in g.always_gated:
            reasons.append(f"{tag!r} runs are always gated")
    if cost.basis == "budget-cap" and cost.units and g.unknown_device == "gate" \
            and cost.wall_seconds > g.ungated_wall_minutes * 60:
        reasons.append("no throughput record for this device; the estimate is the budget cap "
                       "(run the canary on this device to calibrate)")
    cost.gated, cost.reasons = bool(reasons), reasons
    return cost


def approvals_dir(config: Config) -> Path:
    return config.path("approvals")


def approval_path(config: Config, spec: RunSpec) -> Path:
    return approvals_dir(config) / f"{spec.name}.{spec_fingerprint(spec)}.toml"


def find_approval(config: Config, spec: RunSpec) -> Optional[Dict[str, Any]]:
    p = approval_path(config, spec)
    if not p.is_file():
        return None
    try:
        d = load_toml(p)
    except Exception:  # noqa: BLE001 - an unreadable approval is no approval
        return None
    return d if d.get("fingerprint") == spec_fingerprint(spec) else None


def _toml_str(s: Any) -> str:
    return json.dumps(str(s))  # a JSON string is a valid TOML basic string


def write_approval(config: Config, spec: RunSpec, cost: Cost, *, quote: str, session: str = "",
                   approved_by: str = "the human, in chat", note: str = "") -> Path:
    """Record that the human approved this exact spec. Written only after an explicit yes."""
    p = approval_path(config, spec)
    p.parent.mkdir(parents=True, exist_ok=True)
    from rl_researcher.runner import git_sha

    lines = [
        f"run = {_toml_str(spec.name)}",
        f"fingerprint = {_toml_str(spec_fingerprint(spec))}",
        f"approved_at = {_toml_str(datetime.now(timezone.utc).isoformat(timespec='seconds'))}",
        f"approved_by = {_toml_str(approved_by)}",
        f"quote = {_toml_str(quote)}",
        f"session = {_toml_str(session)}",
        f"os_user = {_toml_str(os.environ.get('USERNAME') or os.environ.get('USER') or '')}",
        f"git_sha = {_toml_str(git_sha())}",
        f"note = {_toml_str(note)}",
        "",
        "[estimate]",
        f"wall_seconds = {cost.wall_seconds:.1f}",
        f"money_usd = {cost.money_usd:.4f}",
        f"device = {_toml_str(cost.device.name)}",
        f"basis = {_toml_str(cost.basis)}",
        f"units = {cost.units}",
        "",
    ]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def decide(spec: RunSpec, kind: RunKind, config: Config, *, out: Optional[Path] = None,
           max_steps: Optional[int] = None, max_seconds: Optional[float] = None,
           device: Optional[DeviceInfo] = None, units: Optional[List[str]] = None) -> GateDecision:
    cost = assess(estimate(spec, kind, config, out=out, max_steps=max_steps, max_seconds=max_seconds,
                           device=device, units=units), config)
    approval = find_approval(config, spec) if cost.gated else None
    return GateDecision(cost=cost, gated=cost.gated, approved=approval is not None,
                        approval_path=approval_path(config, spec) if cost.gated else None,
                        approval=approval, reasons=list(cost.reasons))


def refusal_message(decision: GateDecision, spec: RunSpec) -> str:
    c = decision.cost
    cmd = (f"python -m rl_researcher.approve {spec.source_path or spec.name} "
           f"--quote \"<what the human said>\"")
    return ("this run is over the gate line and has no approval.\n"
            f"  estimate: {c.describe()}\n"
            + "".join(f"  because: {r}\n" for r in decision.reasons)
            + f"  after the human says yes, record it with:\n    {cmd}\n"
            f"  (the approval is bound to fingerprint {c.fingerprint}; editing the spec voids it)")


def enforce(spec: RunSpec, kind: RunKind, out: Path, *, max_steps: Optional[int], max_seconds: Optional[float],
            config: Optional[Config], device: Optional[DeviceInfo], units: Optional[List[str]] = None) -> GateDecision:
    """What ``runner.run`` calls before taking the lock. Raises :class:`GateRefused`."""
    if config is None:
        raise GateRefused("no project config; the gate cannot be assessed (run inside a project "
                          "with rl-researcher.toml, or pass --no-gate deliberately)")
    d = decide(spec, kind, config, out=out, max_steps=max_steps, max_seconds=max_seconds, device=device, units=units)
    if not d.may_run:
        raise GateRefused(refusal_message(d, spec))
    return d
