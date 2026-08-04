#!/usr/bin/env python3
"""
tools/generate_runtime.py

First-pass POC generator to build globals_runtime.gfx from globals.gfx + g_1.ggfx + g_2.ggfx

This is intentionally heuristic: it uses analysis_results.json to locate changed regions
and copies the "changed" subregions from the g_1_runtime.ggfx / g_2_runtime.ggfx files
into the globals.gfx changed area, concatenating/truncating as needed to match length.

Usage:
  python3 tools/generate_runtime.py --out tools/out/globals_generated_runtime.gfx

Outputs:
  - tools/out/globals_generated_runtime.gfx
  - prints sha256 of generated file and of provided globals_runtime.gfx (if available)
  - prints a small binary diff (first 32 differing offsets) so you can iterate.

This is a POC to run locally and refine. It will not perfectly reproduce the runtime file yet,
but it should demonstrate how GGFX pieces can be inlined.
"""

import json
from pathlib import Path
import hashlib
import sys
import argparse

ROOT = Path('.').resolve()
TOOLS = ROOT / 'tools'
OUT = TOOLS / 'out'
OUT.mkdir(parents=True, exist_ok=True)

AR = TOOLS / 'analysis_results.json'
if not AR.exists():
    print('Missing tools/analysis_results.json — run tools/analyze_gfx.py first or ensure it exists in the repo.')
    sys.exit(1)

analysis = json.loads(AR.read_text())

# helper
def sha256(p: Path):
    import hashlib
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()

# read base files
local = (ROOT / 'globals.gfx').read_bytes()
runtime = (ROOT / 'globals_runtime.gfx').read_bytes()

# get prefix/suffix
pair = analysis['pairs'].get('globals.gfx -> globals_runtime.gfx')
if pair is None:
    print('No globals.gfx -> globals_runtime.gfx entry in analysis_results.json')
    sys.exit(1)

pref = pair['prefix_match']
suff = pair['suffix_match']
local_changed_start = pref
local_changed_end = len(local) - suff
runtime_changed_start = pref
runtime_changed_end = len(runtime) - suff

target_len = runtime_changed_end - runtime_changed_start
print(f'globals: prefix={pref} suffix={suff} local_changed_len={local_changed_end-local_changed_start} target_runtime_changed_len={target_len}')

# load ggfx runtime changed regions using their analysis pairs
def get_changed_region(name):
    key = f'{name} -> {name.replace('.ggfx','')}_runtime.ggfx'
    # The analysis file uses keys like 'g_1.ggfx -> g_1_runtime.ggfx'
    key = f'{name} -> {name.replace('.ggfx','')}_runtime.ggfx'

# Instead parse analysis keys
changed_chunks = []
for gg_name in ('g_1.ggfx','g_2.ggfx'):
    # find its pair key
    pair_key = f'{gg_name} -> {gg_name.replace('.ggfx','')}_runtime.ggfx'
    if pair_key not in analysis['pairs']:
        # fallback search
        pair_key = None
        for k in analysis['pairs'].keys():
            if k.startswith(gg_name + ' -> '):
                pair_key = k
                break
    if pair_key is None:
        print('No pair entry for', gg_name)
        continue
    info = analysis['pairs'][pair_key]
    gbytes = (ROOT / gg_name).read_bytes()
    # compute changed region using prefix and suffix
    gpref = info['prefix_match']
    gsuff = info['suffix_match']
    g_changed_start = gpref
    g_changed_end = len(gbytes) - gsuff
    chunk = gbytes[g_changed_start:g_changed_end]
    print(f'{gg_name}: gpref={gpref} gsuff={gsuff} changed_len={len(chunk)}')
    changed_chunks.append((gg_name, chunk))

if not changed_chunks:
    print('No ggfx changed chunks found; aborting')
    sys.exit(1)

# Build concatenation of chunks to reach target_len
assembled = b''
ci = 0
while len(assembled) < target_len:
    name, chunk = changed_chunks[ci % len(changed_chunks)]
    assembled += chunk
    ci += 1
# trim to exact
assembled = assembled[:target_len]
print(f'Assembled {len(assembled)} bytes from {len(changed_chunks)} ggfx chunks (used {ci} chunks)')

# build generated runtime file
generated = local[:pref] + assembled + local[len(local)-suff:]
outp = OUT / 'globals_generated_runtime.gfx'
outp.write_bytes(generated)
print('Wrote', outp)

# printsums and first diff
print('sha256(generated)=', hashlib.sha256(generated).hexdigest())
print('sha256(runtime)=  ', hashlib.sha256(runtime).hexdigest())

# binary diff (first 64 diffs)

def binary_diffs(a: bytes, b: bytes, limit=64):
    diffs = []
    la = len(a)
    lb = len(b)
    l = min(la, lb)
    for i in range(l):
        if a[i] != b[i]:
            diffs.append((i, a[i], b[i]))
            if len(diffs) >= limit:
                break
    if la != lb and len(diffs) < limit:
        for i in range(l, max(la, lb)):
            ai = a[i] if i < la else None
            bi = b[i] if i < lb else None
            diffs.append((i, ai, bi))
            if len(diffs) >= limit:
                break
    return diffs

diffs = binary_diffs(generated, runtime, limit=64)
print('First diffs (offset, generated_byte, expected_byte)')
for d in diffs[:32]:
    off, a_, b_ = d
    a_hex = '??' if a_ is None else f'{a_:02x}'
    b_hex = '??' if b_ is None else f'{b_:02x}'
    print(f'  0x{off:08x}: {a_hex} != {b_hex}')

# save a small diff file
with open(OUT / 'globals_generated_runtime.diff.txt', 'w') as f:
    f.write('sha256(generated)=' + hashlib.sha256(generated).hexdigest() + '\n')
    f.write('sha256(runtime)=' + hashlib.sha256(runtime).hexdigest() + '\n')
    f.write('\nFirst diffs:\n')
    for d in diffs[:256]:
        off, a_, b_ = d
        a_hex = '??' if a_ is None else f'{a_:02x}'
        b_hex = '??' if b_ is None else f'{b_:02x}'
        f.write(f'0x{off:08x}: {a_hex} != {b_hex}\n')

print('Wrote diff summary to tools/out/globals_generated_runtime.diff.txt')
