from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .model import ServiceProtocol
from .services import ServiceForwardingSpec, build_service_spec


@dataclass(frozen=True)
class ProbeObservation:
    peer_reachable: bool = False
    tcp_ports: frozenset[int] = field(default_factory=frozenset)
    udp_ports: frozenset[int] = field(default_factory=frozenset)
    ip_protocols: frozenset[int] = field(default_factory=frozenset)
    service_ok: bool = False
    handshake_age_s: float | None = None
    details: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HealthGateResult:
    protocol: ServiceProtocol
    passed: bool
    checks: tuple[str, ...]
    failures: tuple[str, ...]


def evaluate_service_gate(
    spec: ServiceForwardingSpec,
    observation: ProbeObservation,
    *,
    allow_natt_only: bool = True,
    max_handshake_age_s: float | None = None,
) -> HealthGateResult:
    checks: list[str] = []
    failures: list[str] = []

    if spec.requires_l3:
        checks.append("peer_l3_reachable")
        if not observation.peer_reachable:
            failures.append("peer_l3_unreachable")

    required_tcp = {int(row.target_port) for row in spec.mappings if row.proto == "tcp"}
    required_udp = {int(row.target_port) for row in spec.mappings if row.proto == "udp"}

    for port in sorted(required_tcp):
        checks.append(f"tcp:{port}")
        if port not in observation.tcp_ports:
            failures.append(f"tcp_unreachable:{port}")

    for port in sorted(required_udp):
        checks.append(f"udp:{port}")
        if port not in observation.udp_ports:
            failures.append(f"udp_unverified:{port}")

    if spec.ip_protocols:
        # L2TP/IPsec usually operates through NAT-T (UDP/4500), so native ESP
        # can be optional when NAT-T is explicitly allowed. The requirement is
        # still recorded and can be enforced for sites that need non-NAT-T ESP.
        if not allow_natt_only:
            for proto in spec.ip_protocols:
                checks.append(f"ipproto:{proto}")
                if int(proto) not in observation.ip_protocols:
                    failures.append(f"ipproto_unverified:{proto}")
        else:
            checks.append("natt_allowed")

    if max_handshake_age_s is not None:
        checks.append("handshake_fresh")
        age = observation.handshake_age_s
        if age is None or age < 0 or age > float(max_handshake_age_s):
            failures.append("handshake_stale_or_missing")

    checks.append("service_probe")
    if not observation.service_ok:
        failures.append("service_probe_failed")

    return HealthGateResult(
        protocol=spec.protocol,
        passed=not failures,
        checks=tuple(checks),
        failures=tuple(failures),
    )


def evaluate_protocol_gate(
    protocol: ServiceProtocol | str,
    observation: ProbeObservation,
    *,
    port: int | None = None,
    ports: tuple[int, ...] | None = None,
    allow_natt_only: bool = True,
    max_handshake_age_s: float | None = None,
) -> HealthGateResult:
    spec = build_service_spec(protocol, port=port, ports=ports)
    return evaluate_service_gate(
        spec,
        observation,
        allow_natt_only=allow_natt_only,
        max_handshake_age_s=max_handshake_age_s,
    )
