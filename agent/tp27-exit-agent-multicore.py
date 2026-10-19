#!/usr/bin/env python3
import importlib.util
from pathlib import Path

BASE_AGENT = Path('/opt/tp27-exit-agent/tp27-exit-agent.py')
GOST_ADDON = Path('/opt/tp27-exit-agent/tp27_gost_addon.py')
OPENVPN_ADDON = Path('/opt/tp27-exit-agent/tp27_openvpn_addon.py')
BENCHMARK_ADDON = Path('/opt/tp27-exit-agent/tp27_benchmark_addon.py')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load ' + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load(BASE_AGENT, 'tp27_exit_agent_base')
if GOST_ADDON.exists():
    GOST = load(GOST_ADDON, 'tp27_gost_addon')
    GOST.install(BASE)
if OPENVPN_ADDON.exists():
    OPENVPN = load(OPENVPN_ADDON, 'tp27_openvpn_addon')
    OPENVPN.install(BASE)
if BENCHMARK_ADDON.exists():
    BENCHMARK = load(BENCHMARK_ADDON, 'tp27_benchmark_addon')
    BENCHMARK.install(BASE)

if __name__ == '__main__':
    BASE.main()
