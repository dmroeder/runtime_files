#!/usr/bin/env python3
from pathlib import Path

# Inspect small u32 values in the changed region and check whether they
# are absolute offsets into the runtime file and what they point at.

data = Path('globals_runtime.gfx').read_bytes()
PREFIX = 60
SUFFIX = 309
changed_start = PREFIX
changed_end = len(data) - SUFFIX
changed = data[changed_start:changed_end]

# find content area
contents_pat = 'Contents'.encode('utf-16le')
content_off = data.find(contents_pat)

print(f'changed region: 0x{changed_start:08x}-0x{changed_end:08x}')
print('content area ("Contents") offset:', content_off, hex(content_off))
print()

# collect small u32 values in changed region
small_vals = []
for offset in range(0, len(changed), 4):
    d = int.from_bytes(changed[offset:offset+4], 'little', signed=False)
    if d <= 0xFFFF:  # focus on small values and possible content-relative offsets
        small_vals.append((changed_start + offset, d))

# deduplicate/preserve order
seen = set()
filtered = []
for off, val in small_vals:
    if (off, val) not in seen:
        seen.add((off, val))
        filtered.append((off, val))

for off, val in filtered:
    print(f'Changed-region dword at 0x{off:08x} = {val} (0x{val:08x})')
    # if val looks like an absolute offset inside file, dump hex + try decode UTF-16LE
    if 0 <= val < len(data):
        print('  -> absolute addr exists in file; hexdump 32 bytes:')
        chunk = data[val:val+32]
        print('     ', chunk.hex())
        # try decode as utf-16le snippet
        try:
            s = chunk.decode('utf-16le', errors='ignore').split('\x00',1)[0]
            print('     decodes to (utf-16le):', repr(s[:80]))
        except Exception:
            pass
        # also print relative to content area if content_off found
        if content_off != -1:
            rel = val - content_off
            print('     relative to content_off:', rel, f'(0x{rel:x})')
    else:
        print('  -> value is not a valid absolute offset in file')
    print()

print('Done.')
