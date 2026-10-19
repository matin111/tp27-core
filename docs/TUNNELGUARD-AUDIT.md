# TunnelGuard audit for TP27

Source reviewed: `Mehdi682007/TunnelGuard` (main, 2026-10-19).

This document records concepts worth adapting to TP27. It does **not** vendor or copy TunnelGuard source code. No root `LICENSE` file was observed during the audit, so implementation should remain original and architecture-specific to TP27.

## Adopted now

### 1. Fleet Doctor

TP27 now exposes a read-only Doctor entry in the panel UI. It reuses TP27's authenticated `/api/status` and `/api/exits/{id}/diagnose` endpoints and summarizes:

- agent online/offline state;
- local link service state;
- interface state;
- peer health;
- degraded multi-channel pools;
- missing Port Mapping warnings.

The check does not restart or mutate services.

### 2. Bounded stability check

Each tunnel diagnosis now offers a 12-round bounded stability test. It reports:

- success percentage;
- maximum consecutive failure streak;
- number of degraded rounds for multi-channel pools;
- last actionable failure reason.

The test is intentionally bounded and cancelable when the diagnosis modal closes. It does not claim bandwidth or packet-loss precision beyond the existing TP27 health endpoint.

### 3. Missing Port Mapping visibility

A healthy data plane with zero Port Mapping is now shown explicitly in the tunnel list and diagnosis view. This prevents a healthy GRE/FOU link from being mistaken for a working user-service path.

## High-value follow-ups

### Transactional maintenance and rollback

TunnelGuard's snapshot/recover model is useful. TP27 should add a native transaction layer for agent/panel upgrades and transport rebuilds:

1. snapshot config and unit files;
2. mark transaction pending;
3. apply replacement atomically;
4. verify service + data plane + required mappings;
5. commit on success, rollback automatically on failure;
6. recover an interrupted pending transaction after reboot.

This should be implemented server-side, not only in the browser.

### Quality-aware failover with hysteresis

For multiple independent paths to the same destination, TP27 can add a quality policy using recent health samples, with:

- minimum improvement margin before switching;
- multiple confirming rounds;
- cooldown after a switch;
- immediate failover only when the active path is unhealthy.

This avoids route flapping and should remain separate from provisioning health gates.

### First-class reverse and transport diversity

TunnelGuard's useful architectural idea is transport diversity, not simply a long protocol list. TP27 should evaluate optional fallback families with genuinely different dependencies, especially:

- reverse initiation (exit -> Iran) where inbound reachability differs;
- VXLAN/IPIP as kernel alternatives where supported;
- Chisel/Rathole-style TCP/WebSocket fallbacks where a userspace carrier is preferable.

Any new core must be pinned to a version, verified by checksum, isolated in its own systemd unit, and pass TP27's strict health gate before becoming active.

## Deliberately not imported

- Spoof/Paqet are not enabled by this change. Their upstream maturity and network prerequisites make them inappropriate as automatic defaults.
- No automatic claim is made that a transport bypasses a specific ISP restriction.
- No long-running soak test is started automatically from the dashboard.
- No existing healthy TP27 data plane is restarted by Doctor or the stability test.

## Acceptance criteria for this phase

- Existing create/delete/repair flows remain unchanged.
- Doctor is read-only.
- Stability probing is bounded to 12 rounds.
- A tunnel can be ONLINE while still warning that no Port Mapping exists.
- Multi-channel degraded state remains visible.
