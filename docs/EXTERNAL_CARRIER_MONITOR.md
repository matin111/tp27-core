# TP27 External Carrier Monitor v1

## Goal

Expose health for externally managed network carriers inside the TP27 panel without changing their configuration or data plane.

The adapter is deliberately read-only for networking and services. It does **not** create tunnels, interfaces, routes, firewall rules, transport configuration, source-address rewriting, or systemd start/restart/stop operations.

## Panel URL

After the panel is installed or updated from a revision containing this feature:

```text
https://<TP27-PANEL-IP>:8787/external-carriers
```

The route uses the same TP27 admin session as the main panel.

## What can be registered

Each monitor record contains:

- `id`: TP27-local monitor identifier
- `interface`: an existing Linux network interface
- `service`: optional existing systemd unit name
- `peer_ip`: optional peer address for an interface-bound ping health check
- `note`: optional operator note

Registration stores monitoring metadata only in:

```text
/etc/tp27/external-carriers.json
```

The file is written mode `0600`.

## Health checks

For each record TP27 reads:

```text
ip -br link show dev <interface>
ip -br addr show dev <interface>
ip -s link show dev <interface>
systemctl is-active <service>     # only when configured
ping -I <interface> ... <peer_ip> # only when configured
```

A monitor is healthy when the interface exists and every optional configured health gate that was actually requested passes.

## API

```text
GET    /api/external-carriers
POST   /api/external-carriers
DELETE /api/external-carriers/{carrier_id}
```

All endpoints require the existing authenticated TP27 panel session.

### POST body

```json
{
  "id": "carrier01",
  "interface": "wg-ext0",
  "service": "my-carrier.service",
  "peer_ip": "10.10.10.2",
  "note": "externally managed path"
}
```

`service`, `peer_ip`, and `note` are optional.

## Operational boundary

TP27 v1 monitor deliberately does not manage the external carrier lifecycle. Creation, configuration, upgrades, transport selection, and service actions remain outside this adapter. This keeps the existing TP27 production data plane and its current GRE/FOU/OpenVPN/GOST/Framed-TLS logic unchanged.

## Rollback

The feature is self-contained in the panel code. Removing all monitor records deletes only TP27 monitor metadata and does not touch the underlying network interface or service.
