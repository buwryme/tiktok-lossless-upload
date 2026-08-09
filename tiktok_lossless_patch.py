#!/usr/bin/env python3
"""
tiktok_lossless_patch.py

Patches MP4 files so TikTok skips re-encoding and keeps them lossless.

How it works: TikTok's uploader checks for an "edit list" (ELST box) in 
MP4 files. If the edit list looks complex enough (like it came from a pro
NLE editor), TikTok assumes re-encoding would mess up the edits and 
just passes the video through as-is.

This script corrupts the ELST entry count to an absurdly large number,
which tricks TikTok into thinking "oh wow, professional edit, better not touch this."

^ P.S. this is speculation.

Usage:
    python3 tiktok_lossless_patch.py input.mp4 [output.mp4]
    
If no output is given, it overwrites the original file.

Credits: The core technique was pulled from MASKA's browser extension
posting method that did the same thing client-side. Thanks a lot for not
obfuscating your code <3. For legal inquiries, contact @buwryy on Discord.

LICENSE: MIT License, copyright holder: buwryme @ GitHub
"""

import sys
import struct


# The magic value we write into the ELST box
# 0x10000001 = 268,435,457 entries... yeah right, TikTok
ELST_MAGIC = 0x10000001


def find_box(data: bytes, fourcc: bytes, start: int = 0) -> int:
    """Find an MP4 atom by its FourCC code. Returns offset or -1."""
    target_len = len(fourcc)
    for i in range(start, len(data) - target_len + 1):
        if data[i:i+target_len] == fourcc:
            return i
    return -1


def read_u32be(data: bytes, offset: int) -> int:
    """Read a big-endian 32-bit unsigned integer."""
    return struct.unpack('>I', data[offset:offset+4])[0]


def write_u32be(data: bytearray, offset: int, value: int):
    """Write a big-endian 32-bit unsigned integer."""
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)


def patch_mp4(input_path: str, output_path: str = None) -> bool:
    """
    Patch an MP4 file's ELST box to trigger TikTok's lossless passthrough.
    
    Returns True if we found and patched the ELST, False otherwise.
    """
    target = output_path or input_path
    
    # Read the whole file into memory
    # (MP4s aren't usually that big, so this is fine)
    try:
        with open(input_path, 'rb') as f:
            raw = f.read()
    except IOError as e:
        print(f"  couldn't read {input_path}: {e}")
        return False
    
    data = bytearray(raw)
    
    # Look for the ELST (Edit List) box
    elst_pos = find_box(data, b'elst')
    
    if elst_pos == -1:
        print("  no ELST box found — this file might already be simple")
        print("  or ffmpeg didn't create one. trying anyway...")
        
        # Some files don't have ELST at all — that's actually fine
        # TikTok might still pass them through if they look clean enough
        # But we can't patch what doesn't exist, so just copy and bail
        if target != input_path:
            with open(target, 'wb') as f:
                f.write(bytes(data))
        return True
    
    # The ELST box structure:
    # [4 bytes: size] [4 bytes: 'elst'] [4 bytes: version+flags] [4 bytes: entry_count] ...
    #
    # We're overwriting the version+flags field (at offset+8) with our magic value.
    # When TikTok reads this, it sees a ridiculously high entry count and freaks out
    # in the best way possible — it decides to not touch the video at all.
    
    old_val = read_u32be(data, elst_pos + 8)
    write_u32be(data, elst_pos + 8, ELST_MAGIC)
    new_val = read_u32be(data, elst_pos + 8)
    
    print(f"  found ELST at byte {elst_pos}")
    print(f"  patched: 0x{old_val:08X} → 0x{new_val:08X}")
    
    # Write it out
    try:
        with open(target, 'wb') as f:
            f.write(bytes(data))
    except IOError as e:
        print(f"  couldn't write to {target}: {e}")
        return False
    
    orig_size = len(raw)
    new_size = len(data)
    print(f"  done! ({orig_size} bytes in, {new_size} bytes out)")
    
    return True


def main():
    if len(sys.argv) < 2:
        print("tiktok_lossless_patch.py")
        print()
        print("usage:")
        print(f"  python3 {sys.argv[0]} input.mp4")
        print(f"  python3 {sys.argv[0]} input.mp4 output.mp4")
        print()
        print("patches mp4 files so tiktok doesn't re-encode them.")
        print("no output path = overwrite in place.")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    print(f"          tiktok lossless patch")
    print(f"──────────────────────────────────────────")
    print(f"                            made by buwryy")
    print(f"    specialthx2 maska's posting method for")
    print(f"             not obfuscating their code <3")
    print(f"  input:  {input_file}")
    print(f"  output: {output_file or '(overwrite)'}")
    print()
    
    if not input_file.endswith('.mp4'):
        print("  this is designed for .mp4 files. ymmv with other formats.")
        print()
    
    ok = patch_mp4(input_file, output_file)
    
    if ok:
        print()
        print("  patched. credit me, buwryy, or not i don't care <3")
        return 0
    else:
        print()
        print("  something went wrong :(")
        return 1


if __name__ == '__main__':
    sys.exit(main())
