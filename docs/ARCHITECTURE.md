# Architecture

TP27 separates control state from the data plane.

## Iran node

Each exit gets its own WireGuard interface:

- `tp27exit01`
- `tp27exit02`
- `tp27exit03`

The public ingress remains on the Iran server. Dedicated `TP27_*`
iptables chains contain only TP27 rules.

## Exit node

The existing VPN services are not modified. TP27 delivers traffic to the
exit's tunnel address. Services that listen on `0.0.0.0` can receive that
traffic directly.

## Multi-exit

TCP/UDP services that support arbitrary public ports can be assigned
independent Iran ingress ports for each exit.

L2TP/IPsec uses UDP 500/4500/1701 and therefore only one active exit can
own those ports on one Iran public IPv4 address at a time. `l2tp-active`
switches those mappings without touching other service mappings.

## Health

`tp27-agent.service` checks each exit every 10 seconds using both:
- WireGuard handshake freshness
- ICMP reachability through the tunnel interface

After three consecutive failures it restarts only the affected
`wg-quick@<interface>` unit.
