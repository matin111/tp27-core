# TP27 Hybrid Core — Runtime Readiness

This document is intentionally conservative. `config_ready` does not mean a
transport is allowed to replace production traffic. Promotion requires an actual
runtime, health gates, benchmark gates and rollback validation.

## Runtime matrix

| Transport | Readiness | L3 | Intended use |
| --- | --- | ---: | --- |
| Fast WireGuard overlay | `production_proven` | yes | Preferred secure L3 fast path when the carrier passes UDP reliably |
| Direct GRE | `runtime_present` | yes | Existing baseline/carrier; unencrypted and not selected by default secure policy |
| Native WSS L3 | `runtime_present` | yes | New clean-room TUN over WebSocket/TLS fallback; lab validation required |
| FRP QUIC | `config_ready` | no | TCP/UDP reverse service relay over QUIC |
| FRP KCP | `config_ready` | no | TCP/UDP reverse service relay for lossy UDP paths |
| TCPMux relay | `config_ready` | no | FRP/Public Backhaul multiplexed TCP fallback |
| SSH Hybrid | `config_ready` | no | Restricted reverse-SSH control path with relay behind it |
| Independent Backhaul-compatible TUN | `planned` | yes | Future TP27-owned L3 implementation; no dependency on private binaries |

The machine-readable source of this table is `hybrid_core/drivers.py` and can be
printed offline with:

```bash
python bin/tp27-hybridctl.py drivers
```

## Five-service support matrix

| Service | Fast WG | Native WSS L3 | FRP QUIC/KCP | TCPMux relay | SSH Hybrid | Native ESP |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cisco/OpenConnect | yes | yes | yes | yes | TCP fallback only | n/a |
| OpenVPN | yes | yes | yes | yes | TCP mode only | n/a |
| WireGuard | yes | yes | UDP relay candidate | UDP encapsulation candidate | no | n/a |
| L2TP/IPsec | yes | yes | no full-L3 guarantee | no full-L3 guarantee | no | Fast WG/WSS L3 |
| Xray/V2Ray | yes | yes | yes | yes | TCP fallback | n/a |

`hybrid_core/services.py` models Cisco TCP+DTLS/UDP, OpenVPN TCP+UDP,
WireGuard UDP, L2TP UDP 500/4500/1701 plus IP protocol 50, and arbitrary Xray
TCP/UDP ports.

## Native WSS L3 core

`bin/tp27-wss-l3.py` is a clean-room TP27 runtime and does not use Hashem code or
GOST. It creates a point-to-point TUN interface and transports raw IP packets in
WebSocket binary frames over TLS.

Properties implemented in source:

- TUN/IPv4 point-to-point data plane.
- TLS with verified client mode by default.
- token authentication using constant-time comparison.
- RFC 6455 masking direction enforcement.
- binary packet frames with a 65,535 byte hard limit.
- TCP keepalive and WebSocket ping/pong heartbeat.
- client reconnect loop.
- single active point-to-point session per runtime instance.
- no shell interpolation for network commands.
- MTU validation (1000–1400).

The runtime is deliberately only `runtime_present`, not `production_proven`.
The existing production Fast-WG path remains the known-good reference until a
separate two-VPS WSS lab proves throughput, loss, reconnect and real VPN service
behaviour.

## Health gates

`hybrid_core/health.py` makes cutover protocol-aware instead of relying only on
`systemctl active` or a ping:

- Cisco: TCP control and UDP/DTLS port verification + service probe.
- OpenVPN: TCP and UDP deployment paths + service probe.
- WireGuard: UDP plus optional maximum handshake age.
- L2TP/IPsec: L3 peer health, UDP 500/4500/1701, optional strict native ESP
  verification, plus service probe.
- Xray: every configured TCP/UDP service port plus service probe.

## Transactional rollout

`hybrid_core/transaction.py` generates the only rollout sequence that future
production adapters should execute:

1. snapshot current state,
2. stage candidate under an isolated id,
3. start candidate in parallel,
4. pass protocol-aware health gates,
5. benchmark candidate,
6. cut over only service mappings,
7. verify real service traffic,
8. persist only after verification,
9. restore the exact snapshot on any failed gate.

The CLI can show this sequence without executing anything:

```bash
python bin/tp27-hybridctl.py rollout \
  --protocol xray \
  --current direct_gre \
  --metrics config/hybrid-metrics.example.json
```

Production transaction planning ignores `config_ready` transports. The `--lab`
flag intentionally relaxes that rule for isolated test nodes only.

## Reversible forwarding rules

`hybrid_core/rules.py` renders iptables arguments as arrays rather than shell
strings. It can generate candidate DNAT/FORWARD/SNAT rules for all five service
profiles and the exact reverse-order removals for rollback. L2TP can additionally
render native ESP (`IP protocol 50`) rules when strict non-NAT-T support is
required.

The renderer does not execute iptables. Snapshot/restore remains the authoritative
production rollback strategy; candidate-owned rule deletion is a secondary clean
rollback path.

## Current safety boundary

Nothing in this branch has been installed on the active Iran/Foreign servers.
The draft PR must stay separate from `main` until the isolated lab phase passes.
In particular, the proven GRE/FOU carrier that currently carries the Fast-WG path
must never be stopped merely because service traffic has moved into an overlay.
