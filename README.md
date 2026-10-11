# TP27 Core

Standalone multi-exit L3 tunnel manager for Ubuntu/Debian.

TP27 is intentionally independent from TwinPanel and any existing VPN panel.

## Layout on server

- `/etc/tp27/` configuration and node keys
- `/var/lib/tp27/` runtime state
- `/var/log/tp27/` logs
- `/usr/local/lib/tp27/` installed Python programs
- `/usr/local/bin/tp27ctl` CLI
- `tp27-agent.service` health/auto-heal daemon
- `TP27_PRE`, `TP27_POST`, `TP27_FWD` dedicated iptables chains

It does not edit TwinPanel files or services.

## Supported topology

    Clients
       |
       v
    Iran Node
      |  \
      |   \__ tp27e02 -> Exit-02
      |
      \______ tp27e01 -> Exit-01

Each exit has an independent WireGuard L3 path. TCP and UDP service
forwarding can be mapped independently to each exit.

For L2TP/IPsec, one active exit at a time should own UDP 500/4500/1701
on a single Iran public IP.

## Install

Iran node:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- iran
```

Exit node:

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/install.sh | bash -s -- exit
```

After installation:

```bash
tp27ctl status
tp27ctl doctor
```

## Pair an exit

On the exit:

```bash
tp27ctl exit-init --id exit01 --listen-port 51877 --tunnel-ip 10.254.250.1/30
tp27ctl exit-info
```

Copy the printed exit public key.

On Iran:

```bash
tp27ctl add-exit \
  --id exit01 \
  --endpoint 168.222.49.180 \
  --port 51877 \
  --exit-public-key 'EXIT_PUBLIC_KEY' \
  --local-ip 10.254.250.2/30 \
  --remote-ip 10.254.250.1
```

The Iran command prints `IRAN_PUBLIC_KEY`.

Back on Exit:

```bash
tp27ctl exit-add-peer \
  --peer-public-key 'IRAN_PUBLIC_KEY' \
  --peer-ip 10.254.250.2/32
```

Then verify:

```bash
tp27ctl status
ping -c 4 10.254.250.1
```

## Add service forwarding

Example for the first exit:

```bash
tp27ctl forward-add --exit exit01 --proto tcp --listen 8897 --target 8897
tp27ctl forward-add --exit exit01 --proto udp --listen 8897 --target 8897
tp27ctl forward-add --exit exit01 --proto tcp --listen 9988 --target 9988
tp27ctl forward-add --exit exit01 --proto udp --listen 55821 --target 55821
```

L2TP/IPsec active exit:

```bash
tp27ctl l2tp-active --exit exit01
```

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/matin111/tp27-core/main/uninstall.sh | bash
```

The uninstall script only removes TP27-owned services, interfaces, files,
and dedicated firewall chains.
