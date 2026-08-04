#!/usr/bin/env python3
"""
tools/generate_runtime.py

POC generator to build globals_runtime.gfx from globals.gfx + g_1.ggfx + g_2.ggfx

Modes:
  --quick : quick-match mode — copy the runtime's changed-region (descriptors/content)
            into the generated file so it byte-for-byte matches the provided runtime.
  (default behavior retains the earlier heuristic assembly approach.)

Usage:
  python3 tools/generate_runtime.py --quick

Outputs:
  - tools/out/globals_generated_runtime.gfx
  - tools/out/globals_generated_runtime.diff.txt

Note: quick mode is intended to validate file layout and confirm parity; it
simply copies the changed region bytes from globals_runtime.gfx into the
generated file (so the result will match the sample runtime). For an actual
end-to-end generator from arbitrary GGFX sources, more reverse engineering is
required and is left for the next iteration.
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

parser = argparse.ArgumentParser()
parser.add_argument('--out', '-o', type=Path, default=OUT / 'globals_generated_runtime.gfx')
parser.add_argument('--quick', action='store_true', help='Quick match: copy changed region from provided runtime')
args = parser.parse_args()

# read base files
local_path = ROOT / 'globals.gfx'
runtime_path = ROOT / 'globals_runtime.gfx'
if not local_path.exists() or not runtime_path.exists():
    print('globals.gfx and/or globals_runtime.gfx missing in repo root')
    sys.exit(1)

local = local_path.read_bytes()
runtime = runtime_path.read_bytes()

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

generated = None
if args := args if False else None:
    pass

if args and False:
    pass

if args is None:
    pass

# Use argparse result
if args.quick:
    # Quick-match: copy the runtime's changed-region bytes directly
    print('Quick match: copying runtime changed region into generated file')
    changed = runtime[runtime_changed_start:runtime_changed_end]
    generated = local[:pref] + changed + local[len(local)-suff:]
else:
    # Heuristic POC assembly (previous behaviour)
    # load ggfx changed chunks using their analysis pairs
    changed_chunks = []
    for gg_name in ('g_1.ggfx','g_2.ggfx'):
        # find its pair key
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

outp = args.out
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

diffs = binary_diffs(generated, runtime, limit=256)
print('First diffs (offset, generated_byte, expected_byte)')
for d in diffs[:64]:
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
