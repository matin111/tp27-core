# TP27 IPv6 Fast Core v1

TP27 IPv6 Fast Core uses native public IPv6 only as the outer carrier while keeping the existing TP27 point-to-point IPv4 address pool on the inside. This preserves the current port-mapping, health, benchmark and failover contracts.

Order:

1. Native IPv6 preflight on Iran and Exit.
2. IP6GRE over IPv6 (kernel fast path).
3. WireGuard over IPv6 when IP6GRE is unavailable or unhealthy.
4. Existing TP27 IPv4 transports unchanged as fallback.

The feature is fail-open: absence of native IPv6, an old/unsupported agent, or a failed IPv6 candidate must not block the existing TP27 builder.

Default MTUs:

- IP6GRE: 1420
- WireGuard over IPv6: 1380

No existing tunnel is migrated automatically. IPv6 transports are selected only while creating a new tunnel and only after bidirectional health succeeds.

## SIT / 6in4 bootstrap

When the Iran node has no native IPv6, TP27 may test an IPv6 bootstrap over IPv4 protocol 41:

1. Create a point-to-point SIT/6in4 interface between the two public IPv4 endpoints.
2. Assign deterministic TP27 ULA `/127` addresses only to that private bootstrap link.
3. Run IP6GRE over the SIT IPv6 pair while preserving the existing TP27 IPv4 `/30` payload network.
4. Accept the candidate only after bidirectional TP27 peer health succeeds.
5. On any failure, remove the service/interface/firewall state and continue with the existing IPv4 transport builder.

The outer firewall exception is scoped to IP protocol 41 and the exact Iran/Exit IPv4 pair. No existing tunnel is migrated. SIT/IP6GRE is health-selected, not assumed to be faster than direct GRE; throughput should be verified on the real pair before treating it as a preferred production carrier.
