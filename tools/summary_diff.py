#!/usr/bin/env python3
"""
tools/summary_diff.py

Compute SHA256 of expected runtime and generated runtime and print a compact summary
with the first N differing offsets.

Usage:
  python3 tools/summary_diff.py \
      --expected globals_runtime.gfx \
      --generated tools/out/globals_generated_runtime.gfx \
      --out tools/out/summary.txt

Defaults assume the files from the repository and the generator output location.
The script prints the summary to stdout and writes tools/out/summary.txt.
"""

from pathlib import Path
import hashlib
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--expected', '-e', type=Path, default=Path('globals_runtime.gfx'))
parser.add_argument('--generated', '-g', type=Path, default=Path('tools/out/globals_generated_runtime.gfx'))
parser.add_argument('--out', '-o', type=Path, default=Path('tools/out/summary.txt'))
parser.add_argument('--limit', '-n', type=int, default=16)
args = parser.parse_args()

E = args.expected
G = args.generated
OUT = args.out
LIMIT = args.limit

if not E.exists():
    print(f'Expected runtime file not found: {E}')
    raise SystemExit(1)
if not G.exists():
    print(f'Generated runtime file not found: {G}')
    raise SystemExit(1)

def sha256(path: Path):
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

sha_e = sha256(E)
sha_g = sha256(G)

b_e = E.read_bytes()
b_g = G.read_bytes()

# collect diffs
diffs = []
minlen = min(len(b_e), len(b_g))
for i in range(minlen):
    if b_e[i] != b_g[i]:
        diffs.append((i, b_g[i], b_e[i]))
        if len(diffs) >= LIMIT:
            break
# if lengths differ and we have space
if len(diffs) < LIMIT and len(b_e) != len(b_g):
    larger = max(len(b_e), len(b_g))
    for i in range(minlen, larger):
        a = b_g[i] if i < len(b_g) else None
        b = b_e[i] if i < len(b_e) else None
        diffs.append((i, a, b))
        if len(diffs) >= LIMIT:
            break

lines = []
lines.append(f'expected={E} ({len(b_e)} bytes)')
lines.append(f'generated={G} ({len(b_g)} bytes)')
lines.append(f'sha256(expected)={sha_e}')
lines.append(f'sha256(generated)={sha_g}')
lines.append('')
lines.append(f'first {len(diffs)} diffs (offset, generated_byte, expected_byte)')
for off, a, b in diffs:
    a_hex = '??' if a is None else f'{a:02x}'
    b_hex = '??' if b is None else f'{b:02x}'
    lines.append(f'0x{off:08x}: {a_hex} != {b_hex}')

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text('\n'.join(lines) + '\n')
print('\n'.join(lines))
print('\nWrote summary to', OUT)
