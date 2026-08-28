#!/usr/bin/env python3

import sys
import struct
import subprocess
import os
import time
from pathlib import Path

META_ARTIST   = "uploadmeth: buwryy"
META_COMPOSER = "uploadmeth: buwryy"
META_ALBUM    = "uploadmeth: buwryy"
META_ENCODER  = "Lavf60.16.100"
META_COMMENT  = "uploadmeth: buwryy"
META_COPYRIGHT = "uploadmeth: buwryy"
META_GROUPING = "uploadmeth: buwryy"

STSZ_INFLATE_FACTOR = 10
TRAILING_GARBAGE_SIZE = 16416

def get_default_config() -> dict:
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

    if re_encode:
        if not encode_for_tiktok(input_path, output_path):
            raise RuntimeError("Encoding failed during execution")
        target_file = output_path
    else:
        target_file = input_path

    if not patch_mp4(target_file):
        raise RuntimeError("Patching MP4 structure failed")

    return True

def read_u32be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>I', data[offset:offset+4])[0]

def write_u32be(data: bytearray, offset: int, value: int):
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)

def read_u16be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>H', data[offset:offset+2])[0]

def write_u16be(data: bytearray, offset: int, value: int):
    struct.pack_into('>H', data, offset, value & 0xFFFF)

def parse_boxes(data: bytearray, start: int, end: int) -> list[dict]:
    boxes = []
    pos = start
    while pos + 8 <= end:
        raw_size = read_u32be(data, pos)
        size = raw_size

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
    if end is None:
        end = len(data)
    for box in parse_boxes(data, start, end):
        if box["type"] == fourcc:
            return box
    return None

def find_all_boxes(data: bytearray, fourcc: bytes, start: int = 0, end: int = None) -> list[dict]:
    if end is None:
        end = len(data)
    return [b for b in parse_boxes(data, start, end) if b["type"] == fourcc]

def build_box(box_type: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    return struct.pack('>I', size) + box_type + payload

def build_fullbox(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    ver_flags = struct.pack('>I', (version << 24) | (flags & 0x00FFFFFF))
    return build_box(box_type, ver_flags + payload)

def build_hdlr(handler_type: bytes, name: str) -> bytes:
    payload = b'\x00' * 4
    payload += handler_type
    payload += b'\x00' * 12
    payload += name.encode('utf-8') + b'\x00'
    return build_fullbox(b'hdlr', 0, 0, payload)

def build_data_atom(value: str) -> bytes:
    payload = struct.pack('>II', 1, 0) + value.encode('utf-8')
    return build_box(b'data', payload)

def build_ilst_entry(tag: bytes, value: str) -> bytes:
    return build_box(tag, build_data_atom(value))

def build_udta(artist: str, composer: str, album: str,
               encoder: str, comment: str, copyright: str, grouping: str) -> bytes:
    ilst_payload = b''
    ilst_payload += build_ilst_entry('©ART'.encode('latin-1'), artist)
    ilst_payload += build_ilst_entry('©wrt'.encode('latin-1'), composer)
    ilst_payload += build_ilst_entry('©alb'.encode('latin-1'), album)
    ilst_payload += build_ilst_entry('©too'.encode('latin-1'), encoder)
    if comment:
        ilst_payload += build_ilst_entry('©cmt'.encode('latin-1'), comment)
    ilst_payload += build_ilst_entry(b'cprt', copyright)
    ilst_payload += build_ilst_entry('grpl'.encode('latin-1'), grouping)

    ilst_box = build_box(b'ilst', ilst_payload)
    meta_payload = b'\x00\x00\x00\x00' + ilst_box
    meta_box = build_box(b'meta', meta_payload)

    hdlr_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12 + b'Apple Metadata Handler\x00'
    hdlr_box = build_fullbox(b'hdlr', 0, 0, hdlr_payload)

    udta_payload = hdlr_box + meta_box
    return build_box(b'udta', udta_payload)

def build_trailing_garbage() -> bytes:
    header = struct.pack('>I', 4) + b'\x00' * 4
    padding_size = TRAILING_GARBAGE_SIZE - len(header)
    padding = b'\x00' * padding_size
    return header + padding

def patch_mp4(input_path: str, output_path: str = None) -> bool:
    target = output_path or input_path

    try:
        with open(input_path, 'rb') as f:
            raw = f.read()
    except IOError as e:
        print(f"       couldn't read {input_path}: {e}")
        return False

    data = bytearray(raw)
    orig_size = len(data)
    print(f"       \033[1minput\033[0m: {input_path}")
    print(f"       \033[1msize\033[0m: {orig_size:,} bytes")

    print("       \033[1m[1/6]\033[0m parsing box structure...")
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

    print("       \033[1m[2/6]\033[0m reconstructing layout (ftyp → moov → mdat)...")

    ftyp_data = data[ftyp_box["offset"]:ftyp_box["end"]] if ftyp_box else b''
    moov_data = bytearray(data[moov_box["offset"]:moov_box["end"]])

    mdat_header_size = 16 if read_u32be(data, mdat_box["offset"]) == 1 else 8
    mdat_payload = data[mdat_box["offset"] + mdat_header_size:mdat_box["end"]]
    if len(mdat_payload) >= 4:
        mdat_payload = mdat_payload[:-4]
    mdat_data = build_box(b'mdat', mdat_payload)

    print("       \033[1m[3/6]\033[0m patching moov...")

    moov_inner_start = 8
    moov_inner_end = len(moov_data)
    moov_children = parse_boxes(moov_data, moov_inner_start, moov_inner_end)

    video_trak_idx = None
    audio_trak_idx = None
    tmcd_trak_idx = None

    for i, child in enumerate(moov_children):
        if child["type"] == b'trak':
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
                            hdlr_start = mc["offset"] + 8 + 4
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

    new_moov_children = []
    creation_time = int(time.time()) + 2082844800

    for i, child in enumerate(moov_children):
        if child["type"] == b'mvhd':
            mvhd = bytearray(moov_data[child["offset"]:child["end"]])
            if len(mvhd) > 20:
                write_u32be(mvhd, 12, creation_time)
                write_u32be(mvhd, 16, creation_time)
            new_moov_children.append(bytes(mvhd))

        elif child["type"] == b'trak':
            trak_data = bytearray(moov_data[child["offset"]:child["end"]])

            if i == tmcd_trak_idx:
                print("       stripped tmcd track")
                continue

            is_audio = (i == audio_trak_idx)
            is_video = (i == video_trak_idx)

            if is_audio:
                primary = patch_trak(trak_data, is_video, is_audio, creation_time, inflate=False)
                new_moov_children.append(bytes(primary))

                print("       duplicating audio track for inflation...")
                clone = bytearray(trak_data)
                clone_patched = patch_trak(clone, False, True, creation_time, inflate=True)
                new_moov_children.append(bytes(clone_patched))
                print("       appended inflated audio clone after primary")
            else:
                patched = patch_trak(trak_data, is_video, is_audio, creation_time, inflate=False)
                new_moov_children.append(bytes(patched))

        elif child["type"] == b'udta':
            print("       replacing existing udta")
            continue

        else:
            new_moov_children.append(bytes(moov_data[child["offset"]:child["end"]]))

    new_udta = build_udta(META_ARTIST, META_COMPOSER, META_ALBUM,
                          META_ENCODER, META_COMMENT, META_COPYRIGHT, META_GROUPING)
    new_moov_children.append(new_udta)
    print(f"       injected metadata ({len(new_udta)} bytes)")

    moov_payload = b''.join(new_moov_children)
    new_moov = build_box(b'moov', moov_payload)

    print("       \033[1m[4/6]\033[0m assembling output...")

    output_data = bytearray()
    output_data += ftyp_data
    output_data += new_moov
    output_data += mdat_data

    print("       \033[1m[5/6]\033[0m fixing chunk offsets...")

    new_mdat_offset = len(ftyp_data) + len(new_moov) + 8
    old_mdat_payload_offset = mdat_box["offset"] + mdat_header_size

    offset_delta = new_mdat_offset - old_mdat_payload_offset
    if offset_delta != 0:
        fix_chunk_offsets(output_data, offset_delta)
        print(f"       shifted offsets by {offset_delta:+d}")

    print("       \033[1m[6/6]\033[0m appending trailing data...")
    garbage = build_trailing_garbage()
    output_data += garbage
    print(f"       appended {len(garbage)} bytes")

    try:
        with open(target, 'wb') as f:
            f.write(bytes(output_data))
    except IOError as e:
        print(f"       couldn't write to {target}: {e}")
        return False

    final_size = len(output_data)
    print(f"       \033[1mdone!\033[0m ({orig_size:,} bytes in, {final_size:,} bytes out)")
    return True


def patch_trak(trak_data: bytearray, is_video: bool, is_audio: bool,
               creation_time: int, inflate: bool = False) -> bytearray:
    trak_inner_start = 8
    trak_inner_end = len(trak_data)
    trak_children = parse_boxes(trak_data, trak_inner_start, trak_inner_end)

    new_trak_children = []

    for tc in trak_children:
        if tc["type"] == b'tkhd':
            tkhd = bytearray(trak_data[tc["offset"]:tc["end"]])
            if len(tkhd) > 20:
                write_u32be(tkhd, 12, creation_time)
                write_u32be(tkhd, 16, creation_time)
            new_trak_children.append(bytes(tkhd))

        elif tc["type"] == b'tref':
            print("       stripped tref")
            continue

        elif tc["type"] == b'edts':
            if is_audio or is_video:
                print(f"       stripped {'audio' if is_audio else 'video'} elst")
                continue
            else:
                new_trak_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))

        elif tc["type"] == b'mdia':
            mdia_data = bytearray(trak_data[tc["offset"]:tc["end"]])
            mdia_data = patch_mdia(mdia_data, is_video, is_audio, creation_time, inflate)
            new_trak_children.append(bytes(mdia_data))

        else:
            new_trak_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))

    trak_payload = b''.join(new_trak_children)
    return bytearray(build_box(b'trak', trak_payload))


def patch_mdia(mdia_data: bytearray, is_video: bool, is_audio: bool,
               creation_time: int, inflate: bool = False) -> bytearray:
    mdia_inner_start = 8
    mdia_inner_end = len(mdia_data)
    mdia_children = parse_boxes(mdia_data, mdia_inner_start, mdia_inner_end)

    new_mdia_children = []

    for mc in mdia_children:
        if mc["type"] == b'mdhd':
            mdhd = bytearray(mdia_data[mc["offset"]:mc["end"]])
            if len(mdhd) > 20:
                write_u32be(mdhd, 12, creation_time)
                write_u32be(mdhd, 16, creation_time)
            new_mdia_children.append(bytes(mdhd))

        elif mc["type"] == b'hdlr':
            if is_video:
                new_hdlr = build_hdlr(b'vide', "")
            elif is_audio:
                new_hdlr = build_hdlr(b'soun', "")
            else:
                new_hdlr = build_hdlr(b'vide', "")
            new_mdia_children.append(new_hdlr)

        elif mc["type"] == b'minf':
            minf_data = bytearray(mdia_data[mc["offset"]:mc["end"]])
            minf_data = patch_minf(minf_data, is_audio, inflate)
            new_mdia_children.append(bytes(minf_data))

        else:
            new_mdia_children.append(bytes(mdia_data[mc["offset"]:mc["end"]]))

    mdia_payload = b''.join(new_mdia_children)
    return bytearray(build_box(b'mdia', mdia_payload))


def patch_minf(minf_data: bytearray, is_audio: bool, inflate: bool = False) -> bytearray:
    minf_inner_start = 8
    minf_inner_end = len(minf_data)
    minf_children = parse_boxes(minf_data, minf_inner_start, minf_inner_end)

    new_minf_children = []

    for mc in minf_children:
        if mc["type"] == b'nmhd':
            continue
        elif mc["type"] == b'stbl' and is_audio:
            if inflate:
                stbl_data = bytearray(minf_data[mc["offset"]:mc["end"]])
                stbl_data = patch_stbl(stbl_data)
                new_minf_children.append(bytes(stbl_data))
            else:
                new_minf_children.append(bytes(minf_data[mc["offset"]:mc["end"]]))
        else:
            new_minf_children.append(bytes(minf_data[mc["offset"]:mc["end"]]))

    minf_payload = b''.join(new_minf_children)
    return bytearray(build_box(b'minf', minf_payload))


def patch_stbl(stbl_data: bytearray) -> bytearray:
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
        stsc_entries = [(1, real_count, last_sdi)]
        stco_offsets = [stco_offsets[0]]
        original_chunk_count = 1

    first_dummy_offset = stco_offsets[0]

    new_sizes = list(original_sample_sizes)

    for _ in range(STSZ_INFLATE_FACTOR - 1):
        new_sizes.extend(original_sample_sizes)

    new_count = len(new_sizes)

    new_stsz_payload = struct.pack('>II', 0, new_count)
    for sz in new_sizes:
        new_stsz_payload += struct.pack('>I', sz)

    new_stsz = build_fullbox(b'stsz', 0, 0, new_stsz_payload)

    print(f"       \033[1mstsz\033[0m (clone): {real_count} → {new_count} samples")

    orig_stts_pay = stbl_data[stts_box["offset"] + 12:stts_box["end"]]
    orig_entry_count = read_u32be(orig_stts_pay, 0) if len(orig_stts_pay) >= 4 else 0

    if extra_count > 0:
        ext_payload = struct.pack('>I', orig_entry_count + 1)
        ext_payload += orig_stts_pay[4:]
        ext_payload += struct.pack('>II', extra_count, 1)
        new_stts = build_fullbox(b'stts', 0, 0, ext_payload)
        print(f"       \033[1mstts\033[0m (clone): {orig_entry_count} → {orig_entry_count + 1} entries (+({extra_count}, 1))")
    else:
        new_stts = bytes(stbl_data[stts_box["offset"]:stts_box["end"]])
        print("       preserved original audio stts (clone)")

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

    print(f"       \033[1mstsc\033[0m (clone): {stsc_entry_count} → {len(new_stsc_entries)} entries")

    new_stco_offsets = list(stco_offsets)

    if extra_count > 0:
        new_stco_offsets.append(first_dummy_offset)

    new_stco_payload = struct.pack('>I', len(new_stco_offsets))

    for off in new_stco_offsets:
        new_stco_payload += struct.pack('>I', off)

    new_stco = build_fullbox(b'stco', 0, 0, new_stco_payload)

    print(f"       \033[1mstco\033[0m (clone): {stco_entry_count} → {len(new_stco_offsets)} offsets")

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
    top_boxes = parse_boxes(data, 0, len(data))
    for box in top_boxes:
        if box["type"] == b'moov':
            fix_offsets_recursive(data, box["offset"] + 8, box["end"], delta)

def fix_offsets_recursive(data: bytearray, start: int, end: int, delta: int):
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

def encode_for_tiktok(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "256k",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=",
        "-metadata:s:a", "handler_name=",
        "-y",
        output_path
    ]

    print(f"       \033[3mlibx264 crf18 yuv420p level4.2\033[0m")
    print()

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        error_msg = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        print(f"       ✗ {error_msg}")
        return False

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"       ✓ \033[1mencoded\033[0m ({size_mb:.1f} MB)")
        return True
    else:
        print(f"       ✗ output not created")
        return False

def main():
    if len(sys.argv) < 2:
        print("usage:")
        print(f"  python3 {sys.argv[0]} input_file [output.mp4]")
        print()
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(input_file):
        print(f"error: '{input_file}' not found")
        sys.exit(1)

    if not output_file:
        stem = Path(input_file).stem
        output_file = f"{stem}_tiktok.mp4"

    print()
    print(f"  \033[1mTikTok Optimization Pipeline\033[0m")
    print()
    print(f"  \033[1minput\033[0m:  {input_file}")
    print(f"  \033[1moutput\033[0m: {output_file}")
    print()

    print(f"\033[1m[1/2] Encoding...\033[0m")
    print()

    if not encode_for_tiktok(input_file, output_file):
        print()
        print(f"  ✗ encoding failed")
        sys.exit(1)

    print()
    print(f"\033[1m[2/2] Patching...\033[0m")
    print()

    if patch_mp4(output_file):
        print()
        print(f"  ✓ \033[1mdone\033[0m: {output_file}")
        return 0
    else:
        print()
        print(f"  ✗ patch failed (file may still work)")
        return 1


if __name__ == '__main__':
    sys.exit(main())
