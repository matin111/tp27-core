#!/usr/bin/env python3
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'panel'))

import provision_multicore as multicore


class FastBuilderV2Tests(unittest.TestCase):
    def test_read_only_tcp_preflight_prefers_refused_and_skips_open(self):
        probes = {
            443: {'status': 'timeout'},
            2083: {'status': 'refused'},
            80: {'status': 'open'},
            8443: {'status': 'unknown'},
        }
        ordered = multicore._prioritize_tcp_ports((443, 2083, 80, 8443), probes)
        self.assertEqual(ordered, (2083, 443, 8443))

    def test_preflight_fail_open_keeps_original_order(self):
        candidates = (443, 2083, 80)
        self.assertEqual(multicore._prioritize_tcp_ports(candidates, {}), candidates)

    def test_tcp_probe_contract_is_read_only(self):
        text = (ROOT / 'panel' / 'provision_multicore.py').read_text(encoding='utf-8')
        body = text.split('async def _tcp_probe', 1)[1].split('async def _preflight_tcp_candidates', 1)[0]
        self.assertIn('asyncio.open_connection', body)
        self.assertNotIn('systemctl', body)
        self.assertNotIn('atomic_json', body)
        self.assertNotIn('queue_command', body)

    def test_tcp_probe_is_parallel_and_short_bounded(self):
        text = (ROOT / 'panel' / 'provision_multicore.py').read_text(encoding='utf-8')
        self.assertIn('asyncio.gather', text)
        self.assertLessEqual(multicore.TCP_PREFLIGHT_TIMEOUT, 1.0)

    def test_local_settle_reduction_keeps_health_gate(self):
        self.assertLessEqual(multicore.OPENVPN_LOCAL_SETTLE, 0.25)
        self.assertLessEqual(multicore.GOST_LOCAL_SETTLE, 0.35)
        text = (ROOT / 'panel' / 'provision_multicore.py').read_text(encoding='utf-8')
        self.assertIn('legacy._health_peer(iface, remote_addr, tries=8)', text)
        self.assertIn('legacy._health_peer(iface, remote_addr, tries=7)', text)

    def test_timing_v2_is_attached_to_job_result(self):
        timings = [{'stage': 'x', 'ms': 7}]
        fake_jobs = {'job1': {'id': 'job1', 'state': 'running', 'messages': [], 'result': {'ok': True}}}

        def fake_job_set(job_id, state=None, message=None, error=None, result=None):
            job = fake_jobs.setdefault(job_id, {'messages': []})
            if state:
                job['state'] = state
            if message:
                job.setdefault('messages', []).append({'text': message})
            if result:
                job['result'] = result
            return job

        with patch.object(multicore, 'JOBS', fake_jobs), \
             patch.object(multicore, 'job_set', side_effect=fake_job_set), \
             patch.object(multicore.time, 'monotonic', side_effect=[10.0, 10.125]):
            payload = multicore._finish_timing('job1', timings, 10.0)

        self.assertEqual(payload['version'], 2)
        self.assertEqual(payload['total_ms'], 125)
        self.assertEqual(fake_jobs['job1']['result']['timing_v2']['stages'], timings)


if __name__ == '__main__':
    unittest.main()
