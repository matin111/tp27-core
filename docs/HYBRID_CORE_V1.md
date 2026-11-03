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

## FRP transport renderer

`hybrid_core/frp.py` is a clean TP27 renderer for the public FRP configuration surface used by the relay transports. It can render:

- server bind + KCP bind + QUIC bind,
- token authentication,
- pool limits,
- client `tcp`, `kcp`, `quic`, and `websocket` transports,
- TCP and UDP proxy rows generated from the same five-service forwarding specs.

This gives TP27 a source-controlled way to build KCP/QUIC relay candidates without copying Hashem's implementation.

## SSH Hybrid renderer

`hybrid_core/ssh_hybrid.py` implements the configuration side of the SSH-hybrid method:

```text
Foreign frpc -> 127.0.0.1:relay_port
                  |
                  | reverse OpenSSH -R
                  v
Iran 127.0.0.1:relay_port -> frps
```

The renderer produces a persistent SSH systemd unit and a restricted `authorized_keys` entry. The key is limited with `restrict`, `port-forwarding`, and a single `permitlisten=127.0.0.1:<relay_port>` permission. Host-key persistence uses a dedicated TP27 known-hosts file instead of writing to the normal root SSH profile.

SSH Hybrid is intentionally a TCP fallback and is not considered a full WireGuard or L2TP carrier.

## Public Backhaul renderer

`hybrid_core/backhaul.py` renders configuration for the public Backhaul feature set rather than depending on Hashem's optional private `backhaul_premium` binary. It supports the public relay transports:

- `tcp`
- `tcpmux`
- `ws`
- `wss`
- `wsmux`
- `wssmux`

The renderer deduplicates TCP/UDP twins that share the same service port and turns on Backhaul's UDP-over-transport mode when a service spec contains UDP. WSS requires explicit certificate/key paths. This renderer is useful for Cisco, OpenVPN, WireGuard UDP port relay, and Xray proxy traffic. It is **not** treated as a complete L2TP/IPsec L3 replacement because public Backhaul relay mode does not preserve native ESP/IP protocol 50.

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
- FRP TCP/KCP/QUIC/WebSocket server/client config renderer,
- restricted SSH Hybrid config/systemd renderer,
- public Backhaul TCP/TCPMux/WS/WSS/WSMux/WSSMux config renderer,
- offline CLI,
- unit tests and CI.

The branch intentionally does **not** yet install binaries or mutate production networking. The remaining runtime layer is the deployment/health adapter that places generated config, starts candidates in parallel, benchmarks them, and performs transactional cutover/rollback. WSS packet mode already has an existing TP27/GOST TUN core in the repository but still needs to be normalized behind the same Hybrid driver contract before production provisioning calls it. A true independent Backhaul-compatible TUN remains a separate implementation task because TP27 will not depend on a private `backhaul_premium` binary.
