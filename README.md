# TP27 Core v0.2

Standalone encrypted L3 tunnel core for **one Iran ingress -> multiple foreign exits**.

TP27 is independent from TwinPanel. The Iran<->Exit transport in v0.2 is **Linux TUN over mutually-authenticated TLS/TCP**; WireGuard is not used as the TP27 transport.

## Install

Iran node:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- iran
```

Exit node can be installed manually:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- exit
```

or automatically from the Iran node during pairing.

## Automatic pairing

Run on the Iran node:

```bash
tp27 add-exit 168.222.49.180
```

TP27 automatically installs/upgrades the Exit over SSH, allocates an isolated /30 tunnel, creates per-link certificates, exchanges public certificates with SCP, starts both systemd units, and verifies the data plane with ping.

No WireGuard public/private key exchange is involved.

## Commands

```bash
tp27 status
tp27 doctor
tp27 logs exit01
tp27 add-exit <EXIT_IP>
```

Each additional Exit gets its own TUN interface and systemd unit.

## Migration from v0.1

The installer backs up `/etc/tp27/config.json` and removes only old TP27 interfaces recorded in that file whose names begin with `tp27`. Unrelated VPN interfaces such as `wg1` are not touched.

## Current transport

v0.2 ships the first production transport: mTLS/TCP. Additional transports and automatic failover can be added behind the same TP27 control plane without changing the VPN services hosted on Exit servers.
