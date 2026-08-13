#!/usr/bin/env python3
"""
tiktok_lossless_patch.py

TikTok Optimization Pipeline:
  1. Encode with libx264 (ALWAYS - ensures TikTok-compatible output)
  2. Patch ELST boxes for TikTok passthrough

How it works: TikTok's uploader checks for an "edit list" (ELST box) in
MP4 files. If the edit list looks complex enough (like it came from a pro
NLE editor), TikTok assumes re-encoding would mess up the edits and
just passes the video through as-is.

This script corrupts the ELST entry count to an absurdly large number,
which tricks TikTok into thinking "oh wow, professional edit, better not touch this."

^ P.S. this is speculation.

Usage:
    python3 tiktok_lossless_patch.py input_file [output.mp4]

    Accepts ANY input format (mp4, mov, avi, mkv, prores, etc.)
    For best results, use lossless or topaz-enhanced output as input.

If no output is given, auto-generates filename.

Credits:
    MASKA's OSS browser extension for the ELST technique

For legal inquiries, contact @buwryy on Discord.

LICENSE: MIT License, copyright holder: buwryme @ GitHub
"""

import sys
import struct
import subprocess
import os
from pathlib import Path


# The magic value we write into the ELST box
# 0x10000001 = 268,435,457 entries... yeah right, TikTok
ELST_MAGIC = 0x10000001


# ════════════════════════════════════════════════════════════════════════
# MP4 PARSING UTILITIES
# ════════════════════════════════════════════════════════════════════════

def find_box(data: bytes | bytearray, fourcc: bytes | str, start: int = 0) -> int:
    """Find an MP4 atom by its FourCC code. Returns offset or -1."""
    if isinstance(fourcc, str):
        fourcc = fourcc.encode('ascii')
    target_len = len(fourcc)
    for i in range(start, len(data) - target_len + 1):
        if data[i:i+target_len] == fourcc:
            return i
    return -1


def read_u32be(data: bytes | bytearray, offset: int) -> int:
    """Read a big-endian 32-bit unsigned integer."""
    return struct.unpack('>I', data[offset:offset+4])[0]


def write_u32be(data: bytearray, offset: int, value: int):
    """Write a big-endian 32-bit unsigned integer."""
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)


def parse_boxes(data: bytearray, start: int, end: int) -> list[dict]:
    """Parse MP4 boxes in a range. Returns list of {offset, size, type, end} dicts."""
    boxes = []
    pos = start
    while pos + 8 <= end:
        raw_size = read_u32be(data, pos)
        size = raw_size

        # Handle extended size (64-bit)
        if raw_size == 1:
            if pos + 16 > end:
                break
            hi = read_u32be(data, pos + 8)
            lo = read_u32be(data, pos + 12)
            size = (hi << 32) + lo
        elif raw_size == 0:
            size = end - pos

        if size < 8 or pos + size > end:
            break

        atype = data[pos+4:pos+8]
        boxes.append({"offset": pos, "size": size, "type": atype, "end": pos + size})
        pos += size

    return boxes


# ════════════════════════════════════════════════════════════════════════
# ELST PATCHING & METADATA FUNCTIONS
# ════════════════════════════════════════════════════════════════════════

def add_itunes_metadata(data: bytearray) -> bytearray:
    """
    Add iTunes-style metadata so TikTok thinks this came from a pro editor.

    Injects a udta > meta > hdlr atom chain into moov, mimicking what
    Final Cut Pro or Premiere Pro would write.
    """
    moov_pos = find_box(data, b'moov')
    if moov_pos == -1:
        return data

    moov_size = read_u32be(data, moov_pos)

    # Don't double-add if udta already exists
    existing_udta = find_box(data, b'udta', moov_pos)
    if existing_udta != -1 and existing_udta < moov_pos + moov_size:
        print("       udta already there, skipping")
        return data

    # hdlr: handler declaration saying "apple made this"
    hdlr = bytearray([
        0x00, 0x00, 0x00, 0x21,  # size = 33
        0x68, 0x64, 0x6c, 0x72,  # 'hdlr'
        0x00, 0x00, 0x00, 0x00,  # version/flags
        0x00, 0x00, 0x00, 0x00,  # pre_defined
        0x61, 0x70, 0x70, 0x6c,  # handler_type = 'appl'
        0x00, 0x00, 0x00, 0x00,  # reserved
        0x00, 0x00, 0x00, 0x00,  # reserved
        0x00, 0x00, 0x00, 0x00,  # reserved
        0x00,                    # name (empty)
    ])

    # meta container (needs flags byte 0x000001)
    meta_hdr = bytearray([
        0x00, 0x00, 0x00, 0x00,  # size placeholder
        0x6d, 0x65, 0x74, 0x61,  # 'meta'
        0x00, 0x00, 0x00, 0x21,  # version/flags
    ])
    meta_size = 12 + len(hdlr)
    write_u32be(meta_hdr, 0, meta_size)

    # udta wrapper
    udta_inner = meta_hdr + hdlr
    udta_size = 8 + len(udta_inner)
    udta_atom = bytearray(udta_size)
    write_u32be(udta_atom, 0, udta_size)
    udta_atom[4:8] = b'udta'
    udta_atom[8:] = udta_inner

    # Stuff it at the end of moov
    insert_at = moov_pos + moov_size
    data[insert_at:insert_at] = udta_atom

    # Fix moov's size field
    write_u32be(data, moov_pos, moov_size + len(udta_atom))

    print(f"       injected itunes metadata ({len(udta_atom)} bytes)")
    return data


def patch_all_elst(data: bytearray) -> int:
    """
    Patch ALL ELST boxes with Maska magic value.
    Returns count of patched ELST boxes.
    """
    count = 0
    search = 0

    while True:
        pos = find_box(data, b'elst', search)
        if pos == -1:
            break

        if pos + 12 <= len(data):
            old = read_u32be(data, pos + 8)
            entries = read_u32be(data, pos + 12)
            write_u32be(data, pos + 8, ELST_MAGIC)
            print(f"       elst #{count+1} @ {pos}: {old:#010x} -> {ELST_MAGIC:#010x} ({entries} entries)")
            count += 1

        search = pos + 4

    return count


def fix_offsets_recursive(data: bytearray, start: int, end: int, split: int, delta: int):
    """Walk box tree fixing stco/co64 offsets by delta."""
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]

        if typ == b'stco':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                v = read_u32be(data, pos + 16 + i*4)
                if v >= split:
                    write_u32be(data, pos + 16 + i*4, v + delta)
        elif typ == b'co64':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                hi = read_u32be(data, pos + 16 + i*8)
                lo = read_u32be(data, pos + 17 + i*8)
                v = hi * 0x100000000 + lo
                if v >= split:
                    v += delta
                    write_u32be(data, pos + 16 + i*8, (v >> 32) & 0xFFFFFFFF)
                    write_u32be(data, pos + 17 + i*8, v & 0xFFFFFFFF)
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
            fix_offsets_recursive(data, pos + 8, pos + sz, split, delta)

        pos += sz


def normalize_container(data: bytearray) -> tuple[bytearray, bool]:
    """
    Normalize container order to ftyp->moov->mdat with isom brand.
    Returns (data, changed).
    """
    top = parse_boxes(data, 0, len(data))
    ftyp = moov = mdat = None

    for b in top:
        if b["type"] == b"ftyp": ftyp = b
        elif b["type"] == b"moov": moov = b
        elif b["type"] == b"mdat": mdat = b

    if not moov:
        return data, False
    if not mdat:
        return data, True

    # Already in correct order?
    if moov["offset"] < mdat["offset"]:
        # Check ftyp brand
        if ftyp:
            brand = data[ftyp["offset"]+8:ftyp["offset"]+12]
            if brand == b"isom":
                return data, False  # Already good

    # Build new ftyp if needed
    needs_rewrite = False
    new_ftyp = None
    if ftyp:
        brand = data[ftyp["offset"]+8:ftyp["offset"]+12]
        if brand != b"isom":
            needs_rewrite = True
            # Use isom brand (simplified)
            new_ftyp = bytearray([0,0,0,28, 0x66,0x74,0x79,0x70,
                0x69,0x73,0x6f,0x6d, 0,0,2,0,
                0x69,0x73,0x6f,0x6d, 0x69,0x73,0x6f,0x32,
                0x6d,0x70,0x34,0x31])

    # Rebuild: ftyp -> moov -> mdat
    fd = new_ftyp if (needs_rewrite and new_ftyp) else (data[ftyp["offset"]:ftyp["end"]] if ftyp else b"")
    md = data[moov["offset"]:moov["end"]]
    mm = data[mdat["offset"]:mdat["end"]]

    out = bytearray(len(fd) + len(md) + len(mm))
    wp = 0
    out[wp:wp+len(fd)] = fd; wp += len(fd)
    new_moov_off = wp
    out[wp:wp+len(md)] = md; wp += len(md)
    new_mdat_off = wp
    out[wp:] = mm

    # Fix chunk offsets since mdat moved
    off_delta = new_mdat_off - mdat["offset"]
    if off_delta != 0:
        fix_offsets_recursive(out, new_moov_off, new_moov_off + len(md), new_moov_off, off_delta)

    return out, True


# ════════════════════════════════════════════════════════════════════════
# ELST PATCHING (main function)
# ════════════════════════════════════════════════════════════════════════

def patch_mp4(input_path: str, output_path: str = None) -> bool:
    """
    Patch an MP4 file for TikTok lossless passthrough.

    Applies:
      1. Maska ELST corruption method
      2. iTunes metadata injection
      3. Container normalization

    Returns True if successful, False otherwise.
    """
    target = output_path or input_path

    # Read file
    try:
        with open(input_path, 'rb') as f:
            raw = f.read()
    except IOError as e:
        print(f"  couldn't read {input_path}: {e}")
        return False

    data = bytearray(raw)
    orig_size = len(data)

    print(f"  input: {input_path}")
    print(f"  size: {orig_size:,} bytes")

    # Step 1: Patch ALL ELST boxes
    print("  [1/3] patching elst...")
    elst_count = patch_all_elst(data)
    if elst_count == 0:
        print("       no elst found (file might be simple)")
    else:
        print(f"       patched {elst_count} elst box(es)")

    # Step 2: Add iTunes metadata
    print()
    print("  [2/3] adding itunes metadata...")
    data = add_itunes_metadata(data)

    # Step 3: Normalize container
    print()
    print("  [3/3] normalizing container...")
    data, norm_changed = normalize_container(data)
    if norm_changed:
        print("       reordered to ftyp->moov->mdat")
    else:
        print("       already normalized")

    # Write output
    try:
        with open(target, 'wb') as f:
            f.write(bytes(data))
    except IOError as e:
        print(f"  couldn't write to {target}: {e}")
        return False

    final_size = len(data)

    print(f"  done! ({orig_size:,} bytes in, {final_size:,} bytes out)")

    return True


# ════════════════════════════════════════════════════════════════════════
# FFMPEG ENCODING
# ════════════════════════════════════════════════════════════════════════

def encode_for_tiktok(input_path: str, output_path: str) -> bool:
    """
    Encode video with TikTok-optimized libx264 settings.
    
    Uses:
      - libx264, high profile, level 4.1
      - 3000kbps bitrate, 3500k maxrate, 7000k bufsize
      - yuv420p pixel format (required by TikTok)
      - AAC 256k audio (required by TikTok)
      - medium preset (good speed/size balance)
    """
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "libx264",
        "-preset", "medium",
        "-profile:v", "high",
        "-level", "4.1",
        "-b:v", "3000k",
        "-maxrate", "3500k",
        "-bufsize", "7000k",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "256k",
        "-y",
        output_path
    ]
    
    print(f"  cmd: ffmpeg -i \"{input_path}\" \\")
    print(f"       -c:v libx264 -preset medium -profile:v high -level 4.1 \\")
    print(f"       -b:v 3000k -maxrate 3500k -bufsize 7000k \\")
    print(f"       -pix_fmt yuv420p \\")
    print(f"       -c:a aac -b:a 256k \\")
    print(f"       -y \"{output_path}\"")
    print()
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        error_msg = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        print(f"  encode error:")
        print(error_msg)
        return False
    
    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  ✓ encoded ({size_mb:.1f} MB)")
        return True
    else:
        print(f"  encode failed: output not created")
        return False


# ════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("tiktok_lossless_patch.py")
        print()
        print("TikTok Optimization Pipeline")
        print()
        print("usage:")
        print(f"  python3 {sys.argv[0]} input_file [output.mp4]")
        print()
        print("accepts ANY video format (mp4, mov, avi, mkv, prores, etc.)")
        print()
        print("for BEST RESULTS use:")
        print("  - lossless intermediate (ProRes, UT Video, FFV1, etc.)")
        print("  - topaz-enhanced output")
        print("  - source footage with minimal compression artifacts")
        print()
        print("pipeline:")
        print("  1. encode with libx264 (yuv420p, high profile)")
        print("  2. patch ELST boxes for TikTok passthrough")
        print()
        print("credits:")
        print("  - MASKA's OSS browser extension for the ELST technique")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    # Validate input
    if not os.path.exists(input_file):
        print(f"error: '{input_file}' not found")
        sys.exit(1)
    
    # Generate output filename if not provided
    if not output_file:
        stem = Path(input_file).stem
        output_file = f"{stem}_tiktok.mp4"
    
    # Print header
    print()
    print(f"          tiktok optimization pipeline")
    print(f"───────────────────────────────────────────────")
    print(f"                                 made by buwryy")
    print(f"SPECIAL THANKS TO:")
    print(f"                - Maska's OSS browser extension")
    print()
    print(f"  input:     {input_file}")
    print(f"  output:    {output_file}")
    print()
    
    # Show recommendation for non-lossless inputs
    lossless_exts = ('.mov', '.avi', '.mkv', '.ffv1', '.utvideo', '.huff', '.prores')
    is_likely_lossless = any(input_file.lower().endswith(ext) for ext in lossless_exts)
    
    if not is_likely_lossless:
        print(f"  tip: for best quality, feed this script lossless/topazed output")
        print()
    
    # Step 1: Encode (ALWAYS encode, even if input is .mp4)
    print(f"[Step 1/2] Encoding to H.264... (THIS MIGHT TAKE A WHILE -- PLEASE WAIT!!!)")
    print()
    
    if not encode_for_tiktok(input_file, output_file):
        print()
        print(f"  encoding failed :(")
        sys.exit(1)
    
    print()
    
    # Step 2: Patch
    print(f"[Step 2/2] Patching ELST boxes...")
    print()
    
    if patch_mp4(output_file):
        print()
        print(f"  done! ready for TikTok: {output_file}")
        return 0
    else:
        print()
        print(f"  elst patching failed (file is still usable, might get re-encoded)")
        return 1


if __name__ == '__main__':
    sys.exit(main())
