#!/usr/bin/env python3
"""
tools/full_generate.py

Deterministic in-place content patching.

This change updates the generator to patch strings in-place inside the
sample content area (starting at the "Contents" marker) rather than
replacing it with a single contiguous blob. That preserves runtime
markers (0xFFFF sequences, separators) and writes generated strings only
into the same offsets the runtime expects. If a generated string is
longer than the sample slot it replaces, we will skip that slot (emit a
warning) to avoid shifting the runtime layout.

Usage:
  python3 tools/full_generate.py --out tools/out/globals_full_generated.gfx

Output:
 - tools/out/globals_full_generated.gfx
 - tools/out/globals_generated_runtime.gfx (convenience)
 - tools/out/full_generate_diag.json (diagnostics)
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
    s = s.replace('\ufeff','')
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = ''.join(ch for ch in s if ch.isprintable())
    return s.strip().lower()

parser = argparse.ArgumentParser()
parser.add_argument('--out', '-o', type=Path, default=OUT / 'globals_full_generated.gfx')
parser.add_argument('--strings-min', type=int, default=3)
parser.add_argument('--match-threshold', type=float, default=0.40,
                    help='Minimum similarity score (0-1) for best-match fallback')
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

# build normalized lookup for generated strings
gen_norm = {}
for s in uniq:
    n = normalize(s)
    if n:
        gen_norm[n] = s

# parse sample content area into slots: (offset, length, decoded_str)
sbytes = sample
file_len = len(sbytes)
slots = []
p = contents_off
while p + 1 < file_len:
    # if marker (0xFFFF x N) or all FF's in next 2 bytes, treat as marker and advance by 2
    if sbytes[p:p+2] == b'\xff\xff':
        # record marker slot
        slots.append((p, 2, None))
        p += 2
        continue
    # otherwise, read until null terminator (utf-16le)
    end = p
    while end + 1 < min(file_len, p + 4096):
        if sbytes[end:end+2] == b'\x00\x00':
            break
        end += 2
    if end == p:
        # empty slot; advance
        slots.append((p, 2, ''))
        p += 2
        continue
    try:
        s = sbytes[p:end].decode('utf-16le', errors='ignore')
    except Exception:
        s = ''
    slot_len = end + 2 - p
    slots.append((p, slot_len, s))
    p = end + 2

print('Parsed', len(slots), 'content slots from sample')

# prepare generated image copy
gen = bytearray(sample)

patched = []
skipped = []
replaced_count = 0

# function to pick best matching generated string for a sample string
from difflib import SequenceMatcher

def pick_best(sample_str):
    n = normalize(sample_str)
    if not n:
        return None, 0.0
    # exact
    if n in gen_norm:
        return gen_norm[n], 1.0
    # substring
    for gn, gs in gen_norm.items():
        if n in gn or gn in n:
            return gs, 0.95
    # best difflib
    best_score = 0.0
    best_s = None
    for gn, gs in gen_norm.items():
        score = SequenceMatcher(None, n, gn).ratio()
        if score > best_score:
            best_score = score
            best_s = gs
    # enforce threshold
    if best_score < args.match_threshold:
        return None, best_score
    return best_s, best_score

# iterate slots and replace in-place if matched and fits
for off, length, s in slots:
    if s is None:
        # marker, skip
        continue
    if s == '':
        # empty slot; skip
        skipped.append({'off': off, 'len': length, 'reason': 'empty_slot'})
        continue
    best_s, score = pick_best(s)
    if best_s is None:
        skipped.append({'off': off, 'len': length, 'orig': s, 'reason': 'no_candidate', 'score': score})
        continue
    # encode candidate
    enc = best_s.encode('utf-16le') + b'\x00\x00'
    if len(enc) > length:
        # candidate doesn't fit: skip
        skipped.append({'off': off, 'len': length, 'orig': s, 'candidate': best_s, 'cand_len': len(enc), 'reason': 'no_space', 'score': score})
        continue
    # write enc into gen at off, pad remaining with 0x00
    gen[off:off+len(enc)] = enc
    if len(enc) < length:
        gen[off+len(enc):off+length] = b'\x00' * (length - len(enc))
    patched.append({'off': off, 'len': length, 'orig': s, 'candidate': best_s, 'score': score})
    replaced_count += 1

print('Replaced', replaced_count, 'slots; skipped', len(skipped))

# Build normalized slot -> offset map to write mapping table deterministically
map_norm_to_off = {}
for off, length, orig in slots:
    if not orig or orig is None:
        continue
    n = normalize(orig)
    if not n:
        continue
    # prefer larger slot if duplicate normalized key
    prev = map_norm_to_off.get(n)
    if prev is None:
        map_norm_to_off[n] = off
    else:
        # keep the one with larger slot length (prefer room)
        # find prev length
        for o2, l2, _ in slots:
            if o2 == prev:
                if length > l2:
                    map_norm_to_off[n] = off
                break

# Deterministic mapping table write (improved)
search_lo = 0x0400
search_hi = min(0x2000, file_len - 4)
window_u32s = 256
best = None
best_score = -1
for start in range(search_lo, search_hi - window_u32s*4, 4):
    cnt = 0
    for i in range(window_u32s):
        pos = start + i*4
        v = int.from_bytes(sbytes[pos:pos+4], 'little')
        if contents_off <= v < file_len:
            cnt += 1
    if cnt > best_score:
        best_score = cnt
        best = start

if best is None:
    print('Could not locate mapping table heuristically; aborting mapping write')
    mapping_start = search_lo
else:
    mapping_start = best
mapping_end = mapping_start + window_u32s*4
# trim leading/ trailing 0xFF sequences
while mapping_start < mapping_end and all(b == 0xFF for b in sbytes[mapping_start:mapping_start+4]):
    mapping_start += 4
while mapping_end > mapping_start and all(b == 0xFF for b in sbytes[mapping_end-4:mapping_end]):
    mapping_end -= 4

num_slots = (mapping_end - mapping_start) // 4
print('Mapping block:', hex(mapping_start), hex(mapping_end), 'slots:', num_slots)
for i in range(num_slots):
    pos = mapping_start + i*4
    if i < len(uniq):
        sname = uniq[i]
        n = normalize(sname)
        off = map_norm_to_off.get(n)
        if off is None:
            # try to find a patched entry matching this name
            for entry in patched:
                if normalize(entry.get('candidate','')) == n or normalize(entry.get('orig','')) == n:
                    off = entry['off']; break
        if off is None:
            # not found -> write sentinel
            gen[pos:pos+4] = (0xFFFFFFFF).to_bytes(4, 'little')
        else:
            gen[pos:pos+4] = int(off).to_bytes(4, 'little')
    else:
        gen[pos:pos+4] = (0xFFFFFFFF).to_bytes(4, 'little')

# Overwrite prefix/suffix from SAMPLE
gen[:PREF] = sample[:PREF]
if SUFF > 0:
    gen[len(gen)-SUFF:] = sample[len(sample)-SUFF:]

outp = args.out
outp.write_bytes(bytes(gen))
alternate = OUT / 'globals_generated_runtime.gfx'
alternate.write_bytes(bytes(gen))

# write diagnostics
diag = {
    'contents_off': contents_off,
    'parsed_slots': len(slots),
    'replaced': replaced_count,
    'skipped_slots': len(skipped),
    'skipped_details': skipped,
    'mapping_start': mapping_start,
    'mapping_end': mapping_end,
    'mapping_len': mapping_end-mapping_start,
    'num_strings': len(uniq)
}
(Path('tools/out/full_generate_diag.json')).write_text(json.dumps(diag, indent=2))
print('Wrote diagnostics to tools/out/full_generate_diag.json')
print('\nDone. Run tools/summary_diff.py and paste the summary here for the next iteration.')
