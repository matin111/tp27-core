from __future__ import annotations

from .model import (
    Maturity,
    ServiceProfile,
    ServiceProtocol,
    TransportCapabilities,
    TransportKind,
)


SERVICE_PROFILES: dict[ServiceProtocol, ServiceProfile] = {
    ServiceProtocol.CISCO: ServiceProfile(
        protocol=ServiceProtocol.CISCO,
        display_name="Cisco / OpenConnect",
        needs_tcp=True,
        needs_udp=True,
        needs_l3=False,
        tcp_ports=(443,),
        udp_ports=(443,),
        notes="TCP control plus DTLS/UDP data when available.",
    ),
    ServiceProtocol.OPENVPN: ServiceProfile(
        protocol=ServiceProtocol.OPENVPN,
        display_name="OpenVPN",
        needs_tcp=True,
        needs_udp=True,
        needs_l3=False,
        notes="Profiles may use TCP or UDP; the shared TP27 path must support both.",
    ),
    ServiceProtocol.WIREGUARD: ServiceProfile(
        protocol=ServiceProtocol.WIREGUARD,
        display_name="WireGuard",
        needs_tcp=False,
        needs_udp=True,
        needs_l3=False,
        notes="WireGuard data plane is UDP-only.",
    ),
    ServiceProtocol.L2TP: ServiceProfile(
        protocol=ServiceProtocol.L2TP,
        display_name="L2TP / IPsec",
        needs_tcp=False,
        needs_udp=True,
        needs_l3=True,
        needs_esp=True,
        udp_ports=(500, 4500, 1701),
        notes="Requires UDP 500/4500/1701; native ESP may also be needed when NAT-T is not used.",
    ),
    ServiceProtocol.XRAY: ServiceProfile(
        protocol=ServiceProtocol.XRAY,
        display_name="Xray / V2Ray",
        needs_tcp=True,
        needs_udp=True,
        needs_l3=False,
        notes="Supports TCP and UDP workloads; actual inbounds may use WS/WSS/gRPC/XHTTP/etc.",
    ),
}


TRANSPORTS: dict[TransportKind, TransportCapabilities] = {
    TransportKind.DIRECT_GRE: TransportCapabilities(
        kind=TransportKind.DIRECT_GRE,
        display_name="Direct GRE",
        carries_tcp=True,
        carries_udp=True,
        carries_l3=True,
        carries_esp=True,
        encrypted=False,
        outer_udp=False,
        outer_tcp=False,
        supports_multiplex=False,
        maturity=Maturity.STABLE,
        base_priority=40,
        notes="Kernel L3 baseline; useful where IP protocol 47 is clean and fast.",
    ),
    TransportKind.FAST_WG: TransportCapabilities(
        kind=TransportKind.FAST_WG,
        display_name="WireGuard Fast Overlay",
        carries_tcp=True,
        carries_udp=True,
        carries_l3=True,
        carries_esp=True,
        encrypted=True,
        outer_udp=True,
        outer_tcp=False,
        supports_multiplex=False,
        maturity=Maturity.PROVEN,
        base_priority=100,
        notes="L3 WireGuard overlay. Proven on TP27 over a UDP-capable carrier.",
    ),
    TransportKind.QUIC: TransportCapabilities(
        kind=TransportKind.QUIC,
        display_name="QUIC Relay",
        carries_tcp=True,
        carries_udp=True,
        carries_l3=False,
        carries_esp=False,
        encrypted=True,
        outer_udp=True,
        outer_tcp=False,
        supports_multiplex=True,
        maturity=Maturity.STABLE,
        base_priority=86,
        notes="Encrypted UDP transport with independent multiplexed streams.",
    ),
    TransportKind.KCP: TransportCapabilities(
        kind=TransportKind.KCP,
        display_name="KCP Relay",
        carries_tcp=True,
        carries_udp=True,
        carries_l3=False,
        carries_esp=False,
        encrypted=True,
        outer_udp=True,
        outer_tcp=False,
        supports_multiplex=True,
        maturity=Maturity.STABLE,
        base_priority=76,
        notes="Loss-tolerant UDP carrier for proxied TCP/UDP services.",
    ),
    TransportKind.WSS_L3: TransportCapabilities(
        kind=TransportKind.WSS_L3,
        display_name="WSS Packet Carrier",
        carries_tcp=True,
        carries_udp=True,
        carries_l3=True,
        carries_esp=True,
        encrypted=True,
        outer_udp=False,
        outer_tcp=True,
        supports_multiplex=True,
        maturity=Maturity.STABLE,
        base_priority=70,
        notes="Packet-mode L3 carrier over WebSocket/TLS; higher overhead but broad reachability.",
    ),
    TransportKind.TCPMUX: TransportCapabilities(
        kind=TransportKind.TCPMUX,
        display_name="TCP Multiplex Relay",
        carries_tcp=True,
        carries_udp=True,
        carries_l3=False,
        carries_esp=False,
        encrypted=True,
        outer_udp=False,
        outer_tcp=True,
        supports_multiplex=True,
        maturity=Maturity.STABLE,
        base_priority=62,
        notes="Reverse relay over pooled/multiplexed TCP; UDP is encapsulated by the relay.",
    ),
    TransportKind.BACKHAUL_TUN: TransportCapabilities(
        kind=TransportKind.BACKHAUL_TUN,
        display_name="Backhaul-compatible TUN",
        carries_tcp=True,
        carries_udp=True,
        carries_l3=True,
        carries_esp=True,
        encrypted=True,
        outer_udp=False,
        outer_tcp=True,
        supports_multiplex=True,
        maturity=Maturity.STABLE,
        base_priority=68,
        notes="Independent TP27 TUN-compatible mode; no dependency on a private premium binary.",
    ),
    TransportKind.SSH_HYBRID: TransportCapabilities(
        kind=TransportKind.SSH_HYBRID,
        display_name="SSH Hybrid Relay",
        carries_tcp=True,
        carries_udp=False,
        carries_l3=False,
        carries_esp=False,
        encrypted=True,
        outer_udp=False,
        outer_tcp=True,
        supports_multiplex=True,
        maturity=Maturity.STABLE,
        base_priority=46,
        notes="Restricted SSH reverse forwarding with a relay behind it; TCP-only fallback.",
    ),
}


DEFAULT_FALLBACK_ORDER: tuple[TransportKind, ...] = (
    TransportKind.FAST_WG,
    TransportKind.QUIC,
    TransportKind.KCP,
    TransportKind.WSS_L3,
    TransportKind.BACKHAUL_TUN,
    TransportKind.TCPMUX,
    TransportKind.DIRECT_GRE,
    TransportKind.SSH_HYBRID,
)


def service_profile(protocol: ServiceProtocol | str) -> ServiceProfile:
    if not isinstance(protocol, ServiceProtocol):
        protocol = ServiceProtocol(str(protocol).strip().lower())
    return SERVICE_PROFILES[protocol]


def transport_capabilities(kind: TransportKind | str) -> TransportCapabilities:
    if not isinstance(kind, TransportKind):
        kind = TransportKind(str(kind).strip().lower())
    return TRANSPORTS[kind]
