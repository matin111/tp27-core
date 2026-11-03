from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import ServiceProtocol


@dataclass(frozen=True)
class PortMap:
    service: str
    proto: str
    listen_port: int
    target_port: int

    def __post_init__(self):
        proto = self.proto.lower().strip()
        if proto not in {"tcp", "udp"}:
            raise ValueError("proto must be tcp or udp")
        if not 1 <= int(self.listen_port) <= 65535:
            raise ValueError("listen_port must be 1..65535")
        if not 1 <= int(self.target_port) <= 65535:
            raise ValueError("target_port must be 1..65535")
        object.__setattr__(self, "proto", proto)

    def as_portplan_row(self) -> dict:
        return {
            "service": self.service,
            "proto": self.proto,
            "listen_port": int(self.listen_port),
            "target_port": int(self.target_port),
        }


@dataclass(frozen=True)
class ServiceForwardingSpec:
    protocol: ServiceProtocol
    mappings: tuple[PortMap, ...]
    ip_protocols: tuple[int, ...] = ()
    requires_l3: bool = False

    def portplan_rows(self) -> list[dict]:
        return [row.as_portplan_row() for row in self.mappings]


def _same(service: str, proto: str, port: int) -> PortMap:
    return PortMap(service, proto, port, port)


def cisco_spec(port: int = 443) -> ServiceForwardingSpec:
    return ServiceForwardingSpec(
        protocol=ServiceProtocol.CISCO,
        mappings=(
            _same("cisco", "tcp", port),
            _same("cisco", "udp", port),
        ),
    )


def openvpn_spec(port: int = 1194, dual_protocol: bool = True) -> ServiceForwardingSpec:
    rows = [_same("openvpn", "udp", port)]
    if dual_protocol:
        rows.append(_same("openvpn", "tcp", port))
    return ServiceForwardingSpec(ServiceProtocol.OPENVPN, tuple(rows))


def wireguard_spec(port: int = 51820) -> ServiceForwardingSpec:
    return ServiceForwardingSpec(
        protocol=ServiceProtocol.WIREGUARD,
        mappings=(_same("wireguard", "udp", port),),
    )


def l2tp_spec() -> ServiceForwardingSpec:
    return ServiceForwardingSpec(
        protocol=ServiceProtocol.L2TP,
        mappings=(
            _same("l2tp-ipsec", "udp", 500),
            _same("l2tp-ipsec", "udp", 4500),
            _same("l2tp", "udp", 1701),
        ),
        # ESP is IP protocol 50. NAT-T normally moves ESP into UDP/4500, but a
        # full L3 data plane must preserve native ESP too so both modes work.
        ip_protocols=(50,),
        requires_l3=True,
    )


def xray_spec(
    ports: Iterable[int] = (8080,),
    include_udp: bool = True,
) -> ServiceForwardingSpec:
    rows: list[PortMap] = []
    for port in ports:
        p = int(port)
        rows.append(_same("xray", "tcp", p))
        if include_udp:
            rows.append(_same("xray", "udp", p))
    return ServiceForwardingSpec(ServiceProtocol.XRAY, tuple(rows))


def build_service_spec(
    protocol: ServiceProtocol | str,
    *,
    port: int | None = None,
    ports: Iterable[int] | None = None,
) -> ServiceForwardingSpec:
    if not isinstance(protocol, ServiceProtocol):
        protocol = ServiceProtocol(str(protocol).strip().lower())

    if protocol == ServiceProtocol.CISCO:
        return cisco_spec(port or 443)
    if protocol == ServiceProtocol.OPENVPN:
        return openvpn_spec(port or 1194, dual_protocol=True)
    if protocol == ServiceProtocol.WIREGUARD:
        return wireguard_spec(port or 51820)
    if protocol == ServiceProtocol.L2TP:
        return l2tp_spec()
    if protocol == ServiceProtocol.XRAY:
        selected = tuple(ports or ((port or 8080),))
        return xray_spec(selected, include_udp=True)
    raise ValueError(f"unsupported service protocol: {protocol}")
