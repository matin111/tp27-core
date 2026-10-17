from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Sequence


class ServiceProtocol(str, Enum):
    CISCO = "cisco"
    OPENVPN = "openvpn"
    WIREGUARD = "wireguard"
    L2TP = "l2tp"
    XRAY = "xray"


class TransportKind(str, Enum):
    DIRECT_GRE = "direct_gre"
    FAST_WG = "fast_wg"
    QUIC = "quic"
    KCP = "kcp"
    WSS_L3 = "wss_l3"
    TCPMUX = "tcpmux"
    BACKHAUL_TUN = "backhaul_tun"
    SSH_HYBRID = "ssh_hybrid"


class Maturity(str, Enum):
    PROVEN = "proven"
    STABLE = "stable"
    EXPERIMENTAL = "experimental"


@dataclass(frozen=True)
class ServiceProfile:
    protocol: ServiceProtocol
    display_name: str
    needs_tcp: bool
    needs_udp: bool
    needs_l3: bool
    needs_esp: bool = False
    tcp_ports: tuple[int, ...] = ()
    udp_ports: tuple[int, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class TransportCapabilities:
    kind: TransportKind
    display_name: str
    carries_tcp: bool
    carries_udp: bool
    carries_l3: bool
    carries_esp: bool
    encrypted: bool
    outer_udp: bool
    outer_tcp: bool
    supports_multiplex: bool
    maturity: Maturity
    base_priority: int
    notes: str = ""


@dataclass(frozen=True)
class LinkMetrics:
    healthy: bool = True
    rtt_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_pct: float = 0.0
    throughput_mbps: float = 0.0
    recent_failures: int = 0

    def normalized(self) -> "LinkMetrics":
        return LinkMetrics(
            healthy=bool(self.healthy),
            rtt_ms=max(0.0, float(self.rtt_ms)),
            jitter_ms=max(0.0, float(self.jitter_ms)),
            loss_pct=min(100.0, max(0.0, float(self.loss_pct))),
            throughput_mbps=max(0.0, float(self.throughput_mbps)),
            recent_failures=max(0, int(self.recent_failures)),
        )


@dataclass(frozen=True)
class CandidateScore:
    transport: TransportKind
    compatible: bool
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingPlan:
    protocol: ServiceProtocol
    primary: TransportKind | None
    fallbacks: tuple[TransportKind, ...]
    ranked: tuple[CandidateScore, ...]
    rationale: tuple[str, ...] = ()

    @property
    def all_transports(self) -> tuple[TransportKind, ...]:
        if self.primary is None:
            return self.fallbacks
        return (self.primary,) + self.fallbacks


@dataclass(frozen=True)
class PolicyOptions:
    require_encryption: bool = True
    allow_experimental: bool = False
    prefer_outer_udp: bool = True
    max_fallbacks: int = 3
    blocked_transports: frozenset[TransportKind] = field(default_factory=frozenset)


MetricsMap = Mapping[TransportKind, LinkMetrics]
TransportSequence = Sequence[TransportKind]
