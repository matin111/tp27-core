import unittest

from hybrid_core import ProbeObservation, ServiceProtocol, evaluate_protocol_gate


class HybridHealthGateTests(unittest.TestCase):
    def test_cisco_requires_tcp_and_dtls_udp(self):
        ok = evaluate_protocol_gate(
            ServiceProtocol.CISCO,
            ProbeObservation(
                tcp_ports=frozenset({443}),
                udp_ports=frozenset({443}),
                service_ok=True,
            ),
            port=443,
        )
        self.assertTrue(ok.passed)

        bad = evaluate_protocol_gate(
            ServiceProtocol.CISCO,
            ProbeObservation(tcp_ports=frozenset({443}), service_ok=True),
            port=443,
        )
        self.assertFalse(bad.passed)
        self.assertIn("udp_unverified:443", bad.failures)

    def test_openvpn_dual_mode_gate(self):
        result = evaluate_protocol_gate(
            ServiceProtocol.OPENVPN,
            ProbeObservation(
                tcp_ports=frozenset({1194}),
                udp_ports=frozenset({1194}),
                service_ok=True,
            ),
            port=1194,
        )
        self.assertTrue(result.passed)

    def test_wireguard_requires_fresh_handshake_when_requested(self):
        fresh = evaluate_protocol_gate(
            ServiceProtocol.WIREGUARD,
            ProbeObservation(
                udp_ports=frozenset({51820}),
                service_ok=True,
                handshake_age_s=8,
            ),
            port=51820,
            max_handshake_age_s=30,
        )
        self.assertTrue(fresh.passed)

        stale = evaluate_protocol_gate(
            ServiceProtocol.WIREGUARD,
            ProbeObservation(
                udp_ports=frozenset({51820}),
                service_ok=True,
                handshake_age_s=120,
            ),
            port=51820,
            max_handshake_age_s=30,
        )
        self.assertFalse(stale.passed)
        self.assertIn("handshake_stale_or_missing", stale.failures)

    def test_l2tp_accepts_natt_mode_when_udp_and_l3_are_good(self):
        result = evaluate_protocol_gate(
            ServiceProtocol.L2TP,
            ProbeObservation(
                peer_reachable=True,
                udp_ports=frozenset({500, 4500, 1701}),
                service_ok=True,
            ),
            allow_natt_only=True,
        )
        self.assertTrue(result.passed)

    def test_l2tp_native_esp_can_be_enforced(self):
        missing = evaluate_protocol_gate(
            ServiceProtocol.L2TP,
            ProbeObservation(
                peer_reachable=True,
                udp_ports=frozenset({500, 4500, 1701}),
                service_ok=True,
            ),
            allow_natt_only=False,
        )
        self.assertFalse(missing.passed)
        self.assertIn("ipproto_unverified:50", missing.failures)

        good = evaluate_protocol_gate(
            ServiceProtocol.L2TP,
            ProbeObservation(
                peer_reachable=True,
                udp_ports=frozenset({500, 4500, 1701}),
                ip_protocols=frozenset({50}),
                service_ok=True,
            ),
            allow_natt_only=False,
        )
        self.assertTrue(good.passed)

    def test_xray_multiple_ports_are_all_gated(self):
        result = evaluate_protocol_gate(
            ServiceProtocol.XRAY,
            ProbeObservation(
                tcp_ports=frozenset({8080, 8443}),
                udp_ports=frozenset({8080, 8443}),
                service_ok=True,
            ),
            ports=(8080, 8443),
        )
        self.assertTrue(result.passed)


if __name__ == "__main__":
    unittest.main()
