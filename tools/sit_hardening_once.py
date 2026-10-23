#!/usr/bin/env python3
from pathlib import Path

p = Path('panel/provision_ipv6.py')
s = p.read_text(encoding='utf-8')
old = """def _module_supported(name: str) -> bool:
    if Path('/sys/module') .joinpath(name).exists():
        return True
    rc, _, _ = run(['modprobe', '-n', name], check=False)
    return rc == 0
"""
new = """def _module_supported(name: str) -> bool:
    if (Path('/sys/module') / name).exists():
        return True
    for candidate in ('/usr/sbin/modprobe', '/sbin/modprobe', '/usr/bin/modprobe', '/bin/modprobe'):
        if not Path(candidate).is_file():
            continue
        rc, _, _ = run([candidate, '-n', name], check=False)
        return rc == 0
    return False
"""
if s.count(old) != 1:
    raise SystemExit(f'panel module probe anchor count={s.count(old)}')
p.write_text(s.replace(old, new, 1), encoding='utf-8')

p = Path('agent/tp27-exit-agent-plain.py')
s = p.read_text(encoding='utf-8')
old = "    BASE.run(['ip', 'link', 'del', BASE._iface(eid)], timeout=10, check=False)\n"
new = """    BASE.run(['ip', 'link', 'del', BASE._iface(eid)], timeout=10, check=False)
    sit_iface = BASE._sit_iface(eid) if hasattr(BASE, '_sit_iface') else ''
    if sit_iface:
        BASE.run(['ip', 'link', 'del', sit_iface], timeout=10, check=False)
"""
if s.count(old) != 1:
    raise SystemExit(f'plain cleanup anchor count={s.count(old)}')
p.write_text(s.replace(old, new, 1), encoding='utf-8')

p = Path('tests/test_sit6in4_fast_bootstrap_contract.py')
s = p.read_text(encoding='utf-8')
marker = 'def test_minimal_host_probe_and_plain_cleanup_are_fail_safe():'
if marker not in s:
    s = s.rstrip() + """


def test_minimal_host_probe_and_plain_cleanup_are_fail_safe():
    panel = (ROOT / 'panel/provision_ipv6.py').read_text(encoding='utf-8')
    plain = (ROOT / 'agent/tp27-exit-agent-plain.py').read_text(encoding='utf-8')
    assert "for candidate in ('/usr/sbin/modprobe', '/sbin/modprobe', '/usr/bin/modprobe', '/bin/modprobe')" in panel
    assert 'return False' in panel[panel.index('def _module_supported'):panel.index('def local_public_ipv6')]
    assert "BASE._sit_iface(eid) if hasattr(BASE, '_sit_iface') else ''" in plain
    assert "BASE.run(['ip', 'link', 'del', sit_iface]" in plain
""" + '\n'
    p.write_text(s, encoding='utf-8')

print('SIT_HARDENING=OK')
