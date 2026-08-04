#!/usr/bin/env python3
"""
analyze_gfx.py

Binary analysis helpers to compare local vs runtime GFX/GGFX files in this repo.

Usage (run from the repository root):
  python3 tools/analyze_gfx.py

It will look for these files by name in the current directory:
  blank.gfx blank_runtime.gfx
  btn.gfx btn_runtime.gfx
  globals.gfx globals_runtime.gfx
  g_1.ggfx g_1_runtime.ggfx
  g_2.ggfx g_2_runtime.ggfx

Outputs to stdout a summary of sizes, diffs, and searches for GGFX bytes inside runtime files.
"""

import os
import sys
from pathlib import Path
import json

PAIRS = [
    ("blank.gfx", "blank_runtime.gfx"),
    ("btn.gfx", "btn_runtime.gfx"),
    ("globals.gfx", "globals_runtime.gfx"),
    ("g_1.ggfx", "g_1_runtime.ggfx"),
    ("g_2.ggfx", "g_2_runtime.ggfx"),
]

GGFX_FILES = ["g_1.ggfx", "g_2.ggfx"]


def read_bytes(path):
    p = Path(path)
    if not p.exists():
        print(f"MISSING: {path}")
        return None
    return p.read_bytes()


def find_all(hay: bytes, needle: bytes):
    start = 0
    while True:
        i = hay.find(needle, start)
        if i == -1:
            break
        yield i
        start = i + 1


def prefix_suffix_diff(a: bytes, b: bytes):
    # return (prefix_len, suffix_len) where a and b match prefix and suffix
    la, lb = len(a), len(b)
    pref = 0
    while pref < la and pref < lb and a[pref] == b[pref]:
        pref += 1
    # suffix match (careful with overlap)
    s1 = la - 1
    s2 = lb - 1
    suff = 0
    while s1 >= pref and s2 >= pref and a[s1] == b[s2]:
        suff += 1
        s1 -= 1
        s2 -= 1
    return pref, suff


def hexdump_region(b: bytes, off: int, length: int=64):
    return b[off:off+length].hex()


def main():
    repo_root = Path('.')
    results = {"pairs": {}, "ggfx_search": {}}

    # pair diffs
    for a, b in PAIRS:
        A = read_bytes(a)
        B = read_bytes(b)
        if A is None or B is None:
            continue
        results['pairs'][f"{a} -> {b}"] = {
            'size_a': len(A),
            'size_b': len(B)
        }
        pref, suff = prefix_suffix_diff(A, B)
        results['pairs'][f"{a} -> {b}"]['prefix_match'] = pref
        results['pairs'][f"{a} -> {b}"]['suffix_match'] = suff
        # changed region(s)
        changed_a = (pref, len(A) - suff - 1) if (len(A) - suff - 1) >= pref else (pref, pref)
        changed_b = (pref, len(B) - suff - 1) if (len(B) - suff - 1) >= pref else (pref, pref)
        results['pairs'][f"{a} -> {b}"]['changed_region_a'] = changed_a
        results['pairs'][f"{a} -> {b}"]['changed_region_b'] = changed_b

    # search for GGFX blobs inside globals_runtime.gfx and globals.gfx
    globals_runtime = read_bytes('globals_runtime.gfx')
    globals_local = read_bytes('globals.gfx')
    if globals_runtime is None:
        print("globals_runtime.gfx missing, aborting GGFX search")
    else:
        for g in GGFX_FILES:
            nug = read_bytes(g)
            if nug is None:
                continue
            hits_full = list(find_all(globals_runtime, nug))
            results['ggfx_search'][g] = {
                'len': len(nug),
                'full_matches_in_globals_runtime': hits_full,
            }
            # if no full matches, try searching for partial matches: first/last N bytes
            if not hits_full:
                for win in (256, 128, 64, 32):
                    head = nug[:win]
                    tail = nug[-win:]
                    h1 = list(find_all(globals_runtime, head))
                    h2 = list(find_all(globals_runtime, tail))
                    results['ggfx_search'][g][f'head_{win}_matches'] = h1
                    results['ggfx_search'][g][f'tail_{win}_matches'] = h2

    # also compute difference regions between globals.gfx and globals_runtime.gfx and dump hexdumps
    if globals_local is not None and globals_runtime is not None:
        pref, suff = prefix_suffix_diff(globals_local, globals_runtime)
        results['globals_diff'] = {
            'size_local': len(globals_local),
            'size_runtime': len(globals_runtime),
            'prefix_match': pref,
            'suffix_match': suff,
        }
        # show some sample bytes around change boundaries
        a_start = pref
        a_end = len(globals_local) - suff
        b_start = pref
        b_end = len(globals_runtime) - suff
        results['globals_diff']['local_changed_hexdump_start'] = hexdump_region(globals_local, max(0, a_start-32), 128)
        results['globals_diff']['runtime_changed_hexdump_start'] = hexdump_region(globals_runtime, max(0, b_start-32), 128)
        results['globals_diff']['local_changed_hexdump_end'] = hexdump_region(globals_local, max(0, a_end-64), 128)
        results['globals_diff']['runtime_changed_hexdump_end'] = hexdump_region(globals_runtime, max(0, b_end-64), 128)

    # write JSON to stdout and to file analysis_results.json
    out = json.dumps(results, indent=2)
    print(out)
    Path('tools/analysis_results.json').parent.mkdir(parents=True, exist_ok=True)
    Path('tools/analysis_results.json').write_text(out)

if __name__ == '__main__':
    main()
