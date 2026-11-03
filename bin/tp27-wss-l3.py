#!/usr/bin/env python3
"""TP27 WSS L3 point-to-point carrier.

Clean-room runtime: one TUN interface is carried as WebSocket binary frames over
TLS.  The module has no third-party Python dependency.  It is intentionally not
wired into production provisioning yet; Hybrid Core CI tests its pure helpers.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import select
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


RUNTIME_VERSION = "1.0.0"
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HTTP_HEADER = 16384
MAX_FRAME = 65535
_IFACE_RE = re.compile(r"[A-Za-z0-9_.-]{1,15}")
_PATH_RE = re.compile(r"/[A-Za-z0-9/_-]{4,160}")


@dataclass(frozen=True)
class Config:
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

    @classmethod
    def from_dict(cls, raw: dict) -> "Config":
        cfg = cls(
            id=str(raw.get("id") or ""),
            role=str(raw.get("role") or ""),
            interface=str(raw.get("interface") or ""),
            tunnel_ip=str(raw.get("tunnel_ip") or ""),
            peer_ip=str(raw.get("peer_ip") or ""),
            mtu=int(raw.get("mtu") or 1280),
            ws_path=str(raw.get("ws_path") or ""),
            auth_token=str(raw.get("auth_token") or ""),
            listen_host=str(raw.get("listen_host") or "0.0.0.0"),
            listen_port=int(raw.get("listen_port") or 8443),
            remote_addr=str(raw.get("remote_addr") or ""),
            tls_cert=str(raw.get("tls_cert") or ""),
            tls_key=str(raw.get("tls_key") or ""),
            ca_file=str(raw.get("ca_file") or ""),
            server_name=str(raw.get("server_name") or ""),
            insecure_tls=bool(raw.get("insecure_tls", False)),
            reconnect_delay_s=float(raw.get("reconnect_delay_s") or 2.0),
            heartbeat_s=float(raw.get("heartbeat_s") or 10.0),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", self.id):
            raise ValueError("invalid id")
        if self.role not in {"server", "client"}:
            raise ValueError("role must be server or client")
        if not _IFACE_RE.fullmatch(self.interface):
            raise ValueError("invalid interface")
        local = ipaddress.ip_interface(self.tunnel_ip)
        peer = ipaddress.ip_address(self.peer_ip)
        if local.version != 4 or peer.version != 4:
            raise ValueError("WSS L3 v1 requires IPv4 inner addresses")
        if peer not in local.network or peer == local.ip:
            raise ValueError("peer_ip must be the other address in the tunnel subnet")
        if not 1000 <= self.mtu <= 1400:
            raise ValueError("mtu must be 1000..1400")
        if not _PATH_RE.fullmatch(self.ws_path):
            raise ValueError("invalid ws_path")
        if not 16 <= len(self.auth_token) <= 256:
            raise ValueError("auth_token must be 16..256 characters")
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("listen_port must be 1..65535")
        if not 0.25 <= self.reconnect_delay_s <= 120:
            raise ValueError("reconnect_delay_s out of range")
        if not 2 <= self.heartbeat_s <= 120:
            raise ValueError("heartbeat_s out of range")
        if self.role == "server":
            if not self.tls_cert or not self.tls_key:
                raise ValueError("server requires tls_cert and tls_key")
        else:
            split_host_port(self.remote_addr)
            if not self.insecure_tls and not self.server_name:
                raise ValueError("verified client mode requires server_name")


def split_host_port(value: str) -> tuple[str, int]:
    value = str(value).strip()
    if not value:
        raise ValueError("empty host:port")
    if value.startswith("["):
        host, sep, tail = value[1:].partition("]:")
        if not sep:
            raise ValueError("invalid IPv6 host:port")
    else:
        host, sep, tail = value.rpartition(":")
        if not sep or not host:
            raise ValueError("invalid host:port")
    port = int(tail)
    if not 1 <= port <= 65535:
        raise ValueError("port must be 1..65535")
    return host, port


def load_config(path: str) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    return Config.from_dict(raw)


def run_ip(*args: str) -> None:
    proc = subprocess.run(
        ["ip", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ip command failed").strip()
        raise RuntimeError(detail)


def open_tun(cfg: Config) -> int:
    fd = os.open("/dev/net/tun", os.O_RDWR)
    ifr = struct.pack("16sH", cfg.interface.encode("ascii"), IFF_TUN | IFF_NO_PI)
    fcntl.ioctl(fd, TUNSETIFF, ifr)
    run_ip("link", "set", "dev", cfg.interface, "mtu", str(cfg.mtu))
    run_ip("addr", "replace", cfg.tunnel_ip, "dev", cfg.interface)
    run_ip("link", "set", "dev", cfg.interface, "up")
    return fd


def websocket_accept_value(key: str) -> str:
    digest = hashlib.sha1((key.strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _read_http_headers(sock: socket.socket) -> tuple[str, dict[str, str]]:
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("peer closed during HTTP handshake")
        buf.extend(chunk)
        if len(buf) > MAX_HTTP_HEADER:
            raise ValueError("HTTP header too large")
    head, _sep, extra = bytes(buf).partition(b"\r\n\r\n")
    if extra:
        raise ValueError("unexpected data after WebSocket handshake header")
    lines = head.decode("iso-8859-1").split("\r\n")
    start = lines[0]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return start, headers


def server_handshake(sock: socket.socket, cfg: Config) -> None:
    start, headers = _read_http_headers(sock)
    parts = start.split()
    if len(parts) < 3 or parts[0] != "GET" or parts[1] != cfg.ws_path:
        sock.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
        raise ValueError("invalid WebSocket path")
    if headers.get("upgrade", "").lower() != "websocket":
        raise ValueError("missing WebSocket Upgrade")
    if "upgrade" not in headers.get("connection", "").lower():
        raise ValueError("missing Connection Upgrade")
    token = headers.get("x-tp27-token", "")
    if not hmac.compare_digest(token.encode(), cfg.auth_token.encode()):
        sock.sendall(b"HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n")
        raise PermissionError("invalid TP27 token")
    key = headers.get("sec-websocket-key", "")
    if not key:
        raise ValueError("missing Sec-WebSocket-Key")
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {websocket_accept_value(key)}\r\n"
        "\r\n"
    )
    sock.sendall(response.encode("ascii"))


def client_handshake(sock: socket.socket, cfg: Config) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    host, _port = split_host_port(cfg.remote_addr)
    request = (
        f"GET {cfg.ws_path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"X-TP27-Token: {cfg.auth_token}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    start, headers = _read_http_headers(sock)
    if not start.startswith("HTTP/1.1 101 "):
        raise ConnectionError(f"WebSocket upgrade rejected: {start}")
    expected = websocket_accept_value(key)
    got = headers.get("sec-websocket-accept", "")
    if not hmac.compare_digest(got.encode(), expected.encode()):
        raise ConnectionError("invalid Sec-WebSocket-Accept")


def encode_frame(payload: bytes, *, opcode: int = 0x2, masked: bool = False) -> bytes:
    if len(payload) > MAX_FRAME:
        raise ValueError("frame exceeds TP27 maximum")
    first = 0x80 | (opcode & 0x0F)
    mask_bit = 0x80 if masked else 0
    length = len(payload)
    if length < 126:
        header = bytes((first, mask_bit | length))
    elif length <= 0xFFFF:
        header = bytes((first, mask_bit | 126)) + struct.pack("!H", length)
    else:
        header = bytes((first, mask_bit | 127)) + struct.pack("!Q", length)
    if not masked:
        return header + payload
    key = os.urandom(4)
    body = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return header + key + body


def recv_exact(sock: socket.socket, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        chunk = sock.recv(size - len(out))
        if not chunk:
            raise ConnectionError("peer closed")
        out.extend(chunk)
    return bytes(out)


def recv_frame(sock: socket.socket, *, require_masked: bool | None = None) -> tuple[int, bytes]:
    b1, b2 = recv_exact(sock, 2)
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if not fin:
        raise ValueError("fragmented frames are not supported")
    if require_masked is not None and masked != require_masked:
        raise ValueError("invalid WebSocket masking direction")
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    if length > MAX_FRAME:
        raise ValueError("incoming frame too large")
    mask = recv_exact(sock, 4) if masked else b""
    payload = recv_exact(sock, length)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


class WSWriter:
    def __init__(self, sock: socket.socket, *, masked: bool):
        self.sock = sock
        self.masked = masked
        self.lock = threading.Lock()

    def send(self, payload: bytes, opcode: int = 0x2) -> None:
        frame = encode_frame(payload, opcode=opcode, masked=self.masked)
        with self.lock:
            self.sock.sendall(frame)


def configure_tcp_socket(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    # Linux-specific keepalive tuning is best-effort.
    for name, value in (("TCP_KEEPIDLE", 20), ("TCP_KEEPINTVL", 5), ("TCP_KEEPCNT", 3)):
        opt = getattr(socket, name, None)
        if opt is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, opt, value)
            except OSError:
                pass


def bridge(sock: socket.socket, tun_fd: int, *, client_side: bool, heartbeat_s: float) -> None:
    stop = threading.Event()
    writer = WSWriter(sock, masked=client_side)
    error: list[BaseException] = []

    def tun_to_ws() -> None:
        try:
            while not stop.is_set():
                readable, _, _ = select.select([tun_fd], [], [], 1.0)
                if not readable:
                    continue
                packet = os.read(tun_fd, MAX_FRAME)
                if packet:
                    writer.send(packet, 0x2)
        except BaseException as exc:  # propagate through stop flag
            error.append(exc)
            stop.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def heartbeat() -> None:
        while not stop.wait(heartbeat_s):
            try:
                writer.send(b"tp27", 0x9)
            except BaseException as exc:
                error.append(exc)
                stop.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return

    tx = threading.Thread(target=tun_to_ws, name="tp27-wss-tun-tx", daemon=True)
    hb = threading.Thread(target=heartbeat, name="tp27-wss-heartbeat", daemon=True)
    tx.start()
    hb.start()
    try:
        require_masked = not client_side
        while not stop.is_set():
            opcode, payload = recv_frame(sock, require_masked=require_masked)
            if opcode == 0x2:
                if payload:
                    os.write(tun_fd, payload)
            elif opcode == 0x9:
                writer.send(payload, 0xA)
            elif opcode == 0xA:
                continue
            elif opcode == 0x8:
                break
            else:
                raise ValueError(f"unsupported WebSocket opcode: {opcode}")
    finally:
        stop.set()
        try:
            writer.send(b"", 0x8)
        except Exception:
            pass
        tx.join(timeout=2)
        hb.join(timeout=2)
    if error:
        raise ConnectionError(str(error[0]))


def server_tls_context(cfg: Config) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cfg.tls_cert, cfg.tls_key)
    return ctx


def client_tls_context(cfg: Config) -> ssl.SSLContext:
    if cfg.insecure_tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cfg.ca_file or None)
        ctx.check_hostname = True
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def run_server(cfg: Config, tun_fd: int) -> None:
    ctx = server_tls_context(cfg)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((cfg.listen_host, cfg.listen_port))
    listener.listen(16)
    print(
        f"TP27_WSS_L3_LISTEN id={cfg.id} addr={cfg.listen_host}:{cfg.listen_port} iface={cfg.interface}",
        flush=True,
    )
    while True:
        raw, addr = listener.accept()
        configure_tcp_socket(raw)
        try:
            with ctx.wrap_socket(raw, server_side=True) as tls_sock:
                server_handshake(tls_sock, cfg)
                print(f"TP27_WSS_L3_CONNECTED id={cfg.id} peer={addr[0]}:{addr[1]}", flush=True)
                bridge(tls_sock, tun_fd, client_side=False, heartbeat_s=cfg.heartbeat_s)
        except Exception as exc:
            print(f"TP27_WSS_L3_SESSION_ERROR id={cfg.id} error={type(exc).__name__}:{exc}", flush=True)
            try:
                raw.close()
            except OSError:
                pass


def run_client(cfg: Config, tun_fd: int) -> None:
    host, port = split_host_port(cfg.remote_addr)
    ctx = client_tls_context(cfg)
    while True:
        try:
            raw = socket.create_connection((host, port), timeout=10)
            configure_tcp_socket(raw)
            server_name = cfg.server_name or host
            with ctx.wrap_socket(raw, server_hostname=server_name if not cfg.insecure_tls else None) as tls_sock:
                tls_sock.settimeout(None)
                client_handshake(tls_sock, cfg)
                print(f"TP27_WSS_L3_CONNECTED id={cfg.id} remote={cfg.remote_addr}", flush=True)
                bridge(tls_sock, tun_fd, client_side=True, heartbeat_s=cfg.heartbeat_s)
        except Exception as exc:
            print(f"TP27_WSS_L3_RECONNECT id={cfg.id} error={type(exc).__name__}:{exc}", flush=True)
            time.sleep(cfg.reconnect_delay_s)


def main() -> int:
    parser = argparse.ArgumentParser(prog="tp27-wss-l3")
    parser.add_argument("--config", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.validate_only:
        print(f"TP27_WSS_L3_CONFIG_OK id={cfg.id} role={cfg.role}")
        return 0
    if os.geteuid() != 0:
        raise PermissionError("tp27-wss-l3 must run as root to create a TUN interface")
    tun_fd = open_tun(cfg)
    print(
        f"TP27_WSS_L3_READY version={RUNTIME_VERSION} id={cfg.id} role={cfg.role} "
        f"iface={cfg.interface} local={cfg.tunnel_ip} peer={cfg.peer_ip} mtu={cfg.mtu}",
        flush=True,
    )
    try:
        if cfg.role == "server":
            run_server(cfg, tun_fd)
        else:
            run_client(cfg, tun_fd)
    finally:
        os.close(tun_fd)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"TP27_WSS_L3_FATAL {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
