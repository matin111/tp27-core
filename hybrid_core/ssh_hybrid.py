from __future__ import annotations

from dataclasses import dataclass
import re
import shlex


@dataclass(frozen=True)
class SSHHybridConfig:
    remote_host: str
    remote_user: str = "root"
    remote_ssh_port: int = 22
    key_path: str = "/etc/tp27/keys/hybrid_ed25519"
    relay_port: int = 55400
    server_alive_interval: int = 10
    server_alive_count_max: int = 3

    def validate(self) -> None:
        if not self.remote_host.strip():
            raise ValueError("remote_host is required")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", self.remote_user):
            raise ValueError("invalid remote_user")
        if not 1 <= int(self.remote_ssh_port) <= 65535:
            raise ValueError("remote_ssh_port must be 1..65535")
        if not 1024 <= int(self.relay_port) <= 65535:
            raise ValueError("relay_port must be 1024..65535")
        if not self.key_path.startswith("/"):
            raise ValueError("key_path must be absolute")
        if not 1 <= int(self.server_alive_interval) <= 3600:
            raise ValueError("server_alive_interval must be 1..3600")
        if not 1 <= int(self.server_alive_count_max) <= 100:
            raise ValueError("server_alive_count_max must be 1..100")


def render_authorized_key(public_key: str, relay_port: int) -> str:
    key = " ".join(public_key.strip().split())
    if not (key.startswith("ssh-ed25519 ") or key.startswith("sk-ssh-ed25519@openssh.com ")):
        raise ValueError("SSH Hybrid requires an ed25519 public key")
    if not 1024 <= int(relay_port) <= 65535:
        raise ValueError("relay_port must be 1024..65535")
    opts = (
        "restrict,port-forwarding,"
        f'permitlisten="127.0.0.1:{int(relay_port)}"'
    )
    return f"{opts} {key}\n"


def render_iran_systemd(config: SSHHybridConfig) -> str:
    config.validate()
    target = f"{config.remote_user}@{config.remote_host}"
    command = [
        "/usr/bin/ssh",
        "-N",
        "-i",
        config.key_path,
        "-p",
        str(config.remote_ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        f"ServerAliveInterval={config.server_alive_interval}",
        "-o",
        f"ServerAliveCountMax={config.server_alive_count_max}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ClearAllForwardings=yes",
        "-R",
        f"127.0.0.1:{config.relay_port}:127.0.0.1:{config.relay_port}",
        target,
    ]
    exec_start = " ".join(shlex.quote(x) for x in command)
    return f"""[Unit]
Description=TP27 SSH Hybrid carrier
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=always
RestartSec=10
StartLimitIntervalSec=120
StartLimitBurst=6
LimitNOFILE=1048576
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadOnlyPaths=/
ReadWritePaths=/run /var/log/tp27

[Install]
WantedBy=multi-user.target
"""


def relay_addresses(relay_port: int) -> dict[str, str]:
    if not 1024 <= int(relay_port) <= 65535:
        raise ValueError("relay_port must be 1024..65535")
    addr = f"127.0.0.1:{int(relay_port)}"
    return {
        "iran_relay_server": addr,
        "foreign_relay_client_target": addr,
    }
