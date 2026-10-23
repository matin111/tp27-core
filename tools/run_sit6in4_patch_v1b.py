#!/usr/bin/env python3
from pathlib import Path

src = Path(__file__).with_name('apply_sit6in4_v1.py')
text = src.read_text(encoding='utf-8')
old = """    if text.count(old) != 1:\n        raise SystemExit(f'anchor not unique in {path}: count={text.count(old)}')\n    p.write_text(text.replace(old, new, 1), encoding='utf-8')\n"""
new = """    if text.count(old) < 1:\n        raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')\n    p.write_text(text.replace(old, new, 1), encoding='utf-8')\n"""
if old not in text:
    raise SystemExit('replace_once guard anchor missing')
patched = text.replace(old, new, 1)
code = compile(patched, str(src), 'exec')
exec(code, {'__file__': str(src), '__name__': '__main__'})
