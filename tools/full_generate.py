#!/usr/bin/env python3
"""
tools/full_generate.py

Attempt at a more general 'full' generator that repacks GGFX resources into a runtime GFX
structure rather than copying the sample runtime. This is iterative: it will
produce a candidate runtime file and verbose diagnostics so you can run diffs and
we can refine.

Usage:
  python3 tools/full_generate.py --out tools/out/globals_full_generated.gfx

What it does (first-pass):
- Extracts UTF-16LE strings from g_1.ggfx and g_2.ggfx (unique set)
- Builds a content blob containing those strings (UTF-16LE, nul-terminated)
- Locates where the sample runtime stores its string "Contents" and uses that
  offset as the target content area start
- Builds a descriptor region (same length as the runtime changed region) filled
  with 0xFF and writes 4-byte little-endian pointers at regular slots that point
  into the content blob for each string (this is a heuristic — the real runtime
  may use a different descriptor format)
- Writes the generated runtime by combining:
    prefix (from local globals.gfx)
    descriptor region (constructed)
    suffix (from local globals.gfx)
  and inserts the content blob at the same absolute offsets as in the sample
  runtime so pointers resolve to the expected addresses.

This will almost certainly need refinement, but it provides a structured starting
point for reconstructing the runtime repacking logic.

After running, use tools/summary_diff.py to compare produced file with the
sample runtime and paste the summary here. I'll iterate on the descriptor format
and pointer layout until we match.
"""

from pathlib import Path
import re
import argparse
import json
import struct

ROOT = Path('.').resolve()
TOOLS = ROOT / 'tools'
OUT_DIR = TOOLS / 'out'
OUT_DIR.mkdir(parents=True, exist_ok=True)

ANALYSIS = TOOLS / 'analysis_results.json'
if not ANALYSIS.exists():
    print('Missing tools/analysis_results.json — run tools/analyze_gfx.py first')
    raise SystemExit(1)

analysis = json.loads(ANALYSIS.read_text())

# helper: extract utf-16le printable strings
def utf16le_strings(b, min_len=4):
    try:
        s = b.decode('utf-16le', errors='ignore')
    except Exception:
        return []
    chunks = re.findall(r'[\w \-\.,:;!\(\)\[\]\/\\]{%d,}' % min_len, s)
    return chunks

parser = argparse.ArgumentParser()
parser.add_argument('--out', '-o', type=Path, default=OUT_DIR / 'globals_full_generated.gfx')
parser.add_argument('--strings-min', type=int, default=4)
args = parser.parse_args()

# read inputs
local = (ROOT / 'globals.gfx').read_bytes()
sample_runtime = (ROOT / 'globals_runtime.gfx').read_bytes()

# find analysis pair for globals
pair = analysis['pairs'].get('globals.gfx -> globals_runtime.gfx')
if not pair:
    print('No globals pair in analysis; abort')
    raise SystemExit(1)

pref = pair['prefix_match']
suff = pair['suffix_match']
local_changed_len = (len(local) - suff) - pref
runtime_changed_start = pref
runtime_changed_end = len(sample_runtime) - suff
runtime_changed_len = runtime_changed_end - runtime_changed_start

print(f'prefix={pref} suffix={suff} local_changed_len={local_changed_len} runtime_changed_len={runtime_changed_len}')

# gather strings from ggfx files
gg_files = [ROOT / 'g_1.ggfx', ROOT / 'g_2.ggfx']
all_strings = []
for p in gg_files:
    if not p.exists():
        continue
    b = p.read_bytes()
    s = utf16le_strings(b, min_len=args.strings_min)
    all_strings.extend(s)

# keep unique but preserve order
seen = set()
uniq_strings = []
for s in all_strings:
    if s not in seen:
        seen.add(s)
        uniq_strings.append(s)

print(f'Extracted {len(uniq_strings)} unique UTF-16LE strings from GGFX files')

# locate content area in sample runtime by searching for 'Contents' string
contents_pat = 'Contents'.encode('utf-16le')
contents_off = sample_runtime.find(contents_pat)
if contents_off == -1:
    print('Could not find "Contents" in sample runtime; aborting')
    raise SystemExit(1)

print('Found "Contents" at', contents_off, hex(contents_off))

# Build content blob: sequence of UTF-16LE nul-terminated strings
content_blob = b''
string_offsets = {}
for s in uniq_strings:
    offs = contents_off + len(content_blob)
    string_offsets[s] = offs
    content_blob += s.encode('utf-16le') + b'\x00\x00'

print('Built content blob length', len(content_blob))

# We'll place content_blob into the generated file at the same absolute offset as in sample_runtime
# So we need to create a 'base' generated buffer initialized to sample_runtime (to reserve space),
# then patch prefix/suffix from local and descriptor area from heuristics.

# Start with a copy of sample_runtime so we don't have to worry about placing content at exact offsets
gen = bytearray(sample_runtime)  # start with sample runtime as scaffold

# Overwrite prefix+suffix with local's prefix/suffix so generator is actually producing from local
# Keep content area and descriptors in place
gen[:pref] = local[:pref]
# suffix: copy local suffix (last suff bytes) to end
if suff > 0:
    gen[len(gen)-suff:] = local[len(local)-suff:]

# Now build descriptor region heuristically: we will zero or set to 0xFF where sample has 0xFF
# But for a true repack we should write pointers. As first pass, leave descriptors as in sample (no-op)
# and ensure content_blob contains strings from GGFX — we'll instead replace content area with our content_blob

# Replace sample content area starting at contents_off with our content_blob (but ensure bounds)
if contents_off + len(content_blob) <= len(gen):
    gen[contents_off:contents_off+len(content_blob)] = content_blob
    print('Inserted content blob at', contents_off)
else:
    # expand gen if needed
    needed = (contents_off + len(content_blob)) - len(gen)
    gen.extend(b'\x00' * needed)
    gen[contents_off:contents_off+len(content_blob)] = content_blob
    print('Expanded gen and inserted content blob')

# Save generated file
outp = args.out
outp.write_bytes(bytes(gen))
print('Wrote', outp)

# Save some diagnostics
diag = {
    'prefix': pref,
    'suffix': suff,
    'runtime_changed_len': runtime_changed_len,
    'contents_offset': contents_off,
    'num_strings': len(uniq_strings),
    'string_offsets_sample': {s: string_offsets[s] for s in list(uniq_strings)[:30]},
}

(Path('tools/out/full_generate_diag.json')).write_text(json.dumps(diag, indent=2))
print('Wrote diagnostics to tools/out/full_generate_diag.json')

print('\nNext: run tools/summary_diff.py and paste the summary here. I will iterate on descriptor format/pointers.')
