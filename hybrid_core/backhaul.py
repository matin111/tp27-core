from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .services import PortMap


class BackhaulTransport(str, Enum):
    TCP = "tcp"
    TCPMUX = "tcpmux"
    WS = "ws"
    WSS = "wss"
    WSMUX = "wsmux"
    WSSMUX = "wssmux"


@dataclass(frozen=True)
class BackhaulServerConfig:
    bind_addr: str
    token: str
    transport: BackhaulTransport = BackhaulTransport.TCPMUX
    keepalive_period: int = 40
    heartbeat: int = 15
    channel_size: int = 2048
    mux_con: int = 8
    mux_version: int = 2
    tcp_mss: int | None = 1360
    tls_cert: str | None = None
    tls_key: str | None = None

    def validate(self) -> None:
        if ":" not in self.bind_addr:
            raise ValueError("bind_addr must be host:port or :port")
        if not self.token.strip():
            raise ValueError("token is required")
        if self.mux_version not in {1, 2}:
            raise ValueError("mux_version must be 1 or 2")
        if not 1 <= int(self.mux_con) <= 1024:
            raise ValueError("mux_con must be 1..1024")
        if self.transport in {BackhaulTransport.WSS, BackhaulTransport.WSSMUX}:
            if not self.tls_cert or not self.tls_key:
                raise ValueError("WSS transport requires tls_cert and tls_key")


@dataclass(frozen=True)
class BackhaulClientConfig:
    remote_addr: str
    token: str
    transport: BackhaulTransport = BackhaulTransport.TCPMUX
    connection_pool: int = 8
    keepalive_period: int = 40
    retry_interval: int = 3
    dial_timeout: int = 10
    mux_version: int = 2
    tcp_mss: int | None = 1360
    edge_ip: str | None = None

    def validate(self) -> None:
        if ":" not in self.remote_addr:
            raise ValueError("remote_addr must be host:port")
        if not self.token.strip():
            raise ValueError("token is required")
        if not 1 <= int(self.connection_pool) <= 1024:
            raise ValueError("connection_pool must be 1..1024")
        if self.mux_version not in {1, 2}:
            raise ValueError("mux_version must be 1 or 2")


def _q(value: str) -> str:
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _port_mapping(row: PortMap) -> str:
    if row.listen_port == row.target_port:
        return str(row.listen_port)
    return f"{row.listen_port}={row.target_port}"


def _unique_port_rows(mappings: tuple[PortMap, ...]) -> tuple[PortMap, ...]:
    # Public Backhaul's ports array is port-oriented rather than protocol-oriented.
    # Deduplicate TCP/UDP twins that use the same listen/target pair.
    seen: set[tuple[int, int]] = set()
    out: list[PortMap] = []
    for row in mappings:
        key = (int(row.listen_port), int(row.target_port))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return tuple(out)


def render_backhaul_server(config: BackhaulServerConfig, mappings: tuple[PortMap, ...]) -> str:
    config.validate()
    rows = _unique_port_rows(mappings)
    accept_udp = any(row.proto == "udp" for row in mappings)
    lines = [
        "[server]",
        f"bind_addr = {_q(config.bind_addr)}",
        f"transport = {_q(config.transport.value)}",
        f"accept_udp = {'true' if accept_udp else 'false'}",
        f"token = {_q(config.token)}",
        f"keepalive_period = {int(config.keepalive_period)}",
        f"heartbeat = {int(config.heartbeat)}",
        f"channel_size = {int(config.channel_size)}",
        f"mux_con = {int(config.mux_con)}",
        f"mux_version = {int(config.mux_version)}",
    ]
    if config.tcp_mss is not None:
        lines.append(f"mss = {int(config.tcp_mss)}")
    if config.transport in {BackhaulTransport.WSS, BackhaulTransport.WSSMUX}:
        lines.append(f"tls_cert = {_q(config.tls_cert or '')}")
        lines.append(f"tls_key = {_q(config.tls_key or '')}")
    lines.append("ports = [")
    lines.extend(f"  {_q(_port_mapping(row))}," for row in rows)
    lines.append("]")
    return "\n".join(lines) + "\n"


def render_backhaul_client(config: BackhaulClientConfig) -> str:
    config.validate()
    lines = [
        "[client]",
        f"remote_addr = {_q(config.remote_addr)}",
        f"transport = {_q(config.transport.value)}",
        f"token = {_q(config.token)}",
        f"connection_pool = {int(config.connection_pool)}",
        f"keepalive_period = {int(config.keepalive_period)}",
        f"retry_interval = {int(config.retry_interval)}",
        f"dial_timeout = {int(config.dial_timeout)}",
        f"mux_version = {int(config.mux_version)}",
    ]
    if config.tcp_mss is not None:
        lines.append(f"mss = {int(config.tcp_mss)}")
    if config.edge_ip:
        lines.append(f"edge_ip = {_q(config.edge_ip)}")
    return "\n".join(lines) + "\n"
