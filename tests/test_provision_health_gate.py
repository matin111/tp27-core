#!/usr/bin/env python3
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'panel'))

import provision_resilient as target


class StrictHealthGateTests(unittest.TestCase):
    @staticmethod
    def _ping_result(loss_pct):
        received = max(0, min(5, round(5 * (100.0 - loss_pct) / 100.0)))
        rc = 0 if received else 1
        out = (
            'PING 10.253.0.9 (10.253.0.9) 56(84) bytes of data.\n\n'
            '--- 10.253.0.9 ping statistics ---\n'
            f'5 packets transmitted, {received} received, {loss_pct}% packet loss, time 4000ms\n'
        )
        return rc, out, ''

    def _run_health(self, losses, tries=None):
        results = [self._ping_result(loss) for loss in losses]

        def fake_run(_cmd, check=False):
            self.assertFalse(check)
            if not results:
                self.fail('health gate performed more ping windows than expected')
            return results.pop(0)

        if tries is None:
            tries = len(losses)

        with patch.object(target, 'run', side_effect=fake_run), \
             patch.object(target.asyncio, 'sleep', new=AsyncMock(return_value=None)):
            return asyncio.run(target._health_peer('t27test', '10.253.0.9', tries=tries))

    def test_rejects_repeated_80_percent_loss_even_when_ping_rc_is_zero(self):
        healthy, details = self._run_health([80, 80, 80, 80])
        self.assertFalse(healthy)
        self.assertIn('loss=80.0%', details)

    def test_requires_two_consecutive_acceptable_windows(self):
        healthy, _ = self._run_health([0, 100, 0], tries=3)
        self.assertFalse(healthy)

    def test_accepts_two_consecutive_windows_at_or_below_loss_limit(self):
        healthy, details = self._run_health([20, 20], tries=2)
        self.assertTrue(healthy)
        self.assertEqual(details.count('HEALTH_WINDOW='), 2)

    def test_rejects_unknown_ping_summary(self):
        results = [(0, 'unexpected ping output', '')] * 2

        def fake_run(_cmd, check=False):
            return results.pop(0)

        with patch.object(target, 'run', side_effect=fake_run), \
             patch.object(target.asyncio, 'sleep', new=AsyncMock(return_value=None)):
            healthy, details = asyncio.run(
                target._health_peer('t27test', '10.253.0.9', tries=2)
            )
        self.assertFalse(healthy)
        self.assertIn('loss=unknown%', details)


if __name__ == '__main__':
    unittest.main()
