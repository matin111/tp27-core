from __future__ import annotations

import math
from typing import Iterable

from .catalog import DEFAULT_FALLBACK_ORDER, TRANSPORTS, service_profile
from .model import (
    CandidateScore,
    LinkMetrics,
    Maturity,
    MetricsMap,
    PolicyOptions,
    RoutingPlan,
    ServiceProfile,
    ServiceProtocol,
    TransportCapabilities,
    TransportKind,
)


def compatibility_reasons(profile: ServiceProfile, cap: TransportCapabilities) -> tuple[str, ...]:
    missing: list[str] = []
    if profile.needs_tcp and not cap.carries_tcp:
        missing.append("tcp")
    if profile.needs_udp and not cap.carries_udp:
        missing.append("udp")
    if profile.needs_l3 and not cap.carries_l3:
        missing.append("l3")
    if profile.needs_esp and not cap.carries_esp:
        missing.append("esp")
    return tuple(missing)


def is_compatible(profile: ServiceProfile, cap: TransportCapabilities) -> bool:
    return not compatibility_reasons(profile, cap)


def _score_metrics(metrics: LinkMetrics) -> tuple[float, list[str]]:
    m = metrics.normalized()
    reasons: list[str] = []
    if not m.healthy:
        return -1000.0, ["unhealthy"]

    score = 0.0
    score -= m.loss_pct * 3.5
    score -= min(m.rtt_ms, 500.0) * 0.08
    score -= min(m.jitter_ms, 200.0) * 0.30
    score -= m.recent_failures * 8.0

    if m.throughput_mbps > 0:
        # Useful but deliberately bounded so reliability wins over raw speed.
        score += min(35.0, math.log10(1.0 + m.throughput_mbps) * 14.0)
        reasons.append(f"throughput={m.throughput_mbps:.1f}Mbps")
    if m.loss_pct > 0:
        reasons.append(f"loss={m.loss_pct:.2f}%")
    if m.rtt_ms > 0:
        reasons.append(f"rtt={m.rtt_ms:.1f}ms")
    if m.jitter_ms > 0:
        reasons.append(f"jitter={m.jitter_ms:.1f}ms")
    if m.recent_failures:
        reasons.append(f"recent_failures={m.recent_failures}")
    return score, reasons


def score_candidate(
    protocol: ServiceProtocol | str,
    kind: TransportKind,
    metrics: LinkMetrics | None = None,
    options: PolicyOptions | None = None,
) -> CandidateScore:
    profile = service_profile(protocol)
    cap = TRANSPORTS[kind]
    options = options or PolicyOptions()

    reasons: list[str] = []
    missing = compatibility_reasons(profile, cap)
    if missing:
        return CandidateScore(
            transport=kind,
            compatible=False,
            score=-10000.0,
            reasons=("missing:" + ",".join(missing),),
        )
    if kind in options.blocked_transports:
        return CandidateScore(kind, False, -10000.0, ("blocked_by_policy",))
    if options.require_encryption and not cap.encrypted:
        return CandidateScore(kind, False, -10000.0, ("encryption_required",))
    if cap.maturity == Maturity.EXPERIMENTAL and not options.allow_experimental:
        return CandidateScore(kind, False, -10000.0, ("experimental_disabled",))

    score = float(cap.base_priority)
    reasons.append(f"base={cap.base_priority}")

    if cap.maturity == Maturity.PROVEN:
        score += 10.0
        reasons.append("proven")
    elif cap.maturity == Maturity.STABLE:
        score += 4.0
        reasons.append("stable")

    if options.prefer_outer_udp and cap.outer_udp:
        score += 5.0
        reasons.append("outer_udp")
    if profile.needs_l3 and cap.carries_l3:
        score += 4.0
        reasons.append("native_l3")

    metric_score, metric_reasons = _score_metrics(metrics or LinkMetrics())
    score += metric_score
    reasons.extend(metric_reasons)

    return CandidateScore(kind, True, round(score, 3), tuple(reasons))


def rank_candidates(
    protocol: ServiceProtocol | str,
    metrics: MetricsMap | None = None,
    options: PolicyOptions | None = None,
    candidates: Iterable[TransportKind] | None = None,
) -> tuple[CandidateScore, ...]:
    options = options or PolicyOptions()
    metrics = metrics or {}
    order = tuple(candidates or DEFAULT_FALLBACK_ORDER)
    order_index = {kind: idx for idx, kind in enumerate(order)}

    scored = [
        score_candidate(protocol, kind, metrics.get(kind), options)
        for kind in order
    ]
    scored.sort(
        key=lambda row: (
            0 if row.compatible else 1,
            -row.score,
            order_index.get(row.transport, 999),
        )
    )
    return tuple(scored)


def build_plan(
    protocol: ServiceProtocol | str,
    metrics: MetricsMap | None = None,
    options: PolicyOptions | None = None,
    candidates: Iterable[TransportKind] | None = None,
) -> RoutingPlan:
    options = options or PolicyOptions()
    profile = service_profile(protocol)
    ranked = rank_candidates(profile.protocol, metrics, options, candidates)
    compatible = [row for row in ranked if row.compatible]

    primary = compatible[0].transport if compatible else None
    fallback_limit = max(0, int(options.max_fallbacks))
    fallbacks = tuple(row.transport for row in compatible[1 : 1 + fallback_limit])

    rationale: list[str] = [
        f"protocol={profile.protocol.value}",
        f"full_tcp={profile.needs_tcp}",
        f"full_udp={profile.needs_udp}",
        f"l3={profile.needs_l3}",
        f"esp={profile.needs_esp}",
    ]
    if primary:
        rationale.append(f"primary={primary.value}")
    else:
        rationale.append("no_compatible_transport")

    return RoutingPlan(
        protocol=profile.protocol,
        primary=primary,
        fallbacks=fallbacks,
        ranked=ranked,
        rationale=tuple(rationale),
    )


def build_all_protocol_plans(
    metrics: MetricsMap | None = None,
    options: PolicyOptions | None = None,
) -> dict[ServiceProtocol, RoutingPlan]:
    return {
        protocol: build_plan(protocol, metrics=metrics, options=options)
        for protocol in ServiceProtocol
    }
