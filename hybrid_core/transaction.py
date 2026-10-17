from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .drivers import DRIVERS, DriverReadiness
from .model import LinkMetrics, MetricsMap, PolicyOptions, ServiceProtocol, TransportKind
from .planner import build_plan


class RolloutPhase(str, Enum):
    SNAPSHOT = "snapshot"
    STAGE = "stage"
    START_CANDIDATE = "start_candidate"
    HEALTH = "health"
    BENCHMARK = "benchmark"
    CUTOVER = "cutover"
    VERIFY = "verify"
    COMMIT = "commit"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class RolloutStep:
    phase: RolloutPhase
    transport: TransportKind | None
    mutates_host: bool
    description: str
    rollback_required: bool = False


@dataclass(frozen=True)
class CandidateDecision:
    protocol: ServiceProtocol
    selected: TransportKind | None
    rejected: tuple[tuple[TransportKind, str], ...]
    reason: str


@dataclass(frozen=True)
class TransactionPlan:
    protocol: ServiceProtocol
    current_transport: TransportKind | None
    candidate: TransportKind | None
    steps: tuple[RolloutStep, ...]
    decision: CandidateDecision

    @property
    def can_roll_out(self) -> bool:
        return self.candidate is not None


_RUNTIME_READY = {
    DriverReadiness.PRODUCTION_PROVEN,
    DriverReadiness.RUNTIME_PRESENT,
}


def choose_runtime_candidate(
    protocol: ServiceProtocol | str,
    *,
    metrics: MetricsMap | None = None,
    options: PolicyOptions | None = None,
    current_transport: TransportKind | None = None,
    allow_config_ready_for_lab: bool = False,
) -> CandidateDecision:
    if not isinstance(protocol, ServiceProtocol):
        protocol = ServiceProtocol(str(protocol).strip().lower())
    ranked = build_plan(protocol, metrics=metrics, options=options).ranked
    allowed_readiness = set(_RUNTIME_READY)
    if allow_config_ready_for_lab:
        allowed_readiness.add(DriverReadiness.CONFIG_READY)

    rejected: list[tuple[TransportKind, str]] = []
    for row in ranked:
        kind = row.transport
        if not row.compatible:
            rejected.append((kind, ";".join(row.reasons) or "incompatible"))
            continue
        if current_transport is not None and kind == current_transport:
            rejected.append((kind, "already_current"))
            continue
        descriptor = DRIVERS[kind]
        if descriptor.readiness not in allowed_readiness:
            rejected.append((kind, f"driver_not_runtime_ready:{descriptor.readiness.value}"))
            continue
        if not descriptor.parallel_candidate:
            rejected.append((kind, "driver_not_parallel_safe"))
            continue
        return CandidateDecision(
            protocol=protocol,
            selected=kind,
            rejected=tuple(rejected),
            reason=f"selected:{kind.value}:{descriptor.readiness.value}",
        )

    return CandidateDecision(
        protocol=protocol,
        selected=None,
        rejected=tuple(rejected),
        reason="no_runtime_ready_compatible_candidate",
    )


def build_transaction_plan(
    protocol: ServiceProtocol | str,
    *,
    metrics: MetricsMap | None = None,
    options: PolicyOptions | None = None,
    current_transport: TransportKind | None = None,
    allow_config_ready_for_lab: bool = False,
) -> TransactionPlan:
    decision = choose_runtime_candidate(
        protocol,
        metrics=metrics,
        options=options,
        current_transport=current_transport,
        allow_config_ready_for_lab=allow_config_ready_for_lab,
    )
    candidate = decision.selected
    if candidate is None:
        return TransactionPlan(
            protocol=decision.protocol,
            current_transport=current_transport,
            candidate=None,
            steps=(),
            decision=decision,
        )

    steps = (
        RolloutStep(
            phase=RolloutPhase.SNAPSHOT,
            transport=current_transport,
            mutates_host=False,
            description="Capture current config, routes, service mappings and health before staging.",
        ),
        RolloutStep(
            phase=RolloutPhase.STAGE,
            transport=candidate,
            mutates_host=True,
            description="Write candidate config under an isolated candidate id; do not replace current files.",
            rollback_required=True,
        ),
        RolloutStep(
            phase=RolloutPhase.START_CANDIDATE,
            transport=candidate,
            mutates_host=True,
            description="Start the candidate in parallel while the current transport remains active.",
            rollback_required=True,
        ),
        RolloutStep(
            phase=RolloutPhase.HEALTH,
            transport=candidate,
            mutates_host=False,
            description="Require bidirectional peer health and protocol-specific reachability.",
        ),
        RolloutStep(
            phase=RolloutPhase.BENCHMARK,
            transport=candidate,
            mutates_host=False,
            description="Measure throughput, RTT, jitter and loss without touching the active mapping.",
        ),
        RolloutStep(
            phase=RolloutPhase.CUTOVER,
            transport=candidate,
            mutates_host=True,
            description="Atomically move only the service mapping to the candidate path.",
            rollback_required=True,
        ),
        RolloutStep(
            phase=RolloutPhase.VERIFY,
            transport=candidate,
            mutates_host=False,
            description="Verify real service connectivity after cutover while the old carrier stays available.",
        ),
        RolloutStep(
            phase=RolloutPhase.COMMIT,
            transport=candidate,
            mutates_host=True,
            description="Persist the candidate as active only after verification and keep a rollback snapshot.",
            rollback_required=True,
        ),
        RolloutStep(
            phase=RolloutPhase.ROLLBACK,
            transport=current_transport,
            mutates_host=True,
            description="On any failed gate, restore the exact snapshot and remove only candidate-owned state.",
        ),
    )
    return TransactionPlan(
        protocol=decision.protocol,
        current_transport=current_transport,
        candidate=candidate,
        steps=steps,
        decision=decision,
    )


def transaction_plan_dict(plan: TransactionPlan) -> dict:
    return {
        "protocol": plan.protocol.value,
        "current_transport": plan.current_transport.value if plan.current_transport else None,
        "candidate": plan.candidate.value if plan.candidate else None,
        "can_roll_out": plan.can_roll_out,
        "decision": {
            "reason": plan.decision.reason,
            "rejected": [
                {"transport": kind.value, "reason": reason}
                for kind, reason in plan.decision.rejected
            ],
        },
        "steps": [
            {
                "phase": step.phase.value,
                "transport": step.transport.value if step.transport else None,
                "mutates_host": step.mutates_host,
                "rollback_required": step.rollback_required,
                "description": step.description,
            }
            for step in plan.steps
        ],
    }
