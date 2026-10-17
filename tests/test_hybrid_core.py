import unittest

from hybrid_core import (
    LinkMetrics,
    PolicyOptions,
    ServiceProtocol,
    TransportKind,
    build_all_protocol_plans,
    build_plan,
    rank_candidates,
)


class HybridCorePlannerTests(unittest.TestCase):
    def test_all_five_protocols_have_encrypted_primary(self):
        plans = build_all_protocol_plans()
        self.assertEqual(set(plans), set(ServiceProtocol))
        for protocol, plan in plans.items():
            self.assertIsNotNone(plan.primary, protocol.value)

    def test_l2tp_requires_full_l3_and_esp(self):
        ranked = rank_candidates(ServiceProtocol.L2TP)
        compatible = {row.transport for row in ranked if row.compatible}
        self.assertIn(TransportKind.FAST_WG, compatible)
        self.assertIn(TransportKind.WSS_L3, compatible)
        self.assertIn(TransportKind.BACKHAUL_TUN, compatible)
        self.assertNotIn(TransportKind.QUIC, compatible)
        self.assertNotIn(TransportKind.KCP, compatible)
        self.assertNotIn(TransportKind.TCPMUX, compatible)
        self.assertNotIn(TransportKind.SSH_HYBRID, compatible)

    def test_wireguard_never_uses_tcp_only_ssh_hybrid(self):
        plan = build_plan(ServiceProtocol.WIREGUARD)
        selected = set(plan.all_transports)
        self.assertNotIn(TransportKind.SSH_HYBRID, selected)

    def test_unhealthy_fast_wg_fails_over(self):
        metrics = {
            TransportKind.FAST_WG: LinkMetrics(healthy=False),
            TransportKind.QUIC: LinkMetrics(
                healthy=True,
                rtt_ms=75,
                jitter_ms=3,
                loss_pct=0,
                throughput_mbps=80,
            ),
        }
        plan = build_plan(ServiceProtocol.XRAY, metrics=metrics)
        self.assertEqual(plan.primary, TransportKind.QUIC)

    def test_proven_fast_wg_remains_preferred_with_measured_110mbps(self):
        metrics = {
            TransportKind.FAST_WG: LinkMetrics(
                healthy=True,
                rtt_ms=72,
                jitter_ms=0.2,
                loss_pct=0,
                throughput_mbps=110.155,
            ),
            TransportKind.QUIC: LinkMetrics(
                healthy=True,
                rtt_ms=70,
                jitter_ms=1,
                loss_pct=0,
                throughput_mbps=90,
            ),
        }
        plan = build_plan(ServiceProtocol.XRAY, metrics=metrics)
        self.assertEqual(plan.primary, TransportKind.FAST_WG)

    def test_plain_gre_is_excluded_by_default(self):
        ranked = rank_candidates(ServiceProtocol.XRAY)
        direct = next(row for row in ranked if row.transport == TransportKind.DIRECT_GRE)
        self.assertFalse(direct.compatible)
        self.assertIn("encryption_required", direct.reasons)

    def test_plain_gre_can_be_explicitly_enabled(self):
        ranked = rank_candidates(
            ServiceProtocol.XRAY,
            options=PolicyOptions(require_encryption=False),
        )
        direct = next(row for row in ranked if row.transport == TransportKind.DIRECT_GRE)
        self.assertTrue(direct.compatible)


if __name__ == "__main__":
    unittest.main()
