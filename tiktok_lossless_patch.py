#!/usr/bin/env python3
"""
tiktok_patch.py
does stsz inflation trick dynamically.
all metadata fields are fully customizable.
"""

import sys
import struct
import subprocess
import os
from pathlib import Path

# default settings
DEFAULTS = {
    "title": "",
    "artist": "",
    "composer": "",
    "album": "",
    "date": "",
    "encoder": "Lavf59.27.100",
    "comment": "Patched by Buwryy",
    "comment_short": "Patched by Buwryy",
    "genre": "",
    "copyright": "",
    "grouping": "",
    "name_box_payload": "buwryy<3",
    "inflation_rate": 9,
    "dummy_sample_size": 8,
    "trailing_bytes": 33836,
    "re_encode": True,
}


def get_config(overrides: dict = None) -> dict:
    cfg = dict(DEFAULTS)
    if overrides:
        cfg.update(overrides)
    return cfg


def read_u32be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>I', data[offset:offset+4])[0]


def write_u32be(data: bytearray, offset: int, value: int):
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)


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


def build_box(box_type: bytes, payload: bytes) -> bytes:
    size = 8 + len(payload)
    return struct.pack('>I', size) + box_type + payload


def build_fullbox(box_type: bytes, version: int, flags: int, payload: bytes) -> bytes:
    ver_flags = struct.pack('>I', (version << 24) | (flags & 0x00FFFFFF))
    return build_box(box_type, ver_flags + payload)


def build_hdlr(handler_type: bytes, name: str) -> bytes:
    payload = b'\x00' * 4 + handler_type + b'\x00' * 12
    payload += name.encode('utf-8') + b'\x00'
    return build_fullbox(b'hdlr', 0, 0, payload)


def build_data_atom(value: str) -> bytes:
    payload = struct.pack('>II', 1, 0) + value.encode('utf-8')
    return build_box(b'data', payload)


def build_ilst_entry(tag: bytes, value: str) -> bytes:
    return build_box(tag, build_data_atom(value))


def build_combined_udta(cfg: dict) -> bytes:
    """single udta with dual meta boxes + name box, all fields customizable"""

    # --- first meta box (flags=375, full ilst) ---
    ilst1 = b''
    tag_map = [
        ('\xa9nam', cfg.get("title")),
        ('\xa9ART', cfg.get("artist")),
        ('\xa9wrt', cfg.get("composer")),
        ('\xa9alb', cfg.get("album")),
        ('\xa9day', cfg.get("date")),
        ('\xa9too', cfg.get("encoder")),
        ('\xa9cmt', cfg.get("comment")),
        ('\xa9gen', cfg.get("genre")),
        ('cprt',    cfg.get("copyright")),
        ('\xa9grp', cfg.get("grouping")),
    ]
    for tag, val in tag_map:
        if val:
            ilst1 += build_ilst_entry(tag.encode('latin-1'), val)

    meta1_payload = b'\x00\x00\x00\x00' + build_box(b'ilst', ilst1)
    meta1_box = build_fullbox(b'meta', 0, 375, meta1_payload)

    hdlr1_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr1_payload += b'appl\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
    hdlr1_box = build_fullbox(b'hdlr', 0, 0, hdlr1_payload)

    # --- second meta box (flags=0, short comment only) ---
    ilst2 = b''
    short_comment = cfg.get("comment_short")
    if short_comment:
        ilst2 += build_ilst_entry('\xa9cmt'.encode('latin-1'), short_comment)

    meta2_payload = b'\x00\x00\x00\x00' + build_box(b'ilst', ilst2)
    meta2_box = build_fullbox(b'meta', 0, 0, meta2_payload)

    hdlr2_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr2_payload += b'\x00' * 12 + b'\x00'
    hdlr2_box = build_fullbox(b'hdlr', 0, 0, hdlr2_payload)

    # --- unknown 'name' box (fully customizable payload) ---
    name_payload = cfg.get("name_box_payload", "").encode('utf-8')
    name_box = build_box(b'name', name_payload)

    # assemble: hdlr1 + meta1 + hdlr2 + name + meta2
    udta_payload = hdlr1_box + meta1_box + hdlr2_box + name_box + meta2_box
    return build_box(b'udta', udta_payload)


def build_trailing_garbage(size: int) -> bytes:
    void_box = b'\x00\x00\x00\x04VOID'
    pattern = b'\x00\x00\x00\x04'
    remaining = size - len(void_box)
    repeats = max(0, remaining // len(pattern))
    return void_box + (pattern * repeats)


def patch_video(input_path: str, config: dict = None) -> bool:
    cfg = get_config(config)
    p = Path(input_path)
    output_path = str(p.parent / f"{p.stem}_tiktok.mp4")

    if cfg["re_encode"]:
        if not encode_for_tiktok(input_path, output_path):
            raise RuntimeError("Encoding failed during execution")
        target_file = output_path
    else:
        target_file = input_path

    if not patch_mp4(target_file, cfg):
        raise RuntimeError("Patching MP4 structure failed")

    return True


def patch_mp4(input_path: str, cfg: dict) -> bool:
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

    ftyp_box = moov_box = mdat_box = None
    for box in top_boxes:
        if box["type"] == b'ftyp': ftyp_box = box
        elif box["type"] == b'moov': moov_box = box
        elif box["type"] == b'mdat': mdat_box = box

    if not moov_box or not mdat_box:
        print("       ERROR: missing moov or mdat, aborting")
        return False

    print(f"       ftyp @ {ftyp_box['offset'] if ftyp_box else 'N/A'}")
    print(f"       moov @ {moov_box['offset']} ({moov_box['size']} bytes)")
    print(f"       mdat @ {mdat_box['offset']} ({mdat_box['size']} bytes)")

    print("       \033[1m[2/6]\033[0m reconstructing layout (ftyp → moov → mdat)...")
    ftyp_data = data[ftyp_box["offset"]:ftyp_box["end"]] if ftyp_box else b''
    moov_data = bytearray(data[moov_box["offset"]:moov_box["end"]])

    mdat_header_size = 16 if read_u32be(data, mdat_box["offset"]) == 1 else 8
    mdat_payload = data[mdat_box["offset"] + mdat_header_size:mdat_box["end"]]
    mdat_data = build_box(b'mdat', mdat_payload)

    print("       \033[1m[3/6]\033[0m patching moov...")
    moov_children = parse_boxes(moov_data, 8, len(moov_data))

    video_trak_idx = audio_trak_idx = tmcd_trak_idx = None
    for i, child in enumerate(moov_children):
        if child["type"] == b'trak':
            trak_children = parse_boxes(moov_data, child["offset"] + 8, child["end"])
            for tc in trak_children:
                if tc["type"] == b'mdia':
                    mdia_children = parse_boxes(moov_data, tc["offset"] + 8, tc["end"])
                    for mc in mdia_children:
                        if mc["type"] == b'hdlr':
                            ht = moov_data[mc["offset"]+16:mc["offset"]+20]
                            if ht == b'vide': video_trak_idx = i
                            elif ht == b'soun': audio_trak_idx = i
                            elif ht == b'tmcd': tmcd_trak_idx = i

    print(f"       video trak: index {video_trak_idx}")
    print(f"       audio trak: index {audio_trak_idx}")
    print(f"       tmcd trak:  index {tmcd_trak_idx}")

    new_moov_children = []
    for i, child in enumerate(moov_children):
        if child["type"] == b'mvhd':
            new_moov_children.append(bytes(moov_data[child["offset"]:child["end"]]))
        elif child["type"] == b'trak':
            trak_data = bytearray(moov_data[child["offset"]:child["end"]])
            if i == tmcd_trak_idx:
                print("       stripped tmcd track")
                continue
            is_audio = (i == audio_trak_idx)
            is_video = (i == video_trak_idx)
            if is_audio:
                primary = patch_trak(trak_data, is_video, True, inflate=False)
                new_moov_children.append(bytes(primary))
                print("       duplicating audio track for inflation...")
                clone = bytearray(trak_data)
                clone_patched = patch_trak(clone, False, True, inflate=True)
                new_moov_children.append(bytes(clone_patched))
                print("       appended inflated audio clone after primary")
            else:
                patched = patch_trak(trak_data, is_video, False, inflate=False)
                new_moov_children.append(bytes(patched))
        elif child["type"] == b'udta':
            print("       replacing existing udta")
            continue
        else:
            new_moov_children.append(bytes(moov_data[child["offset"]:child["end"]]))

    combined_udta = build_combined_udta(cfg)
    new_moov_children.append(combined_udta)
    print(f"       injected dual metadata ({len(combined_udta)} bytes)")

    moov_payload = b''.join(new_moov_children)
    new_moov = build_box(b'moov', moov_payload)

    print("       \033[1m[4/6]\033[0m assembling output...")
    output_data = bytearray(ftyp_data) + new_moov + mdat_data

    print("       \033[1m[5/6]\033[0m fixing chunk offsets...")
    new_mdat_offset = len(ftyp_data) + len(new_moov) + 8
    old_mdat_payload_offset = mdat_box["offset"] + mdat_header_size
    offset_delta = new_mdat_offset - old_mdat_payload_offset
    if offset_delta != 0:
        fix_chunk_offsets(output_data, offset_delta)
        print(f"       shifted offsets by {offset_delta:+d}")

    print("       \033[1m[6/6]\033[0m appending trailing data...")
    garbage = build_trailing_garbage(cfg["trailing_bytes"])
    output_data += garbage
    print(f"       appended {len(garbage)} bytes")

    try:
        with open(input_path, 'wb') as f:
            f.write(bytes(output_data))
    except IOError as e:
        print(f"       couldn't write to {input_path}: {e}")
        return False

    final_size = len(output_data)
    print(f"       \033[1mdone!\033[0m ({orig_size:,} bytes in, {final_size:,} bytes out)")
    return True


def patch_trak(trak_data: bytearray, is_video: bool, is_audio: bool,
               inflate: bool = False) -> bytearray:
    trak_children = parse_boxes(trak_data, 8, len(trak_data))
    new_children = []

    for tc in trak_children:
        if tc["type"] == b'tkhd':
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))
        elif tc["type"] == b'tref':
            print("       stripped tref")
            continue
        elif tc["type"] == b'edts':
            if is_audio or is_video:
                print(f"       stripped {'audio' if is_audio else 'video'} elst")
                continue
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))
        elif tc["type"] == b'mdia':
            mdia_data = bytearray(trak_data[tc["offset"]:tc["end"]])
            mdia_data = patch_mdia(mdia_data, is_video, is_audio, inflate)
            new_children.append(bytes(mdia_data))
        else:
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))

    return bytearray(build_box(b'trak', b''.join(new_children)))


def patch_mdia(mdia_data: bytearray, is_video: bool, is_audio: bool,
               inflate: bool = False) -> bytearray:
    mdia_children = parse_boxes(mdia_data, 8, len(mdia_data))
    new_children = []

    for mc in mdia_children:
        if mc["type"] == b'mdhd':
            new_children.append(bytes(mdia_data[mc["offset"]:mc["end"]]))
        elif mc["type"] == b'hdlr':
            if is_video:
                new_children.append(build_hdlr(b'vide', "VideoHandler"))
            elif is_audio:
                new_children.append(build_hdlr(b'soun', "SoundHandler"))
            else:
                new_children.append(build_hdlr(b'vide', ""))
        elif mc["type"] == b'minf':
            minf_data = bytearray(mdia_data[mc["offset"]:mc["end"]])
            minf_data = patch_minf(minf_data, is_audio, inflate)
            new_children.append(bytes(minf_data))
        else:
            new_children.append(bytes(mdia_data[mc["offset"]:mc["end"]]))

    return bytearray(build_box(b'mdia', b''.join(new_children)))


def patch_minf(minf_data: bytearray, is_audio: bool, inflate: bool = False) -> bytearray:
    minf_children = parse_boxes(minf_data, 8, len(minf_data))
    new_children = []

    for mc in minf_children:
        if mc["type"] == b'nmhd':
            continue
        elif mc["type"] == b'stbl' and is_audio and inflate:
            stbl_data = bytearray(minf_data[mc["offset"]:mc["end"]])
            stbl_data = patch_stbl(stbl_data)
            new_children.append(bytes(stbl_data))
        else:
            new_children.append(bytes(minf_data[mc["offset"]:mc["end"]]))

    return bytearray(build_box(b'minf', b''.join(new_children)))


def patch_stbl(stbl_data: bytearray) -> bytearray:
    global STSZ_INFLATE_FACTOR, DUMMY_SAMPLE_SIZE
    stbl_children = parse_boxes(stbl_data, 8, len(stbl_data))

    stsz_box = stts_box = stsc_box = stco_box = None
    for sc in stbl_children:
        if sc["type"] == b'stsz': stsz_box = sc
        elif sc["type"] == b'stts': stts_box = sc
        elif sc["type"] == b'stsc': stsc_box = sc
        elif sc["type"] == b'stco': stco_box = sc

    if not all([stsz_box, stts_box, stsc_box, stco_box]):
        return stbl_data

    # use globals set from config before calling
    inflate_factor = STSZ_INFLATE_FACTOR
    dummy_size = DUMMY_SAMPLE_SIZE

    stsz_payload = stbl_data[stsz_box["offset"] + 12:stsz_box["end"]]
    if len(stsz_payload) < 8:
        return stbl_data

    uniform_size = read_u32be(stsz_payload, 0)
    sample_count = read_u32be(stsz_payload, 4)

    original_sizes = []
    if uniform_size == 0:
        pos = 8
        for _ in range(sample_count):
            if pos + 4 > len(stsz_payload): break
            original_sizes.append(read_u32be(stsz_payload, pos))
            pos += 4
    else:
        original_sizes = [uniform_size] * sample_count

    if not original_sizes:
        return stbl_data

    real_count = len(original_sizes)
    extra_count = real_count * (inflate_factor - 1)

    # stsc
    stsc_payload = stbl_data[stsc_box["offset"] + 12:stsc_box["end"]]
    if len(stsc_payload) < 4:
        return stbl_data
    stsc_entry_count = read_u32be(stsc_payload, 0)
    stsc_entries = []
    pos = 4
    for _ in range(stsc_entry_count):
        if pos + 12 > len(stsc_payload): break
        fc = read_u32be(stsc_payload, pos)
        spc = read_u32be(stsc_payload, pos + 4)
        sdi = read_u32be(stsc_payload, pos + 8)
        stsc_entries.append((fc, spc, sdi))
        pos += 12
    last_sdi = stsc_entries[-1][2] if stsc_entries else 1

    # stco
    stco_payload = stbl_data[stco_box["offset"] + 12:stco_box["end"]]
    if len(stco_payload) < 4:
        return stbl_data
    stco_entry_count = read_u32be(stco_payload, 0)
    stco_offsets = []
    pos = 4
    for _ in range(stco_entry_count):
        if pos + 4 > len(stco_payload): break
        stco_offsets.append(read_u32be(stco_payload, pos))
        pos += 4
    if not stco_offsets:
        return stbl_data

    def stsc_total(entries, chunks):
        total = 0
        for i, e in enumerate(entries):
            fc, spc, _ = e
            nfc = entries[i+1][0] if i+1 < len(entries) else chunks + 1
            if nfc > fc:
                total += (nfc - fc) * spc
        return total

    orig_chunks = len(stco_offsets)
    if not stsc_entries or stsc_total(stsc_entries, orig_chunks) != real_count:
        stsc_entries = [(1, real_count, last_sdi)]
        stco_offsets = [stco_offsets[0]]
        orig_chunks = 1

    first_dummy_offset = stco_offsets[0]

    # stsz: real sizes + constant dummy sizes
    new_sizes = list(original_sizes) + [dummy_size] * extra_count
    new_count = len(new_sizes)
    new_stsz_payload = struct.pack('>II', 0, new_count)
    for sz in new_sizes:
        new_stsz_payload += struct.pack('>I', sz)
    new_stsz = build_fullbox(b'stsz', 0, 0, new_stsz_payload)
    print(f"       \033[1mstsz\033[0m (clone): {real_count} → {new_count}")

    # stts: append dummy entry with delta=1
    orig_stts_pay = stbl_data[stts_box["offset"] + 12:stts_box["end"]]
    orig_tc = read_u32be(orig_stts_pay, 0) if len(orig_stts_pay) >= 4 else 0
    ext_payload = struct.pack('>I', orig_tc + 1) + orig_stts_pay[4:]
    ext_payload += struct.pack('>II', extra_count, 1)
    new_stts = build_fullbox(b'stts', 0, 0, ext_payload)
    print(f"       \033[1mstts\033[0m (clone): {orig_tc} → {orig_tc + 1}")

    # stsc: append dummy chunk entry
    new_stsc_entries = list(stsc_entries)
    if extra_count > 0:
        new_stsc_entries.append((orig_chunks + 1, extra_count, last_sdi))
    new_stsc_payload = struct.pack('>I', len(new_stsc_entries))
    for fc, spc, sdi in new_stsc_entries:
        new_stsc_payload += struct.pack('>III', fc, spc, sdi)
    new_stsc = build_fullbox(b'stsc', 0, 0, new_stsc_payload)
    print(f"       \033[1mstsc\033[0m (clone): {stsc_entry_count} → {len(new_stsc_entries)}")

    # stco: append dummy offset
    new_stco_offsets = list(stco_offsets)
    if extra_count > 0:
        new_stco_offsets.append(first_dummy_offset)
    new_stco_payload = struct.pack('>I', len(new_stco_offsets))
    for off in new_stco_offsets:
        new_stco_payload += struct.pack('>I', off)
    new_stco = build_fullbox(b'stco', 0, 0, new_stco_payload)
    print(f"       \033[1mstco\033[0m (clone): {stco_entry_count} → {len(new_stco_offsets)}")

    # rebuild stbl preserving sgpd/sbgp/stsd/etc
    new_children = []
    for sc in stbl_children:
        if sc["type"] == b'stsz': new_children.append(new_stsz)
        elif sc["type"] == b'stts': new_children.append(new_stts)
        elif sc["type"] == b'stsc': new_children.append(new_stsc)
        elif sc["type"] == b'stco': new_children.append(new_stco)
        else: new_children.append(bytes(stbl_data[sc["offset"]:sc["end"]]))

    return bytearray(build_box(b'stbl', b''.join(new_children)))


def fix_chunk_offsets(data: bytearray, delta: int):
    top_boxes = parse_boxes(data, 0, len(data))
    for box in top_boxes:
        if box["type"] == b'moov':
            fix_offsets_recursive(data, box["offset"] + 8, box["end"], delta)


def fix_offsets_recursive(data: bytearray, start: int, end: int, delta: int):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end: break
        typ = data[pos+4:pos+8]
        if typ == b'stco':
            cnt = read_u32be(data, pos + 12)
            for i in range(cnt):
                v = read_u32be(data, pos + 16 + i*4)
                if v > 0: write_u32be(data, pos + 16 + i*4, v + delta)
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
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-level", "4.2", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata", "-1",
        "-y", output_path
    ]
    print(f"       \033[3mlibx264 crf18 yuv420p level4.2\033[0m\n")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        print(f"       ✗ {err}")
        return False
    if os.path.exists(output_path):
        mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"       ✓ \033[1mencoded\033[0m ({mb:.1f} MB)")
        return True
    print(f"       ✗ output not created")
    return False


# module-level globals for patch_stbl access
STSZ_INFLATE_FACTOR = DEFAULTS["inflation_rate"]
DUMMY_SAMPLE_SIZE = DEFAULTS["dummy_sample_size"]


def main():
    if len(sys.argv) < 2:
        print("usage:")
        print(f"  python3 {sys.argv[0]} input_file [output.mp4]")
        print()
        print("examples:")
        print(f'  python3 {sys.argv[0]} in.mp4 out.mp4')
        print(f'  python3 -c "from tiktok_patch import patch_video; patch_video(\'in.mp4\', {{\'name_box_payload\': \'my-custom-tag\', \'comment\': \'custom\'}})"')
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

    print(f"\033[1m[1/2] Encoding...\033[0m\n")
    if not encode_for_tiktok(input_file, output_file):
        print("\n  ✗ encoding failed")
        sys.exit(1)

    print(f"\n\033[1m[2/2] Patching...\033[0m\n")

    cfg = get_config()
    # set globals so patch_stbl can access them
    global STSZ_INFLATE_FACTOR, DUMMY_SAMPLE_SIZE
    STSZ_INFLATE_FACTOR = cfg["inflation_rate"]
    DUMMY_SAMPLE_SIZE = cfg["dummy_sample_size"]

    if patch_mp4(output_file, cfg):
        print(f"\n  ✓ \033[1mdone\033[0m: {output_file}")
        return 0
    else:
        print(f"\n  ✗ patch failed (file may still work)")
        return 1


if __name__ == '__main__':
    sys.exit(main())
