#!/usr/bin/env python3
"""
tools/full_generate.py

Heuristic full generator that repacks GGFX strings into the runtime layout.

This revision implements a copy-and-patch strategy for the runtime's
index->offset mapping block:
 - Locate the runtime content area by searching for the UTF-16LE "Contents" string.
 - Extract unique UTF-16LE strings from g_1.ggfx and g_2.ggfx and build a
   content blob placed at the same content offset the sample runtime uses.
 - Heuristically locate the runtime's index->offset mapping block by scanning
   a region of the sample file for many u32 words that look like pointers into
   the content area.
 - Copy the sample mapping block bytes into the generated image and then
   patch any u32 values that point at strings in the sample content area so
   they point at the corresponding offsets in the generated content.
 - Preserve the sample's small index table (we write sequential indices there)
   so descriptors -> small index -> mapping block -> content offsets will
   resolve to our generated strings.

This is iterative and conservative: by copying the sample mapping block
layout and only updating offsets we avoid incorrectly reconstructing
metadata fields whose meaning we haven't fully reversed.

Usage:
  python3 tools/full_generate.py --out tools/out/globals_full_generated.gfx

After running: compare with sample using tools/summary_diff.py and paste the
results so I can refine further.
"""

from pathlib import Path
import re
import json
import argparse

ROOT = Path('.').resolve()
TOOLS = ROOT / 'tools'
OUT = TOOLS / 'out'
OUT.mkdir(parents=True, exist_ok=True)

ANALYSIS = TOOLS / 'analysis_results.json'
if not ANALYSIS.exists():
    print('Missing tools/analysis_results.json — run tools/analyze_gfx.py first')
    raise SystemExit(1)

analysis = json.loads(ANALYSIS.read_text())

# helper: extract utf-16le printable strings
def utf16le_strings(b, min_len=3):
    try:
        s = b.decode('utf-16le', errors='ignore')
    except Exception:
        return []
    chunks = re.findall(r'[\w \-\.,:;!\(\)\[\]\/\\\u00A0-\uFFFF]{%d,}' % min_len, s)
    return chunks

parser = argparse.ArgumentParser()
parser.add_argument('--out', '-o', type=Path, default=OUT / 'globals_full_generated.gfx')
parser.add_argument('--strings-min', type=int, default=3)
args = parser.parse_args()

# read files
local_path = ROOT / 'globals.gfx'
sample_path = ROOT / 'globals_runtime.gfx'
if not local_path.exists() or not sample_path.exists():
    print('globals.gfx and/or globals_runtime.gfx missing in repo root')
    raise SystemExit(1)

local = local_path.read_bytes()
sample = sample_path.read_bytes()

# determine changed-region bounds from analysis
pair = analysis['pairs'].get('globals.gfx -> globals_runtime.gfx')
if not pair:
    print('Missing globals pair in analysis_results.json')
    raise SystemExit(1)

PREF = pair['prefix_match']
SUFF = pair['suffix_match']
changed_start = PREF
changed_end = len(sample) - SUFF
print(f'Changed region: 0x{changed_start:08x}-0x{changed_end:08x}')

# find content area by locating "Contents" utf-16le
contents_pat = 'Contents'.encode('utf-16le')
contents_off = sample.find(contents_pat)
if contents_off == -1:
    print('Could not find "Contents" in sample runtime')
    raise SystemExit(1)
print('Found "Contents" at', contents_off, hex(contents_off))

# extract strings from ggfx files
gg_files = [ROOT / 'g_1.ggfx', ROOT / 'g_2.ggfx']
all_s = []
for p in gg_files:
    if not p.exists():
        continue
    all_s.extend(utf16le_strings(p.read_bytes(), min_len=args.strings_min))
# unique preserve order
seen = set(); uniq = []
for s in all_s:
    if s not in seen:
        seen.add(s); uniq.append(s)
print('Extracted', len(uniq), 'unique strings from ggfx files')

# build content blob placing our strings consecutively starting at contents_off
content_blob = b''
string_offsets = {}
for s in uniq:
    string_offsets[s] = contents_off + len(content_blob)
    content_blob += s.encode('utf-16le') + b'\x00\x00'
print('Built content blob len', len(content_blob))

# Heuristic: locate mapping block (index->offset table) by scanning a candidate range
# Use a heuristic scan over 0x0400 .. 0x2000 to find the window with many u32s inside content range
sbytes = sample
file_len = len(sbytes)
search_lo = 0x0400
search_hi = min(0x2000, file_len - 4)
window_u32s = 256  # window size in u32s
best = None
best_score = 0
for start in range(search_lo, search_hi - window_u32s*4, 4):
    cnt = 0
    for i in range(window_u32s):
        pos = start + i*4
        v = int.from_bytes(sbytes[pos:pos+4], 'little')
        # count values that look like offsets into content area
        if contents_off <= v < file_len:
            cnt += 1
    if cnt > best_score:
        best_score = cnt
        best = start

print('Mapping-block scan best start:', best, 'score:', best_score)
if best is None or best_score < 4:
    # fallback to earlier-known candidate area around 0x0c80 if present
    fallback = 0x0c80
    if fallback + 4 <= file_len:
        print('Falling back to', hex(fallback))
        best = fallback
    else:
        print('Could not locate mapping block; aborting')
        raise SystemExit(1)

# Narrow the mapping block by trimming trailing 0xFFFFFFFF runs and include region with many in-range values
mapping_start = best
mapping_end = mapping_start + window_u32s*4
# trim leading/ trailing 0xFF sequences
while mapping_start < mapping_end and all(b == 0xFF for b in sbytes[mapping_start:mapping_start+4]):
    mapping_start += 4
while mapping_end > mapping_start and all(b == 0xFF for b in sbytes[mapping_end-4:mapping_end]):
    mapping_end -= 4

print('Mapping block candidate:', hex(mapping_start), hex(mapping_end), '(%d bytes)' % (mapping_end-mapping_start))

# Build generated image starting from sample (so we preserve layout/metadata) and patch
gen = bytearray(sample)

# insert content blob
if contents_off + len(content_blob) <= len(gen):
    gen[contents_off:contents_off+len(content_blob)] = content_blob
else:
    need = contents_off + len(content_blob) - len(gen)
    gen.extend(b'\x00' * need)
    gen[contents_off:contents_off+len(content_blob)] = content_blob
print('Inserted content blob into generated image')

# Read mapping entries in sample and patch offsets in gen
patched = 0
for pos in range(mapping_start, mapping_end, 4):
    v = int.from_bytes(sbytes[pos:pos+4], 'little')
    # Only attempt to patch values that point into the sample's content area
    if not (contents_off <= v < file_len):
        continue
    # decode string at v in sample
    # read until UTF-16LE terminator
    end = v
    while end + 1 < file_len:
        if sbytes[end:end+2] == b'\x00\x00':
            break
        end += 2
    try:
        s = sbytes[v:end].decode('utf-16le')
    except Exception:
        continue
    if s not in string_offsets:
        # not one of our strings; skip
        continue
    new_off = string_offsets[s]
    gen[pos:pos+4] = new_off.to_bytes(4, 'little')
    patched += 1

print('Patched', patched, 'mapping entries in block')

# Also write sequential index table entries if sample had sequential small indices
idx_table_off = None
# attempt to find small-index table (like 6,7,8...) by scanning near changed region
for start in range(PREF, PREF + 0x400, 4):
    # check if we see an increasing run of small integers
    ok = True
    first = int.from_bytes(sbytes[start:start+4], 'little')
    if not (0 < first < 0x1000):
        continue
    for i in range(1, 8):
        v = int.from_bytes(sbytes[start + i*4:start + i*4 +4], 'little')
        if v != first + i:
            ok = False; break
    if ok:
        idx_table_off = start
        break

if idx_table_off is None:
    # fallback to known 0xA14
    idx_table_off = 0xA14 if 0xA14 + 4 <= len(gen) else None

if idx_table_off:
    sample_first = int.from_bytes(sbytes[idx_table_off:idx_table_off+4],'little')
    base = sample_first
    for i, s in enumerate(uniq):
        pos = idx_table_off + i*4
        if pos +4 > len(gen):
            break
        gen[pos:pos+4] = (base + i).to_bytes(4, 'little')
    print('Wrote sequential index entries at', hex(idx_table_off), 'starting', base)
else:
    print('Could not find index-table to write sequential indices')

# Overwrite prefix/suffix from local so header comes from local file
gen[:PREF] = local[:PREF]
if SUFF > 0:
    gen[len(gen)-SUFF:] = local[len(local)-SUFF:]

outp = args.out
outp.write_bytes(bytes(gen))
print('Wrote generated runtime to', outp)

# write diagnostics
sample_mapping = sbytes[mapping_start:mapping_end]
(Path('tools/out/full_generate_diag.json')).write_text(json.dumps({
    'contents_off': contents_off,
    'mapping_start': mapping_start,
    'mapping_end': mapping_end,
    'mapping_len': mapping_end-mapping_start,
    'patched_entries': patched,
    'num_strings': len(uniq),
    'idx_table_off': idx_table_off
}, indent=2))
print('Wrote diagnostics to tools/out/full_generate_diag.json')
print('\nDone. Run tools/summary_diff.py and paste the summary here for the next iteration.')
