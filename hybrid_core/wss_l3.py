from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import re


@dataclass(frozen=True)
class WSSL3Endpoint:
    id: str
    role: str
    interface: str
    tunnel_ip: str
    peer_ip: str
    mtu: int
    ws_path: str
    auth_token: str
    listen_host: str = "0.0.0.0"
    listen_port: int = 8443
    remote_addr: str = ""
    tls_cert: str = ""
    tls_key: str = ""
    ca_file: str = ""
    server_name: str = ""
    insecure_tls: bool = False
    reconnect_delay_s: float = 2.0
    heartbeat_s: float = 10.0

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.id):
            raise ValueError("invalid id")
        if self.role not in {"server", "client"}:
            raise ValueError("role must be server or client")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", self.interface):
            raise ValueError("invalid interface")
        local = ipaddress.ip_interface(self.tunnel_ip)
        peer = ipaddress.ip_address(self.peer_ip)
        if local.version != 4 or peer.version != 4 or peer not in local.network or peer == local.ip:
            raise ValueError("invalid point-to-point IPv4 addresses")
        if not 1000 <= int(self.mtu) <= 1400:
            raise ValueError("mtu must be 1000..1400")
        if not re.fullmatch(r"/[A-Za-z0-9/_-]{4,160}", self.ws_path):
            raise ValueError("invalid ws_path")
        if not 16 <= len(self.auth_token) <= 256:
            raise ValueError("auth_token must be 16..256 chars")
        if not 1 <= int(self.listen_port) <= 65535:
            raise ValueError("listen_port must be 1..65535")
        if self.role == "server" and (not self.tls_cert or not self.tls_key):
            raise ValueError("server requires tls_cert and tls_key")
        if self.role == "client" and not self.remote_addr:
            raise ValueError("client requires remote_addr")
        if self.role == "client" and not self.insecure_tls and not self.server_name:
            raise ValueError("verified client requires server_name")

    def as_dict(self) -> dict:
        self.validate()
        return {
            "id": self.id,
            "role": self.role,
            "interface": self.interface,
            "tunnel_ip": self.tunnel_ip,
            "peer_ip": self.peer_ip,
            "mtu": int(self.mtu),
            "ws_path": self.ws_path,
            "auth_token": self.auth_token,
            "listen_host": self.listen_host,
            "listen_port": int(self.listen_port),
            "remote_addr": self.remote_addr,
            "tls_cert": self.tls_cert,
            "tls_key": self.tls_key,
            "ca_file": self.ca_file,
            "server_name": self.server_name,
            "insecure_tls": bool(self.insecure_tls),
            "reconnect_delay_s": float(self.reconnect_delay_s),
            "heartbeat_s": float(self.heartbeat_s),
        }

    def render_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class WSSL3Pair:
    server: WSSL3Endpoint
    client: WSSL3Endpoint

    def validate(self) -> None:
        self.server.validate()
        self.client.validate()
        if self.server.role != "server" or self.client.role != "client":
            raise ValueError("pair roles must be server/client")
        server_iface = ipaddress.ip_interface(self.server.tunnel_ip)
        client_iface = ipaddress.ip_interface(self.client.tunnel_ip)
        if server_iface.network != client_iface.network:
            raise ValueError("WSS L3 endpoints must share one inner subnet")
        if self.server.peer_ip != str(client_iface.ip):
            raise ValueError("server peer_ip must equal client inner IP")
        if self.client.peer_ip != str(server_iface.ip):
            raise ValueError("client peer_ip must equal server inner IP")
        if self.server.ws_path != self.client.ws_path:
            raise ValueError("ws_path mismatch")
        if self.server.auth_token != self.client.auth_token:
            raise ValueError("auth_token mismatch")

    def render(self) -> dict[str, str]:
        self.validate()
        return {
            "server": self.server.render_json(),
            "client": self.client.render_json(),
        }


def build_wss_l3_pair(
    *,
    id: str,
    server_interface: str,
    client_interface: str,
    server_tunnel_ip: str,
    client_tunnel_ip: str,
    server_public_host: str,
    listen_port: int,
    ws_path: str,
    auth_token: str,
    tls_cert: str,
    tls_key: str,
    ca_file: str,
    server_name: str,
    mtu: int = 1280,
    insecure_tls: bool = False,
) -> WSSL3Pair:
    server_inner = ipaddress.ip_interface(server_tunnel_ip)
    client_inner = ipaddress.ip_interface(client_tunnel_ip)
    if server_inner.network != client_inner.network:
        raise ValueError("server/client tunnel IPs must share one subnet")
    server = WSSL3Endpoint(
        id=id,
        role="server",
        interface=server_interface,
        tunnel_ip=str(server_inner),
        peer_ip=str(client_inner.ip),
        mtu=mtu,
        ws_path=ws_path,
        auth_token=auth_token,
        listen_port=listen_port,
        tls_cert=tls_cert,
        tls_key=tls_key,
    )
    client = WSSL3Endpoint(
        id=id,
        role="client",
        interface=client_interface,
        tunnel_ip=str(client_inner),
        peer_ip=str(server_inner.ip),
        mtu=mtu,
        ws_path=ws_path,
        auth_token=auth_token,
        listen_port=listen_port,
        remote_addr=f"{server_public_host}:{int(listen_port)}",
        ca_file=ca_file,
        server_name=server_name,
        insecure_tls=insecure_tls,
    )
    pair = WSSL3Pair(server=server, client=client)
    pair.validate()
    return pair


def render_systemd_unit(config_path: str, *, runtime_path: str = "/usr/local/lib/tp27/tp27-wss-l3.py") -> str:
    if not config_path.startswith("/") or not runtime_path.startswith("/"):
        raise ValueError("runtime and config paths must be absolute")
    return f"""[Unit]
Description=TP27 Native WSS L3 carrier
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {runtime_path} --config {config_path}
Restart=always
RestartSec=3
StartLimitIntervalSec=60
StartLimitBurst=8
LimitNOFILE=1048576
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=true
PrivateTmp=yes
ReadWritePaths=/dev/net/tun /run
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
"""
