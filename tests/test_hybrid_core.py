import unittest

from hybrid_core import (
    BackhaulClientConfig,
    BackhaulServerConfig,
    BackhaulTransport,
    DriverReadiness,
    FRPClientConfig,
    FRPServerConfig,
    FRPTransport,
    LinkMetrics,
    PolicyOptions,
    RolloutPhase,
    SSHHybridConfig,
    ServiceProtocol,
    TransportKind,
    build_all_protocol_plans,
    build_overlay_pair,
    build_plan,
    build_service_spec,
    build_transaction_plan,
    driver_descriptor,
    driver_matrix,
    rank_candidates,
    render_authorized_key,
    render_backhaul_client,
    render_backhaul_server,
    render_frpc,
    render_frps,
    render_iran_systemd,
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

    def test_frp_quic_renderer_handles_tcp_and_udp_service_rows(self):
        server = render_frps(
            FRPServerConfig(
                bind_addr="10.253.0.2",
                bind_port=25000,
                token="test-token",
            )
        )
        self.assertIn("kcpBindPort = 25000", server)
        self.assertIn("quicBindPort = 25000", server)

        service = build_service_spec(ServiceProtocol.CISCO, port=443)
        client = render_frpc(
            FRPClientConfig(
                server_addr="10.253.0.2",
                server_port=25000,
                token="test-token",
                transport=FRPTransport.QUIC,
            ),
            service.mappings,
        )
        self.assertIn('transport.protocol = "quic"', client)
        self.assertIn('type = "tcp"', client)
        self.assertIn('type = "udp"', client)
        self.assertIn("remotePort = 443", client)

    def test_ssh_hybrid_is_restricted_to_one_reverse_relay_listener(self):
        pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey tp27"
        auth = render_authorized_key(pub, 55400)
        self.assertIn('permitlisten="127.0.0.1:55400"', auth)
        self.assertIn("restrict,port-forwarding", auth)

        unit = render_iran_systemd(
            SSHHybridConfig(
                remote_host="203.0.113.20",
                remote_ssh_port=22027,
                relay_port=55400,
            )
        )
        self.assertIn("-R 127.0.0.1:55400:127.0.0.1:55400", unit)
        self.assertIn("ExitOnForwardFailure=yes", unit)
        self.assertIn("UserKnownHostsFile=/etc/tp27/keys/known_hosts", unit)

    def test_backhaul_renderer_deduplicates_tcp_udp_twins_and_enables_udp(self):
        service = build_service_spec(ServiceProtocol.CISCO, port=443)
        server = render_backhaul_server(
            BackhaulServerConfig(
                bind_addr=":3080",
                token="test-token",
                transport=BackhaulTransport.TCPMUX,
            ),
            service.mappings,
        )
        self.assertIn('transport = "tcpmux"', server)
        self.assertIn("accept_udp = true", server)
        self.assertEqual(server.count('"443"'), 1)

        client = render_backhaul_client(
            BackhaulClientConfig(
                remote_addr="198.51.100.10:3080",
                token="test-token",
                transport=BackhaulTransport.TCPMUX,
            )
        )
        self.assertIn('remote_addr = "198.51.100.10:3080"', client)
        self.assertIn("connection_pool = 8", client)

    def test_driver_matrix_covers_every_transport(self):
        rows = driver_matrix()
        self.assertEqual({row["transport"] for row in rows}, {x.value for x in TransportKind})
        self.assertEqual(
            driver_descriptor(TransportKind.FAST_WG).readiness,
            DriverReadiness.PRODUCTION_PROVEN,
        )
        self.assertEqual(
            driver_descriptor(TransportKind.QUIC).readiness,
            DriverReadiness.CONFIG_READY,
        )

    def test_production_transaction_skips_config_only_quic(self):
        metrics = {
            TransportKind.FAST_WG: LinkMetrics(healthy=False),
            TransportKind.QUIC: LinkMetrics(healthy=True, throughput_mbps=200, rtt_ms=50),
            TransportKind.WSS_L3: LinkMetrics(healthy=True, throughput_mbps=40, rtt_ms=80),
        }
        plan = build_transaction_plan(
            ServiceProtocol.XRAY,
            metrics=metrics,
            current_transport=TransportKind.DIRECT_GRE,
        )
        self.assertEqual(plan.candidate, TransportKind.WSS_L3)
        self.assertEqual(plan.steps[0].phase, RolloutPhase.SNAPSHOT)
        self.assertEqual(plan.steps[-1].phase, RolloutPhase.ROLLBACK)
        self.assertTrue(any(step.rollback_required for step in plan.steps))

    def test_lab_transaction_can_select_config_ready_quic(self):
        metrics = {
            TransportKind.FAST_WG: LinkMetrics(healthy=False),
            TransportKind.QUIC: LinkMetrics(healthy=True, throughput_mbps=200, rtt_ms=50),
            TransportKind.WSS_L3: LinkMetrics(healthy=True, throughput_mbps=40, rtt_ms=80),
        }
        plan = build_transaction_plan(
            ServiceProtocol.XRAY,
            metrics=metrics,
            current_transport=TransportKind.DIRECT_GRE,
            allow_config_ready_for_lab=True,
        )
        self.assertEqual(plan.candidate, TransportKind.QUIC)

    def test_l2tp_transaction_never_selects_non_l3_runtime(self):
        metrics = {
            TransportKind.FAST_WG: LinkMetrics(healthy=False),
            TransportKind.WSS_L3: LinkMetrics(healthy=True, throughput_mbps=30, rtt_ms=90),
            TransportKind.QUIC: LinkMetrics(healthy=True, throughput_mbps=500, rtt_ms=20),
        }
        plan = build_transaction_plan(ServiceProtocol.L2TP, metrics=metrics)
        self.assertEqual(plan.candidate, TransportKind.WSS_L3)


if __name__ == "__main__":
    unittest.main()
