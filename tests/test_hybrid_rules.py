import unittest

from hybrid_core import ServiceProtocol, build_cutover_rules, build_service_spec


class HybridRuleRendererTests(unittest.TestCase):
    def _plan(self, protocol, **kwargs):
        spec = build_service_spec(protocol, **kwargs)
        return build_cutover_rules(
            spec,
            target_ip="10.250.177.1",
            source_ip="10.250.177.2",
            output_interface="wgi67765",
            include_native_ip_protocols=(protocol == ServiceProtocol.L2TP),
        )

    def test_cisco_has_tcp_and_udp_dnat(self):
        plan = self._plan(ServiceProtocol.CISCO, port=443)
        commands = [" ".join(x) for x in plan.add_commands()]
        self.assertTrue(any("-p tcp --dport 443" in x for x in commands))
        self.assertTrue(any("-p udp --dport 443" in x for x in commands))

    def test_openvpn_has_tcp_and_udp_dnat(self):
        plan = self._plan(ServiceProtocol.OPENVPN, port=1194)
        commands = [" ".join(x) for x in plan.add_commands()]
        self.assertTrue(any("-p tcp --dport 1194" in x for x in commands))
        self.assertTrue(any("-p udp --dport 1194" in x for x in commands))

    def test_wireguard_has_udp_only_service_mapping(self):
        plan = self._plan(ServiceProtocol.WIREGUARD, port=51820)
        commands = [" ".join(x) for x in plan.add_commands()]
        self.assertTrue(any("-p udp --dport 51820" in x for x in commands))
        self.assertFalse(any("-p tcp --dport 51820" in x for x in commands))

    def test_l2tp_renders_udp_ports_and_native_esp(self):
        plan = self._plan(ServiceProtocol.L2TP)
        commands = [" ".join(x) for x in plan.add_commands()]
        for port in (500, 4500, 1701):
            self.assertTrue(any(f"-p udp --dport {port}" in x for x in commands))
        self.assertTrue(any("-p 50 -j DNAT" in x for x in commands))
        self.assertTrue(any("-p 50 -j SNAT" in x for x in commands))

    def test_xray_multiple_ports_render_tcp_udp(self):
        plan = self._plan(ServiceProtocol.XRAY, ports=(8080, 8443))
        commands = [" ".join(x) for x in plan.add_commands()]
        for port in (8080, 8443):
            self.assertTrue(any(f"-p tcp --dport {port}" in x for x in commands))
            self.assertTrue(any(f"-p udp --dport {port}" in x for x in commands))

    def test_rollback_removes_every_candidate_rule_in_reverse_order(self):
        plan = self._plan(ServiceProtocol.XRAY, ports=(8080,))
        add = plan.add_commands()
        remove = plan.remove_commands()
        self.assertEqual(len(add), len(remove))
        self.assertEqual(add[0][6:], remove[-1][6:])
        self.assertEqual(remove[-1][5], "-D")


if __name__ == "__main__":
    unittest.main()
