#!/usr/bin/env python3
"""
tiktok_lossless_patch.py
TikTok Optimization Pipeline:
  1. Encode with libx265/HEVC Main 10 (CPU) - supports up to 4K60fps
  2. Patch sample tables + strip timecode track for TikTok passthrough

How it works: TikTok's ingest pipeline checks the MP4 sample table
consistency. By inflating the audio stsz (sample size table) by 10x
while leaving stts (time-to-sample) at its original count, we create
a deliberate mismatch. Strict transcoders choke on it and fall back
to passthrough. Lenient players (TikTok's mobile decoder) ignore the
mismatch and play the actual frames fine.

Additionally we strip the timecode (tmcd) track that ffmpeg adds by
default, normalize handler names, and reorder moov before mdat.

Supports up to 4K resolution at 60fps.

^ P.S. this is speculation based on clean-room reverse engineering.

Usage:
  python3 tiktok_lossless_patch.py input_file [output.mp4]

Accepts ANY input format (mp4, mov, avi, mkv, prores, etc.)
For best results, use lossless or topaz-enhanced output as input.
If no output is given, auto-generates filename.

Credits:
  Clean-room reverse engineering of a known posting method.
  For legal inquiries, contact @buwryy on Discord.

LICENSE: MIT License, copyright holder: buwryme @ GitHub
"""

import sys
import struct
import subprocess
import os
import time
from pathlib import Path

# ════════════════════════════════════════════════════════════════════════
# CONFIGURABLE METADATA
# ════════════════════════════════════════════════════════════════════════

META_ARTIST   = "buwryy"
META_COMPOSER = "buwryy"
META_ALBUM    = "buwryy Posting Method"
META_ENCODER  = "Lavf60.16.100"  # keep this exactly as it is
META_COMMENT  = "patched by buwryy"
META_COPYRIGHT = "buwryy"
META_GROUPING = "buwryy"

# how many times to inflate the audio stsz
STSZ_INFLATE_FACTOR = 10

# trailing garbage size (bytes) - confuses certain validators
TRAILING_GARBAGE_SIZE = 16416

# ════════════════════════════════════════════════════════════════════════
# WRAPPER FUNCTIONS FOR UI INTEGRATION (APP.PY)
# ════════════════════════════════════════════════════════════════════════

def get_default_config() -> dict:
    """Return default configuration parameters for GUI."""
    return {
        "artist": META_ARTIST,
        "composer": META_COMPOSER,
        "album": META_ALBUM,
        "encoder": META_ENCODER,
        "comment": META_COMMENT,
        "copyright": META_COPYRIGHT,
        "grouping": META_GROUPING,
        "inflation_rate": STSZ_INFLATE_FACTOR,
        "trailing_bytes": TRAILING_GARBAGE_SIZE,
        "re_encode": True
    }

def patch_video(input_path: str, config: dict = None) -> bool:
    """
    Main entry point for app.py to patch a video file.
    Applies config options (if given) and runs encoding/patching pipeline.
    """
    global META_ARTIST, META_COMPOSER, META_ALBUM, META_ENCODER
    global META_COMMENT, META_COPYRIGHT, META_GROUPING
    global STSZ_INFLATE_FACTOR, TRAILING_GARBAGE_SIZE

    if config:
        META_ARTIST = config.get("artist", META_ARTIST)
        META_COMPOSER = config.get("composer", META_COMPOSER)
        META_ALBUM = config.get("album", META_ALBUM)
        META_ENCODER = config.get("encoder", META_ENCODER)
        META_COMMENT = config.get("comment", META_COMMENT)
        META_COPYRIGHT = config.get("copyright", META_COPYRIGHT)
        META_GROUPING = config.get("grouping", META_GROUPING)
        STSZ_INFLATE_FACTOR = config.get("inflation_rate", STSZ_INFLATE_FACTOR)
        TRAILING_GARBAGE_SIZE = config.get("trailing_bytes", TRAILING_GARBAGE_SIZE)
        re_encode = config.get("re_encode", True)
    else:
        re_encode = True

    p = Path(input_path)
    output_path = str(p.parent / f"{p.stem}_tiktok.mp4")

    # Step 1: Encode video if enabled
    if re_encode:
        if not encode_for_tiktok(input_path, output_path):
            raise RuntimeError("Encoding failed during execution")
        target_file = output_path
    else:
        target_file = input_path

    # Step 2: Patch video structure
    if not patch_mp4(target_file):
        raise RuntimeError("Patching MP4 structure failed")

    return True

# ════════════════════════════════════════════════════════════════════════
# MP4 PARSING UTILITIES
# ════════════════════════════════════════════════════════════════════════

def read_u32be(data: bytes | bytearray, offset: int) -> int:
    """Read a big-endian 32-bit unsigned integer."""
    return struct.unpack('>I', data[offset:offset+4])[0]

def write_u32be(data: bytearray, offset: int, value: int):
    """Write a big-endian 32-bit unsigned integer."""
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)

def read_u16be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>H', data[offset:offset+2])[0]

def write_u16be(data: bytearray, offset: int, value: int):
    struct.pack_into('>H', data, offset, value & 0xFFFF)

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

def find_box(data: bytearray, fourcc: bytes, start: int = 0, end: int = None) -> dict | None:
    """Find first occurrence of a box type in range."""
    if end is None:
        end = len(data)
    for box in parse_boxes(data, start, end):
        if box["type"] == fourcc:
            return box
    return None

def find_all_boxes(data: bytearray, fourcc: bytes, start: int = 0, end: int = None) -> list[dict]:
    """Find all occurrences of a box type in range."""
    if end is None:
        end = len(data)
    return [b for b in parse_boxes(data, start, end) if b["type"] == fourcc]

# ════════════════════════════════════════════════════════════════════════
# BOX BUILDING UTILITIES
# ════════════════════════════════════════════════════════════════════════

def build_box(box_type: bytes, payload: bytes) -> bytes:
    """Build a complete MP4 box from type and payload."""
    size = 8 + len(payload)
    return struct.pack('>I', size) + box_type + payload

def build_fullbox(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    """Build a FullBox (with version and flags)."""
    ver_flags = struct.pack('>I', (version << 24) | (flags & 0x00FFFFFF))
    return build_box(box_type, ver_flags + payload)

def build_hdlr(handler_type: bytes, name: str) -> bytes:
    """Build an hdlr box."""
    # pre_defined(4) + handler_type(4) + reserved(12) + name + null
    payload = b'\x00' * 4  # pre_defined
    payload += handler_type
    payload += b'\x00' * 12  # reserved
    payload += name.encode('utf-8') + b'\x00'
    return build_fullbox(b'hdlr', 0, 0, payload)

def build_data_atom(value: str) -> bytes:
    """Build a 'data' sub-atom for ilst entries."""
    # type_flag=1 (UTF-8), locale=0
    payload = struct.pack('>II', 1, 0) + value.encode('utf-8')
    return build_box(b'data', payload)

def build_ilst_entry(tag: bytes, value: str) -> bytes:
    """Build a single ilst metadata entry like ©ART, ©alb, etc."""
    return build_box(tag, build_data_atom(value))

def build_udta(artist: str, composer: str, album: str,
               encoder: str, comment: str, copyright: str, grouping: str) -> bytes:
    """Build complete udta > meta > ilst structure."""
    # ilst contents
    ilst_payload = b''
    ilst_payload += build_ilst_entry('©ART'.encode('latin-1'), artist)
    ilst_payload += build_ilst_entry('©wrt'.encode('latin-1'), composer)
    ilst_payload += build_ilst_entry('©alb'.encode('latin-1'), album)
    ilst_payload += build_ilst_entry('©too'.encode('latin-1'), encoder)
    ilst_payload += build_ilst_entry('©cmt'.encode('latin-1'), comment)
    ilst_payload += build_ilst_entry(b'cprt', copyright)
    ilst_payload += build_ilst_entry('©grp'.encode('latin-1'), grouping)

    ilst_box = build_box(b'ilst', ilst_payload)

    # hdlr for meta (mdir)
    meta_hdlr_payload = b'\x00' * 4  # pre_defined
    meta_hdlr_payload += b'mdir'
    meta_hdlr_payload += b'appl'
    meta_hdlr_payload += b'\x00' * 8  # reserved
    meta_hdlr_payload += b'\x00'  # name (empty)
    meta_hdlr_box = build_fullbox(b'hdlr', 0, 0, meta_hdlr_payload)

    # meta is a FullBox with version=0, flags=0
    meta_payload = struct.pack('>I', 0) + meta_hdlr_box + ilst_box
    meta_box = build_box(b'meta', meta_payload)

    # udta wraps meta
    udta_box = build_box(b'udta', meta_box)

    return udta_box

def build_trailing_garbage() -> bytes:
    """Build the trailing malformed data that confuses strict validators."""
    # 4-byte size field (value=4), 4 bytes of null type, then padding
    header = struct.pack('>I', 4) + b'\x00' * 4
    padding_size = TRAILING_GARBAGE_SIZE - len(header)
    padding = b'\x00' * padding_size
    return header + padding

# ════════════════════════════════════════════════════════════════════════
# MAIN PATCHING LOGIC
# ════════════════════════════════════════════════════════════════════════

def patch_mp4(input_path: str, output_path: str = None) -> bool:
    """
    Patch an MP4 file for TikTok lossless passthrough.
    Applies:
      1. Strip free box, reorder moov before mdat
      2. Remove tmcd track + tref
      3. Normalize handler names (VideoHandler / SoundHandler)
      4. Remove audio elst, inflate audio stsz
      5. Replace udta metadata
      6. Append trailing garbage
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

    # ── Step 1: Parse top-level structure ──────────────────────────────
    print("  [1/6] parsing box structure...")
    top_boxes = parse_boxes(data, 0, len(data))

    ftyp_box = None
    moov_box = None
    mdat_box = None
    free_boxes = []

    for box in top_boxes:
        if box["type"] == b'ftyp':
            ftyp_box = box
        elif box["type"] == b'moov':
            moov_box = box
        elif box["type"] == b'mdat':
            mdat_box = box
        elif box["type"] == b'free':
            free_boxes.append(box)

    if not moov_box or not mdat_box:
        print("       ERROR: missing moov or mdat, aborting")
        return False

    print(f"       ftyp @ {ftyp_box['offset'] if ftyp_box else 'N/A'}")
    print(f"       moov @ {moov_box['offset']} ({moov_box['size']} bytes)")
    print(f"       mdat @ {mdat_box['offset']} ({mdat_box['size']} bytes)")
    if free_boxes:
        print(f"       free boxes: {len(free_boxes)} (will strip)")

    # ── Step 2: Reconstruct as ftyp + moov + mdat (faststart) ─────────
    print("  [2/6] reconstructing layout (ftyp → moov → mdat)...")

    ftyp_data = data[ftyp_box["offset"]:ftyp_box["end"]] if ftyp_box else b''
    moov_data = bytearray(data[moov_box["offset"]:moov_box["end"]])

    mdat_header_size = 16 if read_u32be(data, mdat_box["offset"]) == 1 else 8
    mdat_payload = data[mdat_box["offset"] + mdat_header_size:mdat_box["end"]]
    if len(mdat_payload) >= 4:
        mdat_payload = mdat_payload[:-4]
    mdat_data = build_box(b'mdat', mdat_payload)

    # ── Step 3: Patch moov contents ───────────────────────────────────
    print("  [3/6] patching moov...")

    moov_inner_start = 8  # skip moov box header
    moov_inner_end = len(moov_data)
    moov_children = parse_boxes(moov_data, moov_inner_start, moov_inner_end)

    # Identify tracks
    video_trak_idx = None
    audio_trak_idx = None
    tmcd_trak_idx = None
    trak_indices = []

    for i, child in enumerate(moov_children):
        if child["type"] == b'trak':
            trak_indices.append(i)
            # Look inside trak for mdia > hdlr to identify type
            trak_start = child["offset"] + 8
            trak_end = child["end"]
            trak_children = parse_boxes(moov_data, trak_start, trak_end)

            for tc in trak_children:
                if tc["type"] == b'mdia':
                    mdia_start = tc["offset"] + 8
                    mdia_end = tc["end"]
                    mdia_children = parse_boxes(moov_data, mdia_start, mdia_end)
                    for mc in mdia_children:
                        if mc["type"] == b'hdlr':
                            hdlr_start = mc["offset"] + 8 + 4  # skip box header + fullbox header
                            handler_type = moov_data[hdlr_start + 4:hdlr_start + 8]
                            if handler_type == b'vide':
                                video_trak_idx = i
                            elif handler_type == b'soun':
                                audio_trak_idx = i
                            elif handler_type == b'tmcd':
                                tmcd_trak_idx = i

    print(f"       video trak: index {video_trak_idx}")
    print(f"       audio trak: index {audio_trak_idx}")
    print(f"       tmcd trak:  index {tmcd_trak_idx}")

    # Now rebuild moov by modifying each trak
    new_moov_children = []
    creation_time = int(time.time()) + 2082844800  # MP4 epoch offset

    for i, child in enumerate(moov_children):
        if child["type"] == b'mvhd':
            # Patch creation/modification time in mvhd
            mvhd = bytearray(moov_data[child["offset"]:child["end"]])
            # version 0: offset 8(box)+4(ver/flags) = 12 for creation_time
            if len(mvhd) > 20:
                write_u32be(mvhd, 12, creation_time)
                write_u32be(mvhd, 16, creation_time)
            new_moov_children.append(bytes(mvhd))

        elif child["type"] == b'trak':
            trak_data = bytearray(moov_data[child["offset"]:child["end"]])

            if i == tmcd_trak_idx:
                # Skip tmcd track entirely
                print("       stripped tmcd track")
                continue

            # Patch trak contents
            trak_data = patch_trak(trak_data, i == video_trak_idx, i == audio_trak_idx, creation_time)
            new_moov_children.append(bytes(trak_data))

        elif child["type"] == b'udta':
            # Skip existing udta - we'll replace it
            print("       replacing existing udta")
            continue

        else:
            new_moov_children.append(bytes(moov_data[child["offset"]:child["end"]]))

    # Build new udta
    new_udta = build_udta(META_ARTIST, META_COMPOSER, META_ALBUM,
                          META_ENCODER, META_COMMENT, META_COPYRIGHT, META_GROUPING)
    new_moov_children.append(new_udta)
    print(f"       injected metadata ({len(new_udta)} bytes)")

    # Reassemble moov
    moov_payload = b''.join(new_moov_children)
    new_moov = build_box(b'moov', moov_payload)

    # ── Step 4: Assemble final file ───────────────────────────────────
    print("  [4/6] assembling output...")

    output_data = bytearray()
    output_data += ftyp_data
    output_data += new_moov
    output_data += mdat_data

    # ── Step 5: Fix chunk offsets ─────────────────────────────────────
    print("  [5/6] fixing chunk offsets...")

    # mdat now starts after ftyp + moov
    new_mdat_offset = len(ftyp_data) + len(new_moov) + 8  # +8 for mdat header
    old_mdat_payload_offset = mdat_box["offset"] + mdat_header_size

    offset_delta = new_mdat_offset - old_mdat_payload_offset
    if offset_delta != 0:
        fix_chunk_offsets(output_data, offset_delta)
        print(f"       shifted offsets by {offset_delta:+d}")

    # ── Step 6: Append trailing garbage ───────────────────────────────
    print("  [6/6] appending trailing data...")
    garbage = build_trailing_garbage()
    output_data += garbage
    print(f"       appended {len(garbage)} bytes")

    # Write output
    try:
        with open(target, 'wb') as f:
            f.write(bytes(output_data))
    except IOError as e:
        print(f"  couldn't write to {target}: {e}")
        return False

    final_size = len(output_data)
    print(f"  done! ({orig_size:,} bytes in, {final_size:,} bytes out)")
    return True


def patch_trak(trak_data: bytearray, is_video: bool, is_audio: bool, creation_time: int) -> bytearray:
    """Patch a single trak box."""
    trak_inner_start = 8
    trak_inner_end = len(trak_data)
    trak_children = parse_boxes(trak_data, trak_inner_start, trak_inner_end)

    new_trak_children = []

    for tc in trak_children:
        if tc["type"] == b'tkhd':
            # Patch creation/modification time
            tkhd = bytearray(trak_data[tc["offset"]:tc["end"]])
            if len(tkhd) > 20:
                write_u32be(tkhd, 12, creation_time)
                write_u32be(tkhd, 16, creation_time)
            new_trak_children.append(bytes(tkhd))

        elif tc["type"] == b'tref':
            # Remove track reference box
            print("       stripped tref")
            continue

        elif tc["type"] == b'edts':
            if is_audio:
                # Remove elst from audio track
                print("       stripped audio elst")
                continue
            else:
                # Keep elst for video
                new_trak_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))

        elif tc["type"] == b'mdia':
            mdia_data = bytearray(trak_data[tc["offset"]:tc["end"]])
            mdia_data = patch_mdia(mdia_data, is_video, is_audio, creation_time)
            new_trak_children.append(bytes(mdia_data))

        else:
            new_trak_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))

    trak_payload = b''.join(new_trak_children)
    return bytearray(build_box(b'trak', trak_payload))


def patch_mdia(mdia_data: bytearray, is_video: bool, is_audio: bool, creation_time: int) -> bytearray:
    """Patch mdia box contents."""
    mdia_inner_start = 8
    mdia_inner_end = len(mdia_data)
    mdia_children = parse_boxes(mdia_data, mdia_inner_start, mdia_inner_end)

    new_mdia_children = []

    for mc in mdia_children:
        if mc["type"] == b'mdhd':
            # Patch creation/modification time
            mdhd = bytearray(mdia_data[mc["offset"]:mc["end"]])
            if len(mdhd) > 20:
                write_u32be(mdhd, 12, creation_time)
                write_u32be(mdhd, 16, creation_time)
            new_mdia_children.append(bytes(mdhd))

        elif mc["type"] == b'hdlr':
            # Replace handler name
            if is_video:
                new_hdlr = build_hdlr(b'vide', "VideoHandler")
            elif is_audio:
                new_hdlr = build_hdlr(b'soun', "SoundHandler")
            else:
                new_hdlr = build_hdlr(b'vide', "VideoHandler")
            new_mdia_children.append(new_hdlr)

        elif mc["type"] == b'minf':
            minf_data = bytearray(mdia_data[mc["offset"]:mc["end"]])
            minf_data = patch_minf(minf_data, is_audio)
            new_mdia_children.append(bytes(minf_data))

        else:
            new_mdia_children.append(bytes(mdia_data[mc["offset"]:mc["end"]]))

    mdia_payload = b''.join(new_mdia_children)
    return bytearray(build_box(b'mdia', mdia_payload))


def patch_minf(minf_data: bytearray, is_audio: bool) -> bytearray:
    """Patch minf box - remove nmhd if present, patch stbl for audio."""
    minf_inner_start = 8
    minf_inner_end = len(minf_data)
    minf_children = parse_boxes(minf_data, minf_inner_start, minf_inner_end)

    new_minf_children = []

    for mc in minf_children:
        if mc["type"] == b'nmhd':
            # Strip null media header (from tmcd track remnants)
            continue
        elif mc["type"] == b'stbl' and is_audio:
            stbl_data = bytearray(minf_data[mc["offset"]:mc["end"]])
            stbl_data = patch_stbl(stbl_data)
            new_minf_children.append(bytes(stbl_data))
        else:
            new_minf_children.append(bytes(minf_data[mc["offset"]:mc["end"]]))

    minf_payload = b''.join(new_minf_children)
    return bytearray(build_box(b'minf', minf_payload))


def patch_stbl(stbl_data: bytearray) -> bytearray:
    """
    Patch the sample table for audio track.

    Simple mobile-friendly strategy:
      - Keep real audio samples first and intact.
      - Inflate stsz by appending dummy tail samples.
      - Keep stts at real sample count.
      - Keep original stsc mapping for real samples.
      - Add one dummy stsc entry + one dummy stco offset for the tail.

    This preserves the stsz/stts mismatch that triggers passthrough,
    while leaving the real audio playable.
    """
    stbl_inner_start = 8
    stbl_inner_end = len(stbl_data)
    stbl_children = parse_boxes(stbl_data, stbl_inner_start, stbl_inner_end)

    stsz_box = None
    stts_box = None
    stsc_box = None
    stco_box = None

    for sc in stbl_children:
        if sc["type"] == b'stsz':
            stsz_box = sc
        elif sc["type"] == b'stts':
            stts_box = sc
        elif sc["type"] == b'stsc':
            stsc_box = sc
        elif sc["type"] == b'stco':
            stco_box = sc

    if not stsz_box or not stts_box or not stsc_box or not stco_box:
        return stbl_data

    # ── Read original stsz sizes ───────────────────────────────────────
    stsz_payload = stbl_data[stsz_box["offset"] + 8 + 4:stsz_box["end"]]

    if len(stsz_payload) < 8:
        return stbl_data

    uniform_size = read_u32be(stsz_payload, 0)
    sample_count = read_u32be(stsz_payload, 4)

    original_sample_sizes = []

    if uniform_size == 0:
        pos = 8
        for _ in range(sample_count):
            if pos + 4 > len(stsz_payload):
                break
            original_sample_sizes.append(read_u32be(stsz_payload, pos))
            pos += 4
    else:
        original_sample_sizes = [uniform_size] * sample_count

    if not original_sample_sizes:
        return stbl_data

    real_count = len(original_sample_sizes)
    extra_count = real_count * (STSZ_INFLATE_FACTOR - 1)

    # ── Read original stsc entries ─────────────────────────────────────
    stsc_payload = stbl_data[stsc_box["offset"] + 8 + 4:stsc_box["end"]]

    if len(stsc_payload) < 4:
        return stbl_data

    stsc_entry_count = read_u32be(stsc_payload, 0)
    stsc_entries = []
    pos = 4

    for _ in range(stsc_entry_count):
        if pos + 12 > len(stsc_payload):
            break

        first_chunk = read_u32be(stsc_payload, pos)
        samples_per_chunk = read_u32be(stsc_payload, pos + 4)
        sample_desc_idx = read_u32be(stsc_payload, pos + 8)

        stsc_entries.append((first_chunk, samples_per_chunk, sample_desc_idx))

        pos += 12

    last_sdi = stsc_entries[-1][2] if stsc_entries else 1

    # ── Read original stco offsets ─────────────────────────────────────
    stco_payload = stbl_data[stco_box["offset"] + 8 + 4:stco_box["end"]]

    if len(stco_payload) < 4:
        return stbl_data

    stco_entry_count = read_u32be(stco_payload, 0)
    stco_offsets = []
    pos = 4

    for _ in range(stco_entry_count):
        if pos + 4 > len(stco_payload):
            break

        stco_offsets.append(read_u32be(stco_payload, pos))
        pos += 4

    if not stco_offsets:
        return stbl_data

    # ── Sanity-check original stsc mapping ─────────────────────────────
    def stsc_total_samples(entries, chunk_count):
        total = 0

        for i, entry in enumerate(entries):
            first_chunk, samples_per_chunk, _ = entry

            if i + 1 < len(entries):
                next_first_chunk = entries[i + 1][0]
            else:
                next_first_chunk = chunk_count + 1

            if next_first_chunk <= first_chunk:
                continue

            total += (next_first_chunk - first_chunk) * samples_per_chunk

        return total

    original_chunk_count = len(stco_offsets)

    if not stsc_entries or stsc_total_samples(stsc_entries, original_chunk_count) != real_count:
        # Fallback: describe all real samples as one chunk.
        # This should rarely be needed for normal ffmpeg output.
        stsc_entries = [(1, real_count, last_sdi)]
        stco_offsets = [stco_offsets[0]]
        original_chunk_count = 1

    first_dummy_offset = stco_offsets[0]

    # ── Build new stsz ─────────────────────────────────────────────────
    #
    # IMPORTANT:
    # Real sample sizes come FIRST.
    # Dummy sizes are appended AFTER the real audio.
    #
    # This is what keeps mobile playback alive.
    #
    new_sizes = list(original_sample_sizes)

    for _ in range(STSZ_INFLATE_FACTOR - 1):
        new_sizes.extend(original_sample_sizes)

    new_count = len(new_sizes)

    new_stsz_payload = struct.pack('>II', 0, new_count)
    for sz in new_sizes:
        new_stsz_payload += struct.pack('>I', sz)

    new_stsz = build_fullbox(b'stsz', 0, 0, new_stsz_payload)

    print(f"       inflated stsz: {real_count} → {new_count} samples (real-first tail)")

    # ── Build new stts ─────────────────────────────────────────────────
    #
    # Keep real sample count.
    # For the known 228-frame case, use the reference pattern.
    # For other cases, preserve original stts.
    #
    if real_count == 228:
        stts_payload = struct.pack('>I', 2)
        stts_payload += struct.pack('>II', 227, 1024)
        stts_payload += struct.pack('>II', 1, 560)

        new_stts = build_fullbox(b'stts', 0, 0, stts_payload)

        print("       forced audio stts: 2 entries → (227, 1024), (1, 560)")
    else:
        new_stts = bytes(stbl_data[stts_box["offset"]:stts_box["end"]])

        print("       preserved original audio stts")

    # ── Build new stsc ─────────────────────────────────────────────────
    #
    # Keep original entries for the real samples.
    # Add one dummy entry for all appended samples.
    #
    new_stsc_entries = list(stsc_entries)

    if extra_count > 0:
        new_stsc_entries.append((
            original_chunk_count + 1,
            extra_count,
            last_sdi
        ))

    new_stsc_payload = struct.pack('>I', len(new_stsc_entries))

    for first_chunk, samples_per_chunk, sample_desc_idx in new_stsc_entries:
        new_stsc_payload += struct.pack('>III', first_chunk, samples_per_chunk, sample_desc_idx)

    new_stsc = build_fullbox(b'stsc', 0, 0, new_stsc_payload)

    print(f"       adjusted stsc: {stsc_entry_count} → {len(new_stsc_entries)} entries (tail chunk)")

    # ── Build new stco ─────────────────────────────────────────────────
    #
    # Keep original chunk offsets.
    # Add one dummy offset for the dummy tail chunk.
    #
    new_stco_offsets = list(stco_offsets)

    if extra_count > 0:
        new_stco_offsets.append(first_dummy_offset)

    new_stco_payload = struct.pack('>I', len(new_stco_offsets))

    for off in new_stco_offsets:
        new_stco_payload += struct.pack('>I', off)

    new_stco = build_fullbox(b'stco', 0, 0, new_stco_payload)

    print(f"       adjusted stco: {stco_entry_count} → {len(new_stco_offsets)} offsets (tail chunk)")

    # ── Rebuild stbl ───────────────────────────────────────────────────
    new_stbl_children = []

    for sc in stbl_children:
        if sc["type"] == b'stsz':
            new_stbl_children.append(new_stsz)

        elif sc["type"] == b'stts':
            new_stbl_children.append(new_stts)

        elif sc["type"] == b'stsc':
            new_stbl_children.append(new_stsc)

        elif sc["type"] == b'stco':
            new_stbl_children.append(new_stco)

        else:
            new_stbl_children.append(bytes(stbl_data[sc["offset"]:sc["end"]]))

    stbl_payload = b''.join(new_stbl_children)
    return bytearray(build_box(b'stbl', stbl_payload))


def fix_chunk_offsets(data: bytearray, delta: int):
    """Walk entire file and fix all stco/co64 offsets by delta."""
    top_boxes = parse_boxes(data, 0, len(data))
    for box in top_boxes:
        if box["type"] == b'moov':
            fix_offsets_recursive(data, box["offset"] + 8, box["end"], delta)

def fix_offsets_recursive(data: bytearray, start: int, end: int, delta: int):
    """Recursively fix stco/co64 offsets."""
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
                if v > 0:
                    write_u32be(data, pos + 16 + i*4, v + delta)
        elif typ == b'co64':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                hi = read_u32be(data, pos + 16 + i*8)
                lo = read_u32be(data, pos + 20 + i*8)
                v = (hi << 32) + lo
                if v > 0:
                    v += delta
                    write_u32be(data, pos + 16 + i*8, (v >> 32) & 0xFFFFFFFF)
                    write_u32be(data, pos + 20 + i*8, v & 0xFFFFFFFF)
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
            fix_offsets_recursive(data, pos + 8, pos + sz, delta)

        pos += sz

# ════════════════════════════════════════════════════════════════════════
# FFMPEG ENCODING
# ════════════════════════════════════════════════════════════════════════

def encode_for_tiktok(input_path: str, output_path: str) -> bool:
    """
    Encode video with TikTok-optimized libx265 settings.
    Uses:
      - libx265, main 10 profile, 
      - 15000kbps bitrate, 20000k maxrate, 40000k bufsize
      - yuv420p10le pixel format
      - AAC 256k audio (best for TikTok)
      - medium preset (good speed/size balance)
      - handler names set via movflags
    """

    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "libx265",
        "-preset", "medium",
        "-profile:v", "main10",
        "-b:v", "15000k",
        "-maxrate", "20000k",
        "-bufsize", "40000k",
        "-pix_fmt", "yuv420p10le",
        "-c:a", "aac",
        "-b:a", "256k",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-y",
        output_path
    ]

    print(f"  cmd: ffmpeg -i \"{input_path}\" \\")
    print(f"       -c:v libx265 -preset medium -profile:v main10 \\")
    print(f"       -b:v 15000k -maxrate 20000k -bufsize 40000k \\")
    print(f"       -pix_fmt yuv420p10le \\")
    print(f"       -c:a aac -b:a 256k \\")
    print(f"       -movflags +faststart \\")
    print(f"       -metadata:s:v handler_name=VideoHandler \\")
    print(f"       -metadata:s:a handler_name=SoundHandler \\")
    print(f"       -y \"{output_path}\"")

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
        print("for BEST results use:")
        print("  - lossless intermediate (ProRes, UT Video, FFV1, etc.)")
        print("  - topaz-enhanced output as input")
        print("  - source footage with minimal compression artifacts")
        print()
        print("pipeline:")
        print("  1. encode with libx265 (yuv420ple, main 10 profile)")
        print("  2. patch sample tables for TikTok passthrough")
        print()
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
    print(f"                (supports up to 4k res, 60fps!)")
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
    print(f"[Step 1/2] Encoding to H.265... (THIS MIGHT TAKE A WHILE -- PLEASE WAIT!!!)")
    print()

    if not encode_for_tiktok(input_file, output_file):
        print()
        print(f"  encoding failed :(")
        sys.exit(1)

    print()

    # Step 2: Patch
    print(f"[Step 2/2] Patching sample tables...")
    print()

    if patch_mp4(output_file):
        print()
        print(f"  done! ready for TikTok: {output_file}")
        return 0
    else:
        print()
        print(f"  patching failed (file is still usable, might get re-encoded)")
        return 1


if __name__ == '__main__':
    sys.exit(main())
