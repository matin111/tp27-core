#!/usr/bin/env python3
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'panel'))

import provision_resilient as resilient


class AgentUpgradeTimeoutTests(unittest.TestCase):
    def test_command_timeout_waits_for_upgraded_agent_heartbeat(self):
        old = {
            'agent_id': 'exit-test',
            'online': True,
            'version': '1.5.1',
            'public_ip': '203.0.113.10',
        }
        upgraded = {
            'agent_id': 'exit-test',
            'online': True,
            'version': '1.6.0',
            'public_ip': '203.0.113.10',
        }
        seen = [old, upgraded]
        messages = []

        def fake_get_agent(_agent_id):
            if seen:
                return seen.pop(0)
            return upgraded

        def fake_job_set(_job_id, **kwargs):
            if kwargs.get('message'):
                messages.append(kwargs['message'])

        with patch.object(resilient, 'get_agent', side_effect=fake_get_agent), \
             patch.object(resilient, 'queue_command', return_value={'id': 'upgrade-1'}), \
             patch.object(resilient, 'wait_command', new=AsyncMock(side_effect=TimeoutError('late result'))), \
             patch.object(resilient.asyncio, 'sleep', new=AsyncMock(return_value=None)), \
             patch.object(resilient, 'job_set', side_effect=fake_job_set):
            result = asyncio.run(resilient._ensure_agent('exit-test', 'job-1'))

        self.assertEqual(result['version'], '1.6.0')
        self.assertTrue(any('هنوز در حال اجراست' in text for text in messages))
        self.assertTrue(any('Agent v1.6.0 آنلاین شد' in text for text in messages))

    def test_explicit_upgrade_failure_is_not_ignored(self):
        old = {
            'agent_id': 'exit-test',
            'online': True,
            'version': '1.5.1',
            'public_ip': '203.0.113.10',
        }
        failed = {
            'id': 'upgrade-1',
            'state': 'failed',
            'ok': False,
            'error': 'download failed',
        }

        with patch.object(resilient, 'get_agent', return_value=old), \
             patch.object(resilient, 'queue_command', return_value={'id': 'upgrade-1'}), \
             patch.object(resilient, 'wait_command', new=AsyncMock(return_value=failed)), \
             patch.object(resilient, 'job_set'):
            with self.assertRaisesRegex(RuntimeError, 'download failed'):
                asyncio.run(resilient._ensure_agent('exit-test', 'job-1'))

    def test_upgrade_timeouts_are_explicitly_bounded(self):
        self.assertEqual(resilient.AGENT_UPGRADE_COMMAND_TIMEOUT, 90)
        self.assertEqual(resilient.AGENT_UPGRADE_RECONNECT_TIMEOUT, 90)
        self.assertEqual(resilient.AGENT_RECONNECT_POLL, 1.0)


if __name__ == '__main__':
    unittest.main()
