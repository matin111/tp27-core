import unittest

from hybrid_core import (
    LinkMetrics,
    PolicyOptions,
    ServiceProtocol,
    TransportKind,
    build_all_protocol_plans,
    build_overlay_pair,
    build_plan,
    build_service_spec,
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

    def test_cisco_maps_tcp_and_dtls_udp(self):
        spec = build_service_spec(ServiceProtocol.CISCO, port=443)
        rows = {(x.proto, x.listen_port, x.target_port) for x in spec.mappings}
        self.assertEqual(rows, {("tcp", 443, 443), ("udp", 443, 443)})

    def test_openvpn_dual_protocol_mapping(self):
        spec = build_service_spec(ServiceProtocol.OPENVPN, port=1194)
        rows = {(x.proto, x.listen_port) for x in spec.mappings}
        self.assertEqual(rows, {("tcp", 1194), ("udp", 1194)})

    def test_wireguard_is_udp_only(self):
        spec = build_service_spec(ServiceProtocol.WIREGUARD, port=51820)
        self.assertEqual(len(spec.mappings), 1)
        self.assertEqual(spec.mappings[0].proto, "udp")

    def test_l2tp_has_natt_ports_and_native_esp_requirement(self):
        spec = build_service_spec(ServiceProtocol.L2TP)
        ports = {(x.proto, x.listen_port) for x in spec.mappings}
        self.assertEqual(ports, {("udp", 500), ("udp", 4500), ("udp", 1701)})
        self.assertTrue(spec.requires_l3)
        self.assertEqual(spec.ip_protocols, (50,))

    def test_xray_can_map_multiple_tcp_udp_ports(self):
        spec = build_service_spec(ServiceProtocol.XRAY, ports=(8080, 8443))
        rows = {(x.proto, x.listen_port) for x in spec.mappings}
        self.assertEqual(
            rows,
            {
                ("tcp", 8080),
                ("udp", 8080),
                ("tcp", 8443),
                ("udp", 8443),
            },
        )

    def test_fast_wg_pair_matches_proven_overlay_shape(self):
        pair = build_overlay_pair(
            iran_private_key="iran-private",
            iran_public_key="iran-public",
            foreign_private_key="foreign-private",
            foreign_public_key="foreign-public",
            iran_address="10.250.177.2/30",
            foreign_address="10.250.177.1/30",
            foreign_carrier_endpoint="10.253.0.9:51888",
            listen_port=51888,
            mtu=1280,
            keepalive=5,
        )
        rendered = pair.render()
        self.assertIn("Address = 10.250.177.2/30", rendered["iran"])
        self.assertIn("Endpoint = 10.253.0.9:51888", rendered["iran"])
        self.assertIn("PersistentKeepalive = 5", rendered["iran"])
        self.assertIn("ListenPort = 51888", rendered["foreign"])
        self.assertNotIn("Endpoint =", rendered["foreign"])


if __name__ == "__main__":
    unittest.main()
