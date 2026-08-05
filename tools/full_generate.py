#!/usr/bin/env python3
"""
tools/full_generate.py

Refined full generator (heuristic) that attempts to repack GGFX strings into the
runtime layout by:
 - locating the runtime content area (searching for "Contents")
 - extracting unique UTF-16LE strings from g_1.ggfx and g_2.ggfx
 - building a content blob placed at the same content offset as the sample runtime
 - locating an index table in the sample runtime (a run of small increasing u32s)
 - rewriting the index table entries to point at the absolute offsets of the
   corresponding strings in the content blob
 - producing a generated runtime file that keeps the runtime's descriptor
   layout (changed-region dwords) intact (they appear to be indices into that
   index table) but whose table points at our newly-built content

This is still heuristic and may require further iterations, but it avoids
copying the entire changed-region bytes and instead reconstructs a plausible
index->content mapping used by the runtime.

Usage:
  python3 tools/full_generate.py --out tools/out/globals_full_generated.gfx

After running, compare with the sample using tools/summary_diff.py and paste
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
    # permissive regex for visible chars
    chunks = re.findall(r'[\w \-\.,:;!\(\)\[\]\/\\\u00A0-\uFFFF]{%d,}' % min_len, s)
    return chunks

parser = argparse.ArgumentParser()
parser.add_argument('--out', '-o', type=Path, default=OUT / 'globals_full_generated.gfx')
parser.add_argument('--strings-min', type=int, default=3)
args = parser.parse_args()

# read files
local = (ROOT / 'globals.gfx').read_bytes()
sample = (ROOT / 'globals_runtime.gfx').read_bytes()

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

# Heuristic: find index table in sample by searching for a run of increasing small u32s
# We'll scan for a region where many consecutive u32s are small increasing integers
sbytes = sample
def find_increasing_u32_run(buf, min_run=6):
    L = len(buf)
    for off in range(0, L - 4*min_run):
        run = True
        prev = None
        length = 0
        for i in range(min_run):
            v = int.from_bytes(buf[off + i*4: off + i*4 +4], 'little')
            if i == 0:
                prev = v
                length = 1
                continue
            if v == prev + 1:
                prev = v
                length += 1
            else:
                run = False
                break
        if run and length >= min_run:
            return off
    return None

idx_table_off = find_increasing_u32_run(sbytes)
print('Found candidate index-table at', idx_table_off)

# fallback: look for the particular sequence seen earlier (0x11,0x12,..) by searching for bytes
if idx_table_off is None:
    # search for pattern of bytes 11 00 00 00 12 00 00 00 ... up to length 8
    for start in range(0, len(sbytes)-4*8):
        ok = True
        for i in range(8):
            v = int.from_bytes(sbytes[start + i*4:start + i*4 +4], 'little')
            if v != (0x11 + i):
                ok = False; break
        if ok:
            idx_table_off = start; break
    print('Fallback search index-table at', idx_table_off)

if idx_table_off is None:
    print('Could not find an index table; aborting heuristic generator')
    raise SystemExit(1)

# Read how many entries are in that table by scanning until non-reasonable values
entries = []
for i in range(0, 1024):
    pos = idx_table_off + i*4
    if pos +4 > len(sbytes): break
    v = int.from_bytes(sbytes[pos:pos+4], 'little')
    # consider a table entry valid if it's small (< 0x10000) or appears to be an offset into file
    if v == 0 or v == 0xFFFFFFFF:
        # allow zeros/ff as table entries, but stop if many consecutive invalids
        entries.append(v)
        continue
    entries.append(v)
    # simple stop if we see a long run of 0xFFFFFFFF (likely past end)
    if len(entries) > 2000:
        break

num_entries = len(entries)
print('Index table entries (count estimate):', num_entries)

# Now plan: build new sample image by copying sample, then overwriting content area with our content_blob
# and overwriting index table entries (first N) with absolute offsets for the strings we have
gen = bytearray(sample)
# insert content blob
if contents_off + len(content_blob) <= len(gen):
    gen[contents_off:contents_off+len(content_blob)] = content_blob
else:
    # expand
    need = contents_off + len(content_blob) - len(gen)
    gen.extend(b'\x00' * need)
    gen[contents_off:contents_off+len(content_blob)] = content_blob
print('Inserted content blob into generated image')

# Overwrite index table entries: for i in range(min(len(uniq), num_entries)) write absolute offsets
for i, s in enumerate(uniq):
    if i >= num_entries:
        break
    off = idx_table_off + i*4
    val = string_offsets[s]
    gen[off:off+4] = val.to_bytes(4, 'little')
print('Wrote', min(len(uniq), num_entries), 'index table entries pointing at new strings')

# Finally, overwrite prefix/suffix from local so header comes from local file (keeps structure consistent)
gen[:PREF] = local[:PREF]
if SUFF > 0:
    gen[len(gen)-SUFF:] = local[len(local)-SUFF:]

outp = args.out
outp.write_bytes(bytes(gen))
print('Wrote generated runtime to', outp)

# write diagnostics
diag = {
    'contents_off': contents_off,
    'idx_table_off': idx_table_off,
    'num_index_entries': num_entries,
    'num_strings': len(uniq),
    'string_offsets_sample': {s: string_offsets[s] for s in list(uniq)[:50]}
}
(Path('tools/out/full_generate_diag.json')).write_text(json.dumps(diag, indent=2))
print('Wrote diagnostics to tools/out/full_generate_diag.json')
print('\nDone. Run tools/summary_diff.py and paste the summary here for the next iteration.')
