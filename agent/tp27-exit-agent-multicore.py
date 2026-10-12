#!/usr/bin/env python3
import importlib.util
from pathlib import Path

BASE_AGENT = Path('/opt/tp27-exit-agent/tp27-exit-agent.py')
ADDON = Path('/opt/tp27-exit-agent/tp27_gost_addon.py')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load ' + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load(BASE_AGENT, 'tp27_exit_agent_base')
GOST = load(ADDON, 'tp27_gost_addon')
GOST.install(BASE)

if __name__ == '__main__':
    BASE.main()
