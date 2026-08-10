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

Also includes NoBlur-style sample table inflation (10x) which makes TikTok
see way more samples than actually exist, further reducing recompression.

^ P.S. this is speculation.

Usage:
    python3 tiktok_lossless_patch.py input.mp4 [output.mp4]

If no output is given, it overwrites the original file.

Credits:
    MASKA's OSS browser extension for the ELST technique
    irgifebry's NoBlur OSS client-side processor for the sample table inflation technique

For legal inquiries, contact @buwryy on Discord.

LICENSE: MIT License, copyright holder: buwryme @ GitHub
"""

import sys
import struct


# The magic value we write into the ELST box
# 0x10000001 = 268,435,457 entries... yeah right, TikTok
ELST_MAGIC = 0x10000001

# NoBlur inflation settings
INFLATE_MULT = 10  # 10x sample density

# Codec-aware dummy sample sizes (how big fake samples should be)
DUMMY_SIZES = {
    b"avc1": 8, b"avc3": 8,    # H.264
    b"hvc1": 16, b"hev1": 16,   # H.265/HEVC
    b"vp09": 4,                # VP9
    b"av01": 4,                # AV1
    b"mp4v": 8,                # MPEG-4 Visual
}
DEFAULT_DUMMY = 8


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


# MASKA's ELST inflation & iTunes metadata injection

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
        print("  udta already there, skipping metadata")
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

    print(f"  injected itunes metadata ({len(udta_atom)} bytes)")
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
            print(f"  elst #{count+1} @ {pos}: {old:#010x} -> {ELST_MAGIC:#010x} ({entries} entries)")
            count += 1

        search = pos + 4

    return count


# NoBlur's sample table inflation

def find_video_stbl(moov: dict, data: bytearray) -> dict | None:
    """Find video track's sample table (stbl)."""
    for trak in parse_boxes(data, moov["offset"] + 8, moov["end"]):
        if trak["type"] != b"trak":
            continue

        for mdia in parse_boxes(data, trak["offset"] + 8, trak["end"]):
            if mdia["type"] != b"mdia":
                continue

            # Check for 'vide' handler type
            has_video = False
            for hdlr in parse_boxes(data, mdia["offset"] + 8, mdia["end"]):
                if hdlr["type"] == b"hdlr":
                    for i in range(hdlr["offset"] + 8, min(hdlr["end"], hdlr["offset"] + 100) - 3):
                        if data[i:i+4] == b"vide":
                            has_video = True
                            break
                    break

            if not has_video:
                continue

            # Found video track, look for stbl
            for minf in parse_boxes(data, mdia["offset"] + 8, mdia["end"]):
                if minf["type"] != b"minf":
                    continue
                for stbl in parse_boxes(data, minf["offset"] + 8, minf["end"]):
                    if stbl["type"] == b"stbl":
                        return {"trak": trak, "mdia": mdia, "minf": minf, "stbl": stbl}

    return None


def detect_codec(stbl: dict, data: bytearray) -> bytes:
    """Detect video codec FourCC from stsd inside stbl."""
    for child in parse_boxes(data, stbl["offset"] + 8, stbl["end"]):
        if child["type"] == b"stsd":
            cs = child["offset"] + 16  # After header + ver/flags + entry_count
            if cs + 16 <= child["end"]:
                return bytes(data[cs:cs+4])
    return b"unk"


def build_inflated_stts(real_count: int, sample_delta: int, mult: int) -> bytearray:
    """Build inflated stts (time-to-sample) atom."""
    fake_count = real_count * (mult - 1)
    # [size][stts][ver=0][count=2][real_count][delta][fake_count][delta]
    atom_size = 16 + 16
    a = bytearray(atom_size)
    write_u32be(a, 0, atom_size)
    a[4:8] = b'stts'
    write_u32be(a, 8, 0)           # version/flags
    write_u32be(a, 12, 2)          # entry_count = 2
    write_u32be(a, 16, real_count) # entry 1: sample_count
    write_u32be(a, 20, sample_delta)  # entry 1: delta
    write_u32be(a, 24, fake_count) # entry 2: sample_count (fake)
    write_u32be(a, 28, sample_delta)  # entry 2: delta
    return a


def build_inflated_stsz(data: bytearray, stsz: dict, real_count: int, mult: int, dummy: int) -> bytearray:
    """Build inflated stsz (sample sizes) atom."""
    total_count = real_count * mult
    atom_size = 20 + total_count * 4
    a = bytearray(atom_size)
    write_u32be(a, 0, atom_size)
    a[4:8] = b'stzs'              # NOTE: correct FourCC is 'stsz'!
    write_u32be(a, 8, 0)          # version/flags
    write_u32be(a, 12, 0)         # default_size (0 = variable)
    write_u32be(a, 16, total_count)  # sample_count

    base = stsz["offset"] + 20   # Start of size entries
    for i in range(real_count):
        write_u32be(a, 20 + i*4, read_u32be(data, base + i*4))
    for i in range(real_count, total_count):
        write_u32be(a, 20 + i*4, dummy)  # Fake samples get dummy size
    return a


def build_inflated_stco(data: bytearray, stco: dict, orig_count: int, real_count: int,
                        safe_offset: int, delta: int, mult: int) -> bytearray:
    """Build inflated stco (chunk offsets, 32-bit) atom."""
    fake_count = real_count * (mult - 1)
    new_count = orig_count + fake_count
    atom_size = 16 + new_count * 4
    a = bytearray(atom_size)
    write_u32be(a, 0, atom_size)
    a[4:8] = b'stco'
    write_u32be(a, 8, 0)
    write_u32be(a, 12, new_count)

    base = stco["offset"] + 16
    for i in range(orig_count):
        write_u32be(a, 16 + i*4, read_u32be(data, base + i*4) + delta)
    for i in range(fake_count):
        write_u32be(a, 16 + (orig_count+i)*4, safe_offset)  # Point to EOF padding
    return a


def build_inflated_co64(data: bytearray, co64: dict, orig_count: int, real_count: int,
                         safe_offset: int, delta: int, mult: int) -> bytearray:
    """Build inflated co64 (chunk offsets, 64-bit) atom."""
    fake_count = real_count * (mult - 1)
    new_count = orig_count + fake_count
    atom_size = 16 + new_count * 8
    a = bytearray(atom_size)
    write_u32be(a, 0, atom_size)
    a[4:8] = b'co64'
    write_u32be(a, 8, 0)
    write_u32be(a, 12, new_count)

    base = co64["offset"] + 16
    for i in range(orig_count):
        hi = read_u32be(data, base + i*8)
        lo = read_u32be(data, base + i*8 + 4)
        val = (hi * 0x100000000 + lo) + delta
        write_u32be(a, 16 + i*8, (val >> 32) & 0xFFFFFFFF)
        write_u32be(a, 16 + i*8 + 4, val & 0xFFFFFFFF)
    for i in range(fake_count):
        write_u32be(a, 16 + (orig_count+i)*8, 0)       # hi = 0
        write_u32be(a, 16 + (orig_count+i)*8 + 4, safe_offset)  # lo
    return a


def build_patched_stsc(data: bytearray, stsc: dict, orig_chunk_count: int) -> bytearray:
    """Build patched stsc (sample-to-chunk) with extra entry for fake samples."""
    orig_entries = read_u32be(data, stsc["offset"] + 12)
    new_entries = orig_entries + 1
    atom_size = 16 + new_entries * 12
    a = bytearray(atom_size)
    write_u32be(a, 0, atom_size)
    a[4:8] = b'stsc'
    write_u32be(a, 8, 0)
    write_u32be(a, 12, new_entries)

    base = stsc["offset"] + 16
    for i in range(orig_entries):
        write_u32be(a, 16 + i*12,     read_u32be(data, base + i*12))      # first_chunk
        write_u32be(a, 16 + i*12 + 4, read_u32be(data, base + i*12 + 4))  # samples_per_chunk
        write_u32be(a, 16 + i*12 + 8, read_u32be(data, base + i*12 + 8))  # sample_desc_index

    # Extra entry: fake samples start after real ones, 1 per chunk
    write_u32be(a, 16 + orig_entries*12, orig_chunk_count + 1)
    write_u32be(a, 16 + orig_entries*12 + 4, 1)
    write_u32be(a, 16 + orig_entries*12 + 8, 1)
    return a


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
            located = find_video_stbl(moov, data)
            is_hevc = False
            if located:
                c = detect_codec(located["stbl"], data)
                is_hevc = c in (b"hvc1", b"hev1")

            if is_hevc:
                new_ftyp = bytearray([0,0,0,32, 0x66,0x74,0x79,0x70,
                    0x69,0x73,0x6f,0x34, 0,0,2,0,
                    0x69,0x73,0x6f,0x6d, 0x69,0x73,0x6f,0x32,
                    0x68,0x76,0x63,0x31, 0x6d,0x70,0x34,0x31])
            else:
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


def try_inflate_sample_table(data: bytearray, mult: int = INFLATE_MULT) -> tuple[bytearray, list[str]]:
    """
    Try to apply NoBlur's sample table inflation.

    Inflates whatever sample table boxes we find. Doesn't fail if some are missing -
    just does what it can and returns warnings about what couldn't be done.

    Returns (modified_data, list_of_warnings).
    """
    warnings = []

    if mult < 2:
        return data, ["inflation multiplier must be >= 2"]

    fsize = len(data)
    top = parse_boxes(data, 0, fsize)

    # Find moov
    moov = None
    for b in top:
        if b["type"] == b"moov":
            moov = b; break
    if not moov:
        return data, ["no moov box found"]

    # Find video track's stbl
    located = find_video_stbl(moov, data)
    if not located:
        return data, ["no video track found"]

    stbl = located["stbl"]
    kids = parse_boxes(data, stbl["offset"] + 8, stbl["end"])

    # Find what we have
    stts = stsz = stco = co64 = stsc = None
    for k in kids:
        t = k["type"]
        if t == b'stts': stts = k
        elif t == b'stzs': stsz = k      # NOTE: correct FourCC!
        elif t == b'stco': stco = k
        elif t == b'co64': co64 = k
        elif t == b'stsc': stsc = k

    # Check required boxes
    if not stts:
        return data, ["missing stts (time-to-sample) box"]

    # Count real samples from stts
    entries = read_u32be(data, stts["offset"] + 12)
    real_count = 0
    total_dur = 0
    base = stts["offset"] + 16
    for i in range(entries):
        c = read_u32be(data, base + i*8)
        d = read_u32be(data, base + i*8 + 4)
        real_count += c
        total_dur += c * d

    if real_count == 0:
        return data, ["no video samples found in stts"]

    sample_delta = round(total_dur / real_count)
    codec = detect_codec(stbl, data)
    dummy = DUMMY_SIZES.get(codec, DEFAULT_DUMMY)

    print(f"  inflating {real_count} samples x{mult} (codec={codec.decode('ascii','replace')}, dummy={dummy}B)")

    # Get chunk info
    chunk_box = stco or co64
    if not chunk_box:
        return data, ["neither stco nor co64 found"]

    orig_chunks = read_u32be(data, chunk_box["offset"] + 12)

    # Build replacement atoms for what we HAVE
    replacements = []  # (offset, new_bytes, old_size)

    # Always need stts
    new_stts = build_inflated_stts(real_count, sample_delta, mult)
    replacements.append((stts["offset"], new_stts, stts["size"]))

    # stsz if present
    if stsz:
        new_stsz = build_inflated_stsz(data, stsz, real_count, mult, dummy)
        replacements.append((stsz["offset"], new_stsz, stsz["size"]))
    else:
        warnings.append("no stsz box - skipping sample size inflation")

    # stsc if present
    if stsc:
        new_stsc = build_patched_stsc(data, stsc, orig_chunks)
        replacements.append((stsc["offset"], new_stsc, stsc["size"]))
    else:
        warnings.append("no stsc box - skipping sample-to-chunk patching")

    # Calculate size changes so far
    partial_delta = sum(len(r[1]) - r[2] for r in replacements)

    # Chunk offset box (stco or co64)
    fake_count = real_count * (mult - 1)
    safe_offset = fsize + partial_delta + (fake_count * dummy if stsz else 0)

    # Account for chunk box growth too
    chunk_entry_size = 4 if stco else 8
    chunk_delta = fake_count * chunk_entry_size
    safe_offset += chunk_delta
    total_moov_delta = partial_delta + chunk_delta

    if stco:
        new_chunk = build_inflated_stco(data, stco, orig_chunks, real_count,
                                        safe_offset, total_moov_delta, mult)
        replacements.append((stco["offset"], new_chunk, stco["size"]))
    elif co64:
        new_chunk = build_inflated_co64(data, co64, orig_chunks, real_count,
                                         safe_offset, total_moov_delta, mult)
        replacements.append((co64["offset"], new_chunk, co64["size"]))

    # Sort by offset for sequential processing
    replacements.sort(key=lambda x: x[0])

    # Calculate padding needed
    pad_size = fake_count * dummy if stsz else 0
    new_fsize = fsize + total_moov_delta + pad_size

    # Build new file
    out = bytearray(new_fsize)

    rp = wp = 0
    for rep_off, rep_bytes, old_sz in replacements:
        # Copy up to this point
        cl = rep_off - rp
        out[wp:wp+cl] = data[rp:rp+cl]
        wp += cl

        # Insert new atom
        out[wp:wp+len(rep_bytes)] = rep_bytes
        wp += len(rep_bytes)

        rp = rep_off + old_sz  # Skip past original

    # Copy rest of file
    out[wp:] = data[rp:]

    # Update parent box sizes
    for poff in [stbl["offset"], located["minf"]["offset"],
                 located["mdia"]["offset"], located["trak"]["offset"], moov["offset"]]:
        old_sz = read_u32be(out, poff)
        write_u32be(out, poff, old_sz + total_moov_delta)

    # If moov was before mdat, fix other tracks' offsets too
    mdat = None
    for b in top:
        if b["type"] == b"mdat":
            mdat = b; break

    if mdat and moov["offset"] < mdat["offset"]:
        updated_moov_sz = read_u32be(out, moov["offset"])
        moov_end = moov["offset"] + updated_moov_sz

        for trak in parse_boxes(out, moov["offset"] + 8, moov_end):
            if trak["type"] != b"trak" or trak["offset"] == located["trak"]["offset"]:
                continue

            # Find stbl in other tracks
            for tc in parse_boxes(out, trak["offset"] + 8, trak["end"]):
                if tc["type"] != b"mdia":
                    continue
                for tcc in parse_boxes(out, tc["offset"] + 8, tc["end"]):
                    if tcc["type"] != b"minf":
                        continue
                    for tccc in parse_boxes(out, tcc["offset"] + 8, tcc["end"]):
                        if tccc["type"] != b"stbl" or tccc["offset"] == stbl["offset"]:
                            continue

                        for sc in parse_boxes(out, tccc["offset"] + 8, tccc["end"]):
                            if sc["type"] == b"stco":
                                cnt = read_u32be(out, sc["offset"] + 12)
                                for j in range(cnt):
                                    v = read_u32be(out, sc["offset"] + 16 + j*4)
                                    write_u32be(out, sc["offset"] + 16 + j*4, v + total_moov_delta)
                            elif sc["type"] == b"co64":
                                cnt = read_u32be(out, sc["offset"] + 12)
                                for j in range(cnt):
                                    hi = read_u32be(out, sc["offset"] + 16 + j*8)
                                    lo = read_u32be(out, sc["offset"] + 17 + j*8)
                                    v = (hi * 0x100000000 + lo) + total_moov_delta
                                    write_u32be(out, sc["offset"] + 16 + j*8, (v >> 32) & 0xFFFFFFFF)
                                    write_u32be(out, sc["offset"] + 17 + j*8, v & 0xFFFFFFFF)

    return out, warnings


# ════════════════════════════════════════════════════════════════════════
# MAIN PATCH FUNCTION
# ════════════════════════════════════════════════════════════════════════

def patch_mp4(input_path: str, output_path: str = None) -> bool:
    """
    Patch an MP4 file for TikTok lossless passthrough.

    Applies:
      1. Maska ELST corruption method
      2. iTunes metadata injection
      3. Container normalization (optional)
      4. NoBlur sample table inflation (optional)

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
    print()

    # Step 1: Patch ALL ELST boxes
    print("  [1/4] patching elst...")
    elst_count = patch_all_elst(data)
    if elst_count == 0:
        print("       no elst found (file might be simple)")
    else:
        print(f"       patched {elst_count} elst box(es)")

    # Step 2: Add iTunes metadata
    print()
    print("  [2/4] adding itunes metadata...")
    data = add_itunes_metadata(data)

    # Step 3: Normalize container
    print()
    print("  [3/4] normalizing container...")
    data, norm_changed = normalize_container(data)
    if norm_changed:
        print("       reordered to ftyp->moov->mdat")
    else:
        print("       already normalized")

    # Step 4: Inflate sample tables
    print()
    print("  [4/4] inflating sample tables...")
    data, inflate_warnings = try_inflate_sample_table(data, INFLATE_MULT)

    # Write output
    try:
        with open(target, 'wb') as f:
            f.write(bytes(data))
    except IOError as e:
        print(f"  couldn't write to {target}: {e}")
        return False

    final_size = len(data)

    print()
    print(f"  done! ({orig_size:,} bytes in, {final_size:,} bytes out)")

    # Show any warnings
    if inflate_warnings:
        print()
        print("  warnings:")
        for w in inflate_warnings:
            print(f"    - {w}")

    return True


# ════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("tiktok_lossless_patch.py")
        print()
        print("usage:")
        print(f"  python3 {sys.argv[0]} input.mp4")
        print(f"  python3 {sys.argv[0]} input.mp4 output.mp4")
        print()
        print("patches mp4 files so tiktok doesn't re-encode them.")
        print("combines maska elst method + noblur sample inflation.")
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
