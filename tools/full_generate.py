#!/usr/bin/env python3
"""
tools/full_generate.py

Heuristic full generator that repacks GGFX strings into the runtime layout.

This revision implements a deterministic mapping write: instead of relying
on fuzzy matching to repair individual mapping entries, we now write the
mapping-block sequentially so each index maps directly to the generated
string offset we placed into the content area. Unused mapping slots are
filled with 0xFFFFFFFF to avoid accidental valid pointers.

Usage:
  python3 tools/full_generate.py --out tools/out/globals_full_generated.gfx

Output:
 - tools/out/globals_full_generated.gfx
 - tools/out/globals_generated_runtime.gfx (convenience)
 - tools/out/full_generate_diag.json (diagnostics with patched/skipped info)
"""

from pathlib import Path
import re
import json
import argparse
import unicodedata
import difflib

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

# normalize string for fuzzy matching
def normalize(s):
    if not s:
        return ''
    # Unicode normalization and remove combining marks
    s = s.replace('\ufeff','')
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    # keep only printable characters
    s = ''.join(ch for ch in s if ch.isprintable())
    return s.strip().lower()

parser = argparse.ArgumentParser()
parser.add_argument('--out', '-o', type=Path, default=OUT / 'globals_full_generated.gfx')
parser.add_argument('--strings-min', type=int, default=3)
parser.add_argument('--match-threshold', type=float, default=0.60,
                    help='Minimum similarity score (0-1) for best-match fallback — not used in deterministic mode')
parser.add_argument('--deterministic', action='store_true', default=True,
                    help='Write deterministic mapping table (default: True)')
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
    fallback = 0x0c80
    if fallback + 4 <= file_len:
        print('Falling back to', hex(fallback))
        best = fallback
    else:
        print('Could not locate mapping block; aborting')
        raise SystemExit(1)

mapping_start = best
mapping_end = mapping_start + window_u32s*4
# trim leading/ trailing 0xFF sequences
while mapping_start < mapping_end and all(b == 0xFF for b in sbytes[mapping_start:mapping_start+4]):
    mapping_start += 4
while mapping_end > mapping_start and all(b == 0xFF for b in sbytes[mapping_end-4:mapping_end]):
    mapping_end -= 4

print('Mapping block candidate:', hex(mapping_start), hex(mapping_end), '(%d bytes)' % (mapping_end-mapping_start))

# Build a map of decoded sample content strings -> offset (normalized)
sample_strings = {}
p = contents_off
while p + 2 < file_len:
    end = p
    while end + 1 < min(file_len, p + 4096):
        if sbytes[end:end+2] == b'\x00\x00':
            break
        end += 2
    if end >= file_len or end == p:
        break
    try:
        s = sbytes[p:end].decode('utf-16le', errors='ignore')
    except Exception:
        break
    if len(s) >= 1:
        n = normalize(s)
        if n:
            sample_strings[n] = p
    p = end + 2

print('Sample content decoded strings:', len(sample_strings))

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

# Also build normalized map for our generated strings
gen_norm_to_off = {}
for s, off in string_offsets.items():
    n = normalize(s)
    if n:
        gen_norm_to_off[n] = off

patched = []
skipped_positions = []

# Deterministic mapping: write mapping entries sequentially so index i -> offset of uniq[i]
num_slots = (mapping_end - mapping_start) // 4
print('Mapping slots:', num_slots, 'strings:', len(uniq))
for i in range(num_slots):
    pos = mapping_start + i*4
    if i < len(uniq):
        off = string_offsets[uniq[i]]
        gen[pos:pos+4] = off.to_bytes(4, 'little')
        patched.append({'pos': pos, 'old': int.from_bytes(sbytes[pos:pos+4], 'little'), 'new': off, 'string': uniq[i], 'method': 'deterministic'})
    else:
        # mark unused slots with 0xFFFFFFFF to avoid pointing into content accidentally
        gen[pos:pos+4] = (0xFFFFFFFF).to_bytes(4, 'little')

print('Deterministic mapping written; patched slots:', len(patched))

# Also write sequential index table entries if sample had sequential small indices
idx_table_off = None
for start in range(PREF, PREF + 0x400, 4):
    ok = True
    if start + 32 >= len(sbytes):
        break
    first = int.from_bytes(sbytes[start:start+4], 'little')
    if not (0 < first < 0x1000):
        continue
    for j in range(1, 8):
        v = int.from_bytes(sbytes[start + j*4:start + j*4 +4], 'little')
        if v != first + j:
            ok = False; break
    if ok:
        idx_table_off = start
        break

if idx_table_off is None:
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

# Overwrite prefix/suffix from SAMPLE so header matches sample runtime exactly
gen[:PREF] = sample[:PREF]
if SUFF > 0:
    gen[len(gen)-SUFF:] = sample[len(sample)-SUFF:]

outp = args.out
outp.write_bytes(bytes(gen))
# Also write convenience filename used in earlier diffs
alternate = OUT / 'globals_generated_runtime.gfx'
alternate.write_bytes(bytes(gen))
print('Wrote generated runtime to', outp, 'and', alternate)

# write diagnostics
diag = {
    'contents_off': contents_off,
    'mapping_start': mapping_start,
    'mapping_end': mapping_end,
    'mapping_len': mapping_end-mapping_start,
    'patched_count': len(patched),
    'skipped_count': len(skipped_positions),
    'patched_entries': patched,
    'skipped_entries': skipped_positions,
    'num_strings': len(uniq),
    'idx_table_off': idx_table_off,
    'deterministic': True
}
(Path('tools/out/full_generate_diag.json')).write_text(json.dumps(diag, indent=2))
print('Wrote diagnostics to tools/out/full_generate_diag.json')
print('\nDone. Run tools/summary_diff.py and paste the summary here for the next iteration.')
