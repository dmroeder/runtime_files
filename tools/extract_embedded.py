#!/usr/bin/env python3
"""
tools/extract_embedded.py

Extracts changed regions and searches for GGFX sub-blocks and UTF-16LE strings in globals_runtime.gfx.

Usage:
  python3 tools/extract_embedded.py

Outputs under tools/out/
"""

from pathlib import Path
import json
import sys
import binascii

ROOT = Path('.').resolve()
TOOLS = ROOT / 'tools'
OUT = TOOLS / 'out'
OUT.mkdir(parents=True, exist_ok=True)

AR = TOOLS / 'analysis_results.json'
if not AR.exists():
    print(f"Missing {AR}. Run tools/analyze_gfx.py first.")
    sys.exit(1)

analysis = json.loads(AR.read_text())

def read(p):
    return Path(p).read_bytes()

# files
locals_gfx = read('globals.gfx')
runtime_gfx = read('globals_runtime.gfx')

pair = analysis['pairs'].get('globals.gfx -> globals_runtime.gfx')
if not pair:
    print('No globals pair in analysis_results.json')
    sys.exit(1)

pref = pair['prefix_match']
suff = pair['suffix_match']
print(f'globals prefix={pref} suffix={suff}')

local_changed_start = pref
local_changed_end = len(locals_gfx) - suff
runtime_changed_start = pref
runtime_changed_end = len(runtime_gfx) - suff

local_changed = locals_gfx[local_changed_start:local_changed_end]
runtime_changed = runtime_gfx[runtime_changed_start:runtime_changed_end]

(OUT / 'local_changed.bin').write_bytes(local_changed)
(OUT / 'runtime_changed.bin').write_bytes(runtime_changed)

# hexdump samples
from textwrap import wrap

def hexdump(b, width=16, length=256):
    s = binascii.hexlify(b[:length]).decode('ascii')
    parts = wrap(s, width*2)
    return '\n'.join([' '.join(wrap(p,2)) for p in parts])

(OUT / 'local_changed.hexdump.txt').write_text(hexdump(local_changed,16,1024))
(OUT / 'runtime_changed.hexdump.txt').write_text(hexdump(runtime_changed,16,1024))
print('Wrote changed region binaries and hexdumps to tools/out/')

# load ggfx files
gg1 = read('g_1.ggfx')
gg2 = read('g_2.ggfx')

# helper to find smaller matches (head/tail windows)
def find_all(hay, needle):
    i = 0
    res = []
    while True:
        i = hay.find(needle, i)
        if i == -1:
            break
        res.append(i)
        i += 1
    return res

# search for full ggfx (unlikely), and for small window matches
for name, gg in [('g_1.ggfx', gg1), ('g_2.ggfx', gg2)]:
    print('\nSearching for', name)
    full = find_all(runtime_gfx, gg)
    print(' full matches:', full)
    (OUT / f'{name}.full_matches.txt').write_text('\n'.join(map(str,full)) or 'NONE')
    # search for tails/heads of various sizes
    for w in (256,128,64,32):
        head = gg[:w]
        tail = gg[-w:]
        hpos = find_all(runtime_gfx, head)
        tpos = find_all(runtime_gfx, tail)
        (OUT / f'{name}.head_{w}.txt').write_text('\n'.join(map(str,hpos)) or 'NONE')
        (OUT / f'{name}.tail_{w}.txt').write_text('\n'.join(map(str,tpos)) or 'NONE')
        print(f' head_{w} matches: {len(hpos)}, tail_{w} matches: {len(tpos)}')

# extract UTF-16LE strings from ggfx and search in runtime
import re

def utf16le_strings(b, min_len=4):
    # find sequences of printable UTF-16LE (little endian) chars
    pairs = b
    res = []
    try:
        s = pairs.decode('utf-16le', errors='ignore')
    except Exception:
        return []
    # split on non-printable
    chunks = re.findall(r'[\w \-\.,:;!\(\)\[\]\/\\]{%d,}' % min_len, s)
    return chunks

for name, gg in [('g_1.ggfx', gg1), ('g_2.ggfx', gg2)]:
    strs = utf16le_strings(gg, min_len=4)
    outf = OUT / f'{name}.utf16le.txt'
    outf.write_text('\n'.join(strs))
    print(f'Extracted {len(strs)} UTF-16LE strings from {name}, saved to {outf}')
    # search each string (or subset) in runtime
    hits = []
    runtime_utf16 = runtime_gfx.decode('utf-16le', errors='ignore')
    for s in strs[:80]:
        if s and s in runtime_utf16:
            hits.append((s, runtime_utf16.index(s)))
    (OUT / f'{name}.utf16le_in_runtime.txt').write_text('\n'.join(f'{h[0]} @ {h[1]}' for h in hits) or 'NONE')
    print(f'Found {len(hits)} strings from {name} inside globals_runtime.gfx (first 80 scanned).')

print('\nDone. Review tools/out/')
