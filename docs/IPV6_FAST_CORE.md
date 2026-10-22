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
