from __future__ import annotations

from dataclasses import dataclass
import ipaddress


@dataclass(frozen=True)
class WireGuardPeerConfig:
    private_key: str
    public_key: str
    address: str
    peer_public_key: str
    peer_address: str
    listen_port: int
    endpoint: str | None = None
    mtu: int = 1280
    persistent_keepalive: int | None = None

    def validate(self) -> None:
        iface = ipaddress.ip_interface(self.address)
        peer = ipaddress.ip_address(self.peer_address)
        if iface.version != 4 or peer.version != 4:
            raise ValueError("TP27 Fast WG v1 requires IPv4 inner addresses")
        if peer not in iface.network:
            raise ValueError("peer_address must be inside the local WireGuard subnet")
        if str(peer) == str(iface.ip):
            raise ValueError("peer_address cannot equal local address")
        if not 1 <= int(self.listen_port) <= 65535:
            raise ValueError("listen_port must be 1..65535")
        if not 1000 <= int(self.mtu) <= 1420:
            raise ValueError("WireGuard MTU must be 1000..1420")
        if self.persistent_keepalive is not None and not 0 <= int(self.persistent_keepalive) <= 65535:
            raise ValueError("persistent_keepalive must be 0..65535")
        for name, value in (
            ("private_key", self.private_key),
            ("public_key", self.public_key),
            ("peer_public_key", self.peer_public_key),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} is required")


def render_wg_quick(config: WireGuardPeerConfig) -> str:
    config.validate()
    lines = [
        "[Interface]",
        f"Address = {config.address}",
        f"ListenPort = {int(config.listen_port)}",
        f"PrivateKey = {config.private_key.strip()}",
        f"MTU = {int(config.mtu)}",
        "",
        "[Peer]",
        f"PublicKey = {config.peer_public_key.strip()}",
        f"AllowedIPs = {config.peer_address}/32",
    ]
    if config.endpoint:
        lines.append(f"Endpoint = {config.endpoint.strip()}")
    if config.persistent_keepalive is not None:
        lines.append(f"PersistentKeepalive = {int(config.persistent_keepalive)}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class FastWGPair:
    iran: WireGuardPeerConfig
    foreign: WireGuardPeerConfig

    def validate(self) -> None:
        self.iran.validate()
        self.foreign.validate()
        iran_ip = ipaddress.ip_interface(self.iran.address).ip
        foreign_ip = ipaddress.ip_interface(self.foreign.address).ip
        if str(iran_ip) != self.foreign.peer_address:
            raise ValueError("foreign peer_address must equal Iran WireGuard address")
        if str(foreign_ip) != self.iran.peer_address:
            raise ValueError("Iran peer_address must equal Foreign WireGuard address")
        if self.iran.public_key.strip() != self.foreign.peer_public_key.strip():
            raise ValueError("Foreign peer key does not match Iran public key")
        if self.foreign.public_key.strip() != self.iran.peer_public_key.strip():
            raise ValueError("Iran peer key does not match Foreign public key")

    def render(self) -> dict[str, str]:
        self.validate()
        return {
            "iran": render_wg_quick(self.iran),
            "foreign": render_wg_quick(self.foreign),
        }


def build_overlay_pair(
    *,
    iran_private_key: str,
    iran_public_key: str,
    foreign_private_key: str,
    foreign_public_key: str,
    iran_address: str,
    foreign_address: str,
    foreign_carrier_endpoint: str,
    listen_port: int,
    mtu: int = 1280,
    keepalive: int = 5,
) -> FastWGPair:
    """Build the proven TP27 pattern: WireGuard carried by an existing path.

    foreign_carrier_endpoint is normally the foreign carrier/underlay address,
    for example a GRE/FOU inner address such as 10.253.0.9:51888.  No network
    changes are performed here; the function only validates and renders config.
    """
    iran_iface = ipaddress.ip_interface(iran_address)
    foreign_iface = ipaddress.ip_interface(foreign_address)
    if iran_iface.network != foreign_iface.network:
        raise ValueError("Iran and Foreign WireGuard addresses must share one subnet")

    iran = WireGuardPeerConfig(
        private_key=iran_private_key,
        public_key=iran_public_key,
        address=str(iran_iface),
        peer_public_key=foreign_public_key,
        peer_address=str(foreign_iface.ip),
        listen_port=0xC000 | (int(listen_port) & 0x0FFF),
        endpoint=foreign_carrier_endpoint,
        mtu=mtu,
        persistent_keepalive=keepalive,
    )
    foreign = WireGuardPeerConfig(
        private_key=foreign_private_key,
        public_key=foreign_public_key,
        address=str(foreign_iface),
        peer_public_key=iran_public_key,
        peer_address=str(iran_iface.ip),
        listen_port=int(listen_port),
        endpoint=None,
        mtu=mtu,
        persistent_keepalive=None,
    )
    return FastWGPair(iran=iran, foreign=foreign)
