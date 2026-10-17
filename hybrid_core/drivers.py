from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import TransportKind


class DriverReadiness(str, Enum):
    PRODUCTION_PROVEN = "production_proven"
    RUNTIME_PRESENT = "runtime_present"
    CONFIG_READY = "config_ready"
    PLANNED = "planned"


@dataclass(frozen=True)
class DriverDescriptor:
    transport: TransportKind
    readiness: DriverReadiness
    implementation: str
    l3: bool
    parallel_candidate: bool
    notes: str = ""


DRIVERS: dict[TransportKind, DriverDescriptor] = {
    TransportKind.DIRECT_GRE: DriverDescriptor(
        transport=TransportKind.DIRECT_GRE,
        readiness=DriverReadiness.RUNTIME_PRESENT,
        implementation="bin/tp27-link + kernel GRE",
        l3=True,
        parallel_candidate=True,
        notes="Existing TP27 kernel data plane.",
    ),
    TransportKind.FAST_WG: DriverDescriptor(
        transport=TransportKind.FAST_WG,
        readiness=DriverReadiness.PRODUCTION_PROVEN,
        implementation="hybrid_core/fast_wg.py + wg-quick",
        l3=True,
        parallel_candidate=True,
        notes="Validated as WireGuard over an existing UDP-capable TP27 carrier.",
    ),
    TransportKind.QUIC: DriverDescriptor(
        transport=TransportKind.QUIC,
        readiness=DriverReadiness.CONFIG_READY,
        implementation="hybrid_core/frp.py / FRP QUIC",
        l3=False,
        parallel_candidate=True,
        notes="Config renderer is ready; deployment adapter is intentionally not wired to production yet.",
    ),
    TransportKind.KCP: DriverDescriptor(
        transport=TransportKind.KCP,
        readiness=DriverReadiness.CONFIG_READY,
        implementation="hybrid_core/frp.py / FRP KCP",
        l3=False,
        parallel_candidate=True,
        notes="Config renderer is ready; deployment adapter is intentionally not wired to production yet.",
    ),
    TransportKind.WSS_L3: DriverDescriptor(
        transport=TransportKind.WSS_L3,
        readiness=DriverReadiness.RUNTIME_PRESENT,
        implementation="bin/tp27-wss-l3.py (native TUN over WebSocket/TLS)",
        l3=True,
        parallel_candidate=True,
        notes="Clean-room stdlib runtime exists; it must pass namespace and two-VPS lab validation before production promotion.",
    ),
    TransportKind.TCPMUX: DriverDescriptor(
        transport=TransportKind.TCPMUX,
        readiness=DriverReadiness.CONFIG_READY,
        implementation="hybrid_core/backhaul.py or hybrid_core/frp.py",
        l3=False,
        parallel_candidate=True,
        notes="Public Backhaul/FRP config is ready; no automatic installer is connected.",
    ),
    TransportKind.BACKHAUL_TUN: DriverDescriptor(
        transport=TransportKind.BACKHAUL_TUN,
        readiness=DriverReadiness.PLANNED,
        implementation="independent TP27 TUN driver",
        l3=True,
        parallel_candidate=True,
        notes="Will not depend on Hashem backhaul_premium or any unavailable private binary.",
    ),
    TransportKind.SSH_HYBRID: DriverDescriptor(
        transport=TransportKind.SSH_HYBRID,
        readiness=DriverReadiness.CONFIG_READY,
        implementation="hybrid_core/ssh_hybrid.py + OpenSSH + FRP relay",
        l3=False,
        parallel_candidate=True,
        notes="Restricted SSH reverse relay renderer exists; deployment remains opt-in/lab-only.",
    ),
}


def driver_descriptor(kind: TransportKind | str) -> DriverDescriptor:
    if not isinstance(kind, TransportKind):
        kind = TransportKind(str(kind).strip().lower())
    return DRIVERS[kind]


def driver_matrix() -> list[dict]:
    rows: list[dict] = []
    for kind in TransportKind:
        d = DRIVERS[kind]
        rows.append(
            {
                "transport": kind.value,
                "readiness": d.readiness.value,
                "implementation": d.implementation,
                "l3": d.l3,
                "parallel_candidate": d.parallel_candidate,
                "notes": d.notes,
            }
        )
    return rows
