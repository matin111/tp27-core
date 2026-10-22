from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import ipaddress

from .services import PortMap


class FRPTransport(str, Enum):
    TCP = "tcp"
    KCP = "kcp"
    QUIC = "quic"
    WEBSOCKET = "websocket"


@dataclass(frozen=True)
class FRPServerConfig:
    bind_addr: str
    bind_port: int
    token: str
    kcp_bind_port: int | None = None
    quic_bind_port: int | None = None
    max_pool_count: int = 100
    log_level: str = "info"

    def validate(self) -> None:
        ipaddress.ip_address(self.bind_addr)
        for name, value in (
            ("bind_port", self.bind_port),
            ("kcp_bind_port", self.kcp_bind_port),
            ("quic_bind_port", self.quic_bind_port),
        ):
            if value is not None and not 1 <= int(value) <= 65535:
                raise ValueError(f"{name} must be 1..65535")
        if not self.token.strip():
            raise ValueError("FRP token is required")
        if not 1 <= int(self.max_pool_count) <= 10000:
            raise ValueError("max_pool_count must be 1..10000")


@dataclass(frozen=True)
class FRPClientConfig:
    server_addr: str
    server_port: int
    token: str
    transport: FRPTransport
    pool_count: int = 8
    tcp_mux: bool = False
    heartbeat_interval: int = 15
    heartbeat_timeout: int = 45
    login_fail_exit: bool = False

    def validate(self) -> None:
        if not self.server_addr.strip():
            raise ValueError("server_addr is required")
        if not 1 <= int(self.server_port) <= 65535:
            raise ValueError("server_port must be 1..65535")
        if not self.token.strip():
            raise ValueError("FRP token is required")
        if not 1 <= int(self.pool_count) <= 10000:
            raise ValueError("pool_count must be 1..10000")
        if not 1 <= int(self.heartbeat_interval) <= 3600:
            raise ValueError("heartbeat_interval must be 1..3600")
        if self.heartbeat_timeout <= self.heartbeat_interval:
            raise ValueError("heartbeat_timeout must be greater than heartbeat_interval")


def _q(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def render_frps(config: FRPServerConfig) -> str:
    config.validate()
    kcp = config.kcp_bind_port or config.bind_port
    quic = config.quic_bind_port or config.bind_port
    lines = [
        f"bindAddr = {_q(config.bind_addr)}",
        f"bindPort = {int(config.bind_port)}",
        f"kcpBindPort = {int(kcp)}",
        f"quicBindPort = {int(quic)}",
        'auth.method = "token"',
        f"auth.token = {_q(config.token)}",
        f"transport.maxPoolCount = {int(config.max_pool_count)}",
        f"log.level = {_q(config.log_level)}",
    ]
    return "\n".join(lines) + "\n"


def render_frpc(config: FRPClientConfig, mappings: tuple[PortMap, ...]) -> str:
    config.validate()
    lines = [
        f"serverAddr = {_q(config.server_addr)}",
        f"serverPort = {int(config.server_port)}",
        'auth.method = "token"',
        f"auth.token = {_q(config.token)}",
        f"loginFailExit = {'true' if config.login_fail_exit else 'false'}",
        f"transport.protocol = {_q(config.transport.value)}",
        f"transport.tcpMux = {'true' if config.tcp_mux else 'false'}",
        f"transport.poolCount = {int(config.pool_count)}",
        f"transport.heartbeatInterval = {int(config.heartbeat_interval)}",
        f"transport.heartbeatTimeout = {int(config.heartbeat_timeout)}",
        "",
    ]
    for index, row in enumerate(mappings, start=1):
        if row.proto not in {"tcp", "udp"}:
            raise ValueError(f"unsupported FRP proxy protocol: {row.proto}")
        lines.extend(
            [
                "[[proxies]]",
                f"name = {_q(f'tp27-{row.service}-{index}')}",
                f"type = {_q(row.proto)}",
                'localIP = "127.0.0.1"',
                f"localPort = {int(row.target_port)}",
                f"remotePort = {int(row.listen_port)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def transport_port(config: FRPServerConfig, transport: FRPTransport) -> int:
    if transport == FRPTransport.KCP:
        return int(config.kcp_bind_port or config.bind_port)
    if transport == FRPTransport.QUIC:
        return int(config.quic_bind_port or config.bind_port)
    return int(config.bind_port)
