#!/usr/bin/env python3
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'panel'))

import provision_resilient as resilient


class KernelTimingV3Tests(unittest.TestCase):
    def test_kernel_timing_contract_names_high_cost_substages(self):
        text = (ROOT / 'panel/provision_resilient.py').read_text(encoding='utf-8')
        self.assertIn('KERNEL_TIMING_VERSION = 3', text)
        for stage in (
            'agent_ready',
            'context_prepare',
            'pre_cleanup',
            'raw_gre.local_start',
            'raw_gre.remote_provision',
            'raw_gre.health_gate',
            'raw_gre.candidate_total',
            'commit_config',
        ):
            self.assertIn(stage, text)

    def test_build_gre_records_substages_without_relaxing_health(self):
        timings = []
        remote = {'service_active': True}

        with patch.object(resilient, '_write_local_gre', return_value={}), \
             patch.object(resilient, '_start_local', return_value=None), \
             patch.object(resilient.asyncio, 'sleep', new=AsyncMock(return_value=None)), \
             patch.object(resilient, '_provision_remote_gre', new=AsyncMock(return_value=remote)), \
             patch.object(resilient, '_health_peer', new=AsyncMock(return_value=(True, 'ok'))):
            healthy, details, result = asyncio.run(
                resilient._build_gre(
                    'agent', 'test', 't27test', '192.0.2.1', '198.51.100.2',
                    '10.253.0.2/30', '10.253.0.1/30', '10.253.0.2', '10.253.0.1',
                    1234, 1400, 'gre', 0,
                    timings=timings, timing_prefix='raw_gre',
                )
            )

        self.assertTrue(healthy)
        self.assertEqual(details, 'ok')
        self.assertEqual(result, remote)
        self.assertEqual(
            [row['stage'] for row in timings],
            ['raw_gre.local_start', 'raw_gre.remote_provision', 'raw_gre.health_gate'],
        )
        self.assertEqual(resilient.HEALTH_PING_COUNT, 5)
        self.assertEqual(resilient.HEALTH_REQUIRED_STREAK, 2)
        self.assertEqual(resilient.HEALTH_MAX_LOSS_PCT, 20.0)


if __name__ == '__main__':
    unittest.main()
