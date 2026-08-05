#!/usr/bin/env python3
from pathlib import Path

# Inspect small u32 values in the changed region and check whether they
# are absolute offsets into the runtime file and what they point at.
f = open("tools/out/descriptors_output.txt", "w")
data = Path('tools/out/globals_full_generated.gfx').read_bytes()
PREFIX = 60
SUFFIX = 309
changed_start = PREFIX
changed_end = len(data) - SUFFIX
changed = data[changed_start:changed_end]

# find content area
contents_pat = 'Contents'.encode('utf-16le')
content_off = data.find(contents_pat)

f.write(f"changed region: 0x{changed_start:08x}-0x{changed_end:08x}\n")
f.write(f"content area ('Contents') offset: {content_off}, {hex(content_off)}\n")
f.write("\n")

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
    f.write(f"Changed-region dword at 0x{off:08x} = {val} (0x{val:08x})\n")
    # if val looks like an absolute offset inside file, dump hex + try decode UTF-16LE
    if 0 <= val < len(data):
        f.write("  -> absolute addr exists in file; hexdump 32 bytes:\n")
        chunk = data[val:val+32]
        f.write(f"     {chunk.hex()}\n")
        # try decode as utf-16le snippet
        try:
            s = chunk.decode('utf-16le', errors='ignore').split('\x00',1)[0]
            f.write(f"     decodes to (utf-16le): {repr(s[:80])}\n")
        except Exception:
            pass
        # also print relative to content area if content_off found
        if content_off != -1:
            rel = val - content_off
            f.write(f"     relative to content_off: {rel} 0x{rel:x}\n")
    else:
        f.write("  -> value is not a valid absolute offset in file\n")
    f.write("\n")

f.write("Done.\n")
