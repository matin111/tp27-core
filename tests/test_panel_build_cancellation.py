#!/usr/bin/env python3
import ast
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / 'panel' / 'app.py'
SOURCE = APP.read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)


def _async_function(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f'async function {name} not found')


def test_delete_cancels_build_before_cleanup():
    fn = _async_function('delete_exit')
    text = ast.get_source_segment(SOURCE, fn)
    assert 'await _cancel_builds_for_exit(exit_id)' in text
    assert text.index('await _cancel_builds_for_exit(exit_id)') < text.index('cleanup_local_exit(exit_id)')


def test_new_jobs_store_exit_and_agent_identity():
    fn = _async_function('add_exit')
    text = ast.get_source_segment(SOURCE, fn)
    assert "'exit_id': body.id" in text
    assert "'agent_id': body.agent_id" in text
    assert '_track_build_task(job_id, task)' in text


def test_cancel_helper_cancels_task_and_finishes_job():
    fn = _async_function('_cancel_builds_for_exit')
    text = ast.get_source_segment(SOURCE, fn)
    assert 'task.cancel()' in text
    assert 'await task' in text
    assert "state='failed'" in text
    assert 'لغو شد' in text
