# TP27 Hybrid Core v1

This branch builds the new transport-selection core without installing or changing anything on production servers.

## Target VPN services

The policy engine treats these five services as first-class requirements:

| Service | TCP | UDP | L3 | Special requirements |
| --- | --- | --- | --- | --- |
| Cisco / OpenConnect | yes | yes | no | TCP control + DTLS/UDP data |
| OpenVPN | yes | yes | no | supports both deployment modes |
| WireGuard | no | yes | no | UDP-only service data plane |
| L2TP / IPsec | no | yes | yes | UDP 500/4500/1701 + native ESP/IP protocol 50 |
| Xray / V2Ray | yes | yes | no | arbitrary TCP/UDP service ports |

The service forwarding specs live in `hybrid_core/services.py` and can be converted directly to the existing TP27 `portplan.py` row format. L2TP also records native ESP separately because ESP is not a TCP/UDP port mapping.

## Transport catalog

The first policy catalog contains:

- `fast_wg` — WireGuard L3 overlay over an already-working carrier. This is marked **proven** because the production validation showed the exact architecture can carry TCP efficiently while the carrier remains UDP-capable.
- `quic` — encrypted multiplexed UDP transport.
- `kcp` — loss-tolerant UDP transport.
- `wss_l3` — packet/L3 transport over WebSocket + TLS.
- `backhaul_tun` — independent TUN-compatible L3 mode; the TP27 design must not depend on a private `backhaul_premium` binary.
- `tcpmux` — pooled/multiplexed TCP relay with UDP encapsulation support.
- `direct_gre` — kernel L3 baseline. It is unencrypted and therefore excluded by the default secure policy.
- `ssh_hybrid` — restricted SSH reverse forwarding fallback. It is intentionally not considered full-service for UDP/L3 protocols.

## Capability-first selection

`hybrid_core/planner.py` does not select a transport merely because its socket is open. It first checks protocol capabilities, then scores healthy candidates.

Hard compatibility checks include:

- TCP support when the service needs TCP.
- UDP support when the service needs UDP.
- L3 packet carriage for L2TP/IPsec.
- ESP preservation for native IPsec.
- encryption when the default secure policy is active.
- explicit blocks and experimental-policy gates.

The health score then penalizes loss, RTT, jitter and recent failures while giving a bounded throughput bonus. Reliability therefore wins over a transport that is fast only in a single synthetic sample.

## Failover behaviour

Failover is ordered from measured compatible candidates, not a global fixed list. For example:

- WireGuard will never fail over to `ssh_hybrid` because it needs UDP.
- L2TP/IPsec will never fail over to plain QUIC/KCP/TCPMux proxy modes because it needs a complete L3/ESP path.
- Xray can use Fast WG, QUIC, KCP, WSS L3, Backhaul TUN or TCPMux, depending on measurements.
- A transport marked unhealthy receives a hard score penalty and cannot remain primary merely because it has a high static priority.

## Fast WireGuard overlay renderer

`hybrid_core/fast_wg.py` contains a side-effect-free renderer for the architecture already validated in TP27:

```text
service traffic
    -> WireGuard inner interface
    -> UDP packets
    -> existing carrier (for example GRE/FOU inner path)
    -> WireGuard peer
    -> service on the Foreign node
```

The renderer validates:

- both WG addresses share one subnet,
- peer addresses match,
- public keys match the opposite peer,
- MTU range,
- listen-port range,
- keepalive range.

It only returns configuration text; it does not invoke `wg`, `ip`, `iptables` or `systemctl`.

## Offline CLI

`bin/tp27-hybridctl.py` is deliberately read-only/offline in v1.

Examples:

```bash
python bin/tp27-hybridctl.py plan --protocol all
python bin/tp27-hybridctl.py plan --protocol xray --metrics config/hybrid-metrics.example.json
python bin/tp27-hybridctl.py plan --protocol l2tp --block fast_wg
```

The CLI prints JSON containing the selected primary, fallback order, compatibility flags, scores and reasons.

## Production safety rule

No code in `hybrid_core/` performs host mutation. Deployment is a later adapter layer and must follow the TP27 production rule:

1. snapshot current state,
2. build a candidate in parallel,
3. prove L3/service health,
4. benchmark it,
5. cut over only the service mapping,
6. keep the old carrier alive until rollback is no longer needed.

This is especially important for Fast WG over GRE/FOU: the old GRE/FOU link is still the carrier and must not be stopped after service traffic moves into WireGuard.

## Current implementation boundary

Implemented in this branch:

- five-service capability model,
- transport capability catalog,
- health/throughput-aware policy ranking,
- protocol-specific TCP/UDP/L3 forwarding specs,
- L2TP native ESP requirement,
- proven Fast-WG config renderer,
- offline CLI,
- unit tests and CI.

The branch intentionally does **not** yet install binaries or mutate production networking. Runtime adapters for QUIC, KCP, WSS packet mode, TCPMux, Backhaul-compatible TUN and SSH Hybrid should implement the same driver contract and be tested in network namespaces before any panel provisioning path is allowed to call them.
