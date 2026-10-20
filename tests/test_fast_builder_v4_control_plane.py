#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'panel'))

import provision_resilient as resilient


class FastBuilderV4ControlPlaneTests(unittest.TestCase):
    def test_local_start_is_single_enable_now_without_reload_or_restart(self):
        with patch.object(resilient, 'run', return_value=(0, '', '')) as run_mock:
            resilient._start_local('v4test', 'gre')

        run_mock.assert_called_once_with(
            ['systemctl', 'enable', '--now', 'tp27-link@v4test.service'],
            check=False,
        )
        flat = repr(run_mock.call_args_list)
        self.assertNotIn('daemon-reload', flat)
        self.assertNotIn("'restart'", flat)

    def test_local_cleanup_batches_mutually_exclusive_units(self):
        with patch.object(resilient, 'run', return_value=(0, '', '')) as run_mock:
            resilient._cleanup_local('v4test')

        self.assertIn(
            call([
                'systemctl', 'disable', '--now',
                'tp27-link@v4test.service',
                'tp27-framed@v4test.service',
            ], check=False),
            run_mock.call_args_list,
        )
        self.assertIn(
            call([
                'systemctl', 'reset-failed',
                'tp27-link@v4test.service',
                'tp27-framed@v4test.service',
            ], check=False),
            run_mock.call_args_list,
        )

    def test_initial_precleanup_defers_remote_delete_but_failure_cleanup_keeps_it(self):
        text = (ROOT / 'panel/provision_resilient.py').read_text(encoding='utf-8')
        start = text.index("profile = 'DEEP' if DEEP_SCAN else 'FAST'")
        first_candidate = text.index("job_set(job_id, message=f'مرحله 1:", start)
        precleanup = text[start:first_candidate]

        self.assertIn("remote='deferred-to-provision'", precleanup)
        self.assertNotIn('await _cleanup_remote(agent_id, eid)', precleanup)

        failure = text[text.index('except Exception as exc:', first_candidate):]
        self.assertIn('await _cleanup_remote(agent_id, eid)', failure)

    def test_health_gate_contract_is_unchanged(self):
        self.assertEqual(resilient.HEALTH_PING_COUNT, 5)
        self.assertEqual(resilient.HEALTH_MAX_LOSS_PCT, 20.0)
        self.assertEqual(resilient.HEALTH_REQUIRED_STREAK, 2)
        self.assertEqual(resilient.HEALTH_REJECT_STREAK, 3)
        self.assertEqual(resilient.HEALTH_HARD_FAIL_STREAK, 2)
        self.assertEqual(resilient.HEALTH_PING_INTERVAL, 0.25)
        self.assertEqual(resilient.HEALTH_WINDOW_DEADLINE, 3)
        self.assertEqual(resilient.HEALTH_RETRY_DELAY, 0.35)


if __name__ == '__main__':
    unittest.main()
