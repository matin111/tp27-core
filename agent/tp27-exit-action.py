#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
from pathlib import Path

AGENT = Path('/opt/tp27-exit-agent/tp27-exit-agent.py')


def load_agent():
    if not AGENT.exists():
        raise RuntimeError('TP27 exit agent is not installed')
    spec = importlib.util.spec_from_file_location('tp27_exit_agent_runtime', AGENT)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load TP27 exit agent')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    p = argparse.ArgumentParser(prog='tp27-exit-action')
    p.add_argument('--action', required=True)
    args = p.parse_args()
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise RuntimeError('action payload must be an object')

    module = load_agent()
    handler = getattr(module, 'ACTIONS', {}).get(args.action)
    if handler is None:
        raise RuntimeError('unsupported action')
    result = handler(payload)
    print(json.dumps({'ok': True, 'result': result or {}}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)[:10000]}, ensure_ascii=False))
        raise SystemExit(1)
