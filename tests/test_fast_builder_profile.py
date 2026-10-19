#!/usr/bin/env python3
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'panel'))

import provision_resilient as resilient
import provision_multicore as multicore


class FastBuilderProfileTests(unittest.TestCase):
    def test_default_profile_uses_short_high_probability_port_sets(self):
        self.assertFalse(resilient.DEEP_SCAN)
        self.assertEqual(resilient.FOU_PORT_CANDIDATES, (443, 2083, 8443))
        self.assertEqual(resilient.TLS_PORT_CANDIDATES, (443, 80, 8443))
        self.assertEqual(multicore.OPENVPN_PORT_CANDIDATES, (443, 2083, 80))
        self.assertEqual(multicore.GOST_PORT_CANDIDATES, (443, 8443, 2083))

    def test_two_consecutive_total_loss_windows_fail_fast(self):
        calls = []
        output = (
            'PING 10.253.0.9 (10.253.0.9) 56(84) bytes of data.\n\n'
            '--- 10.253.0.9 ping statistics ---\n'
            '5 packets transmitted, 0 received, 100% packet loss, time 3000ms\n'
        )

        def fake_run(cmd, check=False):
            calls.append(cmd)
            return 1, output, ''

        with patch.object(resilient, 'run', side_effect=fake_run), \
             patch.object(resilient.asyncio, 'sleep', new=AsyncMock(return_value=None)):
            healthy, details = asyncio.run(
                resilient._health_peer('t27test', '10.253.0.9', tries=8)
            )

        self.assertFalse(healthy)
        self.assertEqual(len(calls), 2)
        self.assertEqual(details.count('HEALTH_WINDOW='), 2)

    def test_ping_window_is_accelerated_without_relaxing_loss_limit(self):
        self.assertEqual(resilient.HEALTH_PING_COUNT, 5)
        self.assertEqual(resilient.HEALTH_MAX_LOSS_PCT, 20.0)
        self.assertEqual(resilient.HEALTH_REQUIRED_STREAK, 2)
        self.assertLessEqual(resilient.HEALTH_WINDOW_DEADLINE, 3)
        self.assertLessEqual(resilient.HEALTH_PING_INTERVAL, 0.25)


if __name__ == '__main__':
    unittest.main()
