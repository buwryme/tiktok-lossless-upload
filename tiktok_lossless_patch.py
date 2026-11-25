#!/usr/bin/env python3
"""
tiktok_patch.py
patches MP4 containers for TikTok uploads.
all metadata fields are fully customizable.
"""

import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# codec-aware dummy sample sizes from NoBlur
CODEC_DUMMY_SIZES = {
    b'avc1': 8,
    b'avc3': 8,
    b'hvc1': 16,
    b'hev1': 16,
    b'vp09': 4,
    b'av01': 4,
    b'mp4v': 8,
}


GIST_RAW_URL = (
    "https://gist.buwryy.net/buwryy/"
    "c7f5f909e81440d7a28f7045beeadc66/raw/HEAD/tiktok_patcher.toml"
)

BUILTIN_TOML = r'''# Reverse engineered from Editing News' patcher and made open source.
[patcher]
title = ""
artist = ""
composer = ""
album = ""
date = ""
encoder = "Lavf59.27.100"
comment = "Patched by Buwryy"
comment_short = "Patched by Buwryy"
genre = ""
copyright = ""
grouping = ""
name_box_payload = "buwryy<3"
inflation_rate = 10
dummy_sample_size = 8
trailing_bytes = 184100
method = "obx"

[patcher.dummy_sizes]
avc1 = 8
avc2 = 8
avc3 = 8
avc4 = 8
hvc1 = 16
hev1 = 16
dvh1 = 16
dvhe = 16
dvav = 8
dva1 = 8
vp08 = 4
vp09 = 4
av01 = 4
mp4v = 8
s263 = 8
h263 = 8
jpeg = 8
mjpa = 8
mjpb = 8
ap4h = 8
ap4x = 8
'''

ENCODE_DEFAULTS = {
    "re_encode": True,
    "crf": 18,
    "codec": "h264",
}

_PATCHER_CONFIG = None


def _parse_toml(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:
        pass
    data = {}
    section = data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = data
            for part in line[1:-1].strip().split("."):
                part = part.strip()
                if part:
                    cur = cur.setdefault(part, {})
            section = cur
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except Exception:
                value = value.strip('"')
        elif value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass
        section[key] = value
    return data


def _builtin_patcher_defaults():
    return _parse_toml(BUILTIN_TOML)["patcher"]


def load_patcher_config(reload=False):
    global _PATCHER_CONFIG
    if _PATCHER_CONFIG is not None and not reload:
        return _PATCHER_CONFIG
    cfg = _builtin_patcher_defaults()
    try:
        request = urllib.request.Request(
            GIST_RAW_URL,
            headers={
                "Accept": "text/plain",
                "User-Agent": "tiktok-lossless-upload",
            },
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            text = response.read().decode("utf-8")
            remote = _parse_toml(text)["patcher"]
        for key, value in remote.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    except Exception:
        pass
    _PATCHER_CONFIG = cfg
    return cfg


def get_patcher_config():
    return load_patcher_config()


def get_default_config():
    return dict(ENCODE_DEFAULTS)


DEFAULTS = {**ENCODE_DEFAULTS, **_builtin_patcher_defaults()}


def get_config(overrides: dict = None) -> dict:
    global CODEC_DUMMY_SIZES
    cfg = dict(ENCODE_DEFAULTS)
    cfg.update(load_patcher_config())
    cfg.update(ENCODE_DEFAULTS)
    dummy_sizes = cfg.get("dummy_sizes")
    if isinstance(dummy_sizes, dict) and dummy_sizes:
        CODEC_DUMMY_SIZES = {
            key.encode("latin-1"): int(value)
            for key, value in dummy_sizes.items()
        }
    if overrides:
        for key in ENCODE_DEFAULTS:
            if key in overrides:
                cfg[key] = overrides[key]
    return cfg



def read_u32be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>I', data[offset:offset+4])[0]


def write_u32be(data: bytearray, offset: int, value: int):
    struct.pack_into('>I', data, offset, value & 0xFFFFFFFF)


def read_u64be(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack('>Q', data[offset:offset+8])[0]


def write_u64be(data: bytearray, offset: int, value: int):
    struct.pack_into('>Q', data, offset, value & 0xFFFFFFFFFFFFFFFF)


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
        boxes.append({"offset": pos, "size": size, "type": bytes(atype), "end": pos + size})
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

    hdlr1_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr1_payload += b'appl\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
    hdlr1_box = build_fullbox(b'hdlr', 0, 0, hdlr1_payload)

    meta1_inner = hdlr1_box + build_box(b'ilst', ilst1)
    meta1_payload = b'\x00\x00\x00\x00' + meta1_inner
    meta1_box = build_fullbox(b'meta', 0, 375, meta1_payload)

    hdlr2_payload = b'\x00' * 4 + b'mdir' + b'\x00' * 12
    hdlr2_payload += b'\x00' * 12 + b'\x00'
    hdlr2_box = build_fullbox(b'hdlr', 0, 0, hdlr2_payload)

    name_payload = cfg.get("name_box_payload", "").encode('utf-8')
    name_box = build_box(b'name', name_payload) if name_payload else b''

    ilst2 = b''
    short_comment = cfg.get("comment_short", "")
    if short_comment:
        ilst2 += build_ilst_entry('\xa9cmt'.encode('latin-1'), short_comment)
    ilst2_box = build_box(b'ilst', ilst2)

    meta2_inner = hdlr2_box + name_box + ilst2_box
    meta2_payload = b'\x00\x00\x00\x00' + meta2_inner
    meta2_box = build_fullbox(b'meta', 0, 0, meta2_payload)

    udta_payload = meta1_box + meta2_box
    return build_box(b'udta', udta_payload)


def build_trailing_garbage(size: int) -> bytes:
    if size <= 0:
        return b''
    void_box = b'\x00\x00\x00\x04VOID'
    pattern = b'\x00\x00\x00\x04'
    remaining = size - len(void_box)
    repeats = max(0, remaining // len(pattern))
    return void_box + (pattern * repeats)


def detect_video_codec(data: bytearray) -> bytes:
    """detect video codec fourcc from first video sample entry in stsd"""
    moov = find_box(data, b'moov')
    if not moov:
        return b'avc1'
    for trak in parse_boxes(data, moov["offset"] + 8, moov["end"]):
        if trak["type"] != b'trak':
            continue
        for mdia in parse_boxes(data, trak["offset"] + 8, trak["end"]):
            if mdia["type"] != b'mdia':
                continue
            is_video = False
            for child in parse_boxes(data, mdia["offset"] + 8, mdia["end"]):
                if child["type"] == b'hdlr':
                    ht = data[child["offset"]+16:child["offset"]+20]
                    if ht == b'vide':
                        is_video = True
                    break
            if not is_video:
                continue
            for minf in parse_boxes(data, mdia["offset"] + 8, mdia["end"]):
                if minf["type"] != b'minf':
                    continue
                for stbl in parse_boxes(data, minf["offset"] + 8, minf["end"]):
                    if stbl["type"] != b'stbl':
                        continue
                    for stsd in parse_boxes(data, stbl["offset"] + 8, stbl["end"]):
                        if stsd["type"] != b'stsd':
                            continue
                        entries = parse_boxes(data, stsd["offset"] + 8, stsd["end"])
                        if entries:
                            return entries[0]["type"]
    return b'avc1'


def zero_mp4a_samplerate(data: bytearray):
    top_boxes = parse_boxes(data, 0, len(data))
    moov = next((b for b in top_boxes if b["type"] == b'moov'), None)
    if not moov:
        return
    _zero_mp4a_recursive(data, moov["offset"] + 8, moov["end"])


def _zero_mp4a_recursive(data: bytearray, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            write_u32be(data, pos + 28, 0)
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            _zero_mp4a_recursive(data, pos + 8, pos + sz)
        pos += sz


def spoof_audio_bitrate(data: bytearray, source_path: str):
    """copy esds avgBitrate/maxBitrate and mp4a-btrt from source to all target mp4a entries"""
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError:
        return

    src_moov = find_box(src, b'moov')
    if not src_moov:
        return

    src_bitrates = []
    _extract_mp4a_bitrates(src, src_moov["offset"] + 8, src_moov["end"], src_bitrates)
    if not src_bitrates:
        return
    src_avg, src_max = src_bitrates[0]

    tgt_moov = find_box(data, b'moov')
    if not tgt_moov:
        return
    _apply_mp4a_bitrates(data, tgt_moov["offset"] + 8, tgt_moov["end"], src_avg, src_max)


def _extract_mp4a_bitrates(data: bytearray, start: int, end: int, results: list):
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            cpos = pos + 36
            while cpos + 8 <= pos + sz:
                csz = read_u32be(data, cpos)
                if csz < 8 or cpos + csz > pos + sz:
                    break
                ctyp = data[cpos+4:cpos+8]
                if ctyp == b'esds' and csz >= 30:
                    max_br = read_u32be(data, cpos + 22)
                    avg_br = read_u32be(data, cpos + 26)
                    results.append((avg_br, max_br))
                cpos += csz
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            _extract_mp4a_bitrates(data, pos + 8, pos + sz, results)
        pos += sz


def _apply_mp4a_bitrates(data: bytearray, start: int, end: int, avg: int, mx: int) -> int:
    count = 0
    pos = start
    while pos + 8 <= end:
        sz = read_u32be(data, pos)
        if sz < 8 or pos + sz > end:
            break
        typ = data[pos+4:pos+8]
        if typ == b'mp4a' and sz >= 36:
            cpos = pos + 36
            while cpos + 8 <= pos + sz:
                csz = read_u32be(data, cpos)
                if csz < 8 or cpos + csz > pos + sz:
                    break
                ctyp = data[cpos+4:cpos+8]
                if ctyp == b'esds' and csz >= 30:
                    write_u32be(data, cpos + 22, mx)
                    write_u32be(data, cpos + 26, avg)
                    count += 1
                elif ctyp == b'btrt' and csz >= 20:
                    write_u32be(data, cpos + 12, avg)
                    write_u32be(data, cpos + 16, mx)
                cpos += csz
        elif typ in (b'moov', b'trak', b'mdia', b'minf', b'stbl', b'stsd'):
            count += _apply_mp4a_bitrates(data, pos + 8, pos + sz, avg, mx)
        pos += sz
    return count


def copy_avcc_from_source(source_path: str, target_data: bytearray) -> bytearray:
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError:
        return target_data

    src_moov = find_box(src, b'moov')
    tgt_moov = find_box(target_data, b'moov')
    if not src_moov or not tgt_moov:
        return target_data

    for cfg_box in (b'avcC', b'hvcC'):
        src_cfg = find_box(src, cfg_box, src_moov["offset"], src_moov["end"])
        tgt_cfg = find_box(target_data, cfg_box, tgt_moov["offset"], tgt_moov["end"])
        if src_cfg and tgt_cfg and src_cfg["size"] == tgt_cfg["size"]:
            target_data[tgt_cfg["offset"]:tgt_cfg["end"]] = src[src_cfg["offset"]:src_cfg["end"]]

    return target_data


def fix_btrt_from_source(source_path: str, target_data: bytearray):
    try:
        with open(source_path, 'rb') as f:
            src = bytearray(f.read())
    except IOError:
        return

    src_moov = find_box(src, b'moov')
    tgt_moov = find_box(target_data, b'moov')
    if not src_moov or not tgt_moov:
        return

    src_btrt = find_box(src, b'btrt', src_moov["offset"], src_moov["end"])
    tgt_btrt = find_box(target_data, b'btrt', tgt_moov["offset"], tgt_moov["end"])

    if src_btrt and tgt_btrt and src_btrt["size"] == tgt_btrt["size"]:
        avg = read_u32be(src, src_btrt["offset"] + 12)
        write_u32be(target_data, tgt_btrt["offset"] + 12, avg)


def strip_free_boxes(data: bytearray) -> bytearray:
    top_boxes = parse_boxes(data, 0, len(data))
    keep_ranges = []
    for box in top_boxes:
        if box["type"] not in (b'free', b'skip'):
            keep_ranges.append((box["offset"], box["end"]))
    if len(keep_ranges) == len(top_boxes):
        return data
    result = bytearray()
    for start, end in keep_ranges:
        result.extend(data[start:end])
    return result


def upgrade_mvhd_to_v1(mvhd: bytearray) -> bytearray:
    """convert mvhd to version 1 with unknown duration and nexttrackid=5"""
    ver = mvhd[8]
    timescale = read_u32be(mvhd, 20) if ver == 0 else read_u32be(mvhd, 28)
    ntid_off = 96 if ver == 0 else 108
    ntid = read_u32be(mvhd, ntid_off)

    body = bytearray(112)
    body[0] = 1
    struct.pack_into('>I', body, 20, timescale)
    struct.pack_into('>Q', body, 24, 0xFFFFFFFFFFFFFFFF)
    struct.pack_into('>I', body, 32, 0x00010000)
    struct.pack_into('>H', body, 36, 0x0100)
    struct.pack_into('>I', body, 44, 0x00010000)
    struct.pack_into('>I', body, 60, 0x00010000)
    struct.pack_into('>I', body, 76, 0x40000000)
    struct.pack_into('>I', body, 108, 5)

    return bytearray(build_box(b'mvhd', bytes(body)))


def patch_elst_plus_one(edts_data: bytearray) -> bytearray:
    """increment first elst segment_duration by 1 tick"""
    children = parse_boxes(edts_data, 8, len(edts_data))
    new_children = []
    for c in children:
        if c["type"] == b'elst':
            elst = bytearray(edts_data[c["offset"]:c["end"]])
            ver = elst[8]
            count = read_u32be(elst, 12)
            if count > 0:
                dur_off = 16 if ver == 0 else 20
                dur = read_u32be(elst, dur_off)
                write_u32be(elst, dur_off, dur + 1)
            new_children.append(bytes(elst))
        else:
            new_children.append(bytes(edts_data[c["offset"]:c["end"]]))
    return bytearray(build_box(b'edts', b''.join(new_children)))


def patch_video(input_path: str, config: dict = None) -> bool:
    cfg = get_config(config)
    p = Path(input_path)
    output_path = str(p.parent / f"{p.stem}_tiktok.mp4")

    if cfg.get("method", "obx") in ("obx", "slsr", "mvhd") and p.suffix.lower() == ".mp4":
        shutil.copy2(input_path, output_path)
        if not patch_mp4(output_path, cfg, input_path):
            raise RuntimeError("Patching MP4 structure failed")
        return True

    if cfg["re_encode"]:
        if not encode_for_tiktok(input_path, output_path, cfg.get("crf", 18), cfg.get("codec", "h264")):
            raise RuntimeError("Encoding failed during execution")
    else:
        if not remux_for_tiktok(input_path, output_path):
            raise RuntimeError("Remux failed during execution")

    if not patch_mp4(output_path, cfg, input_path):
        raise RuntimeError("Patching MP4 structure failed")

    return True



WATERMARK = "Patched by https://git.buwryy.net/buwryy/tiktok-lossless-upload"


def _cb_u32(data, offset):
    return struct.unpack('>I', data[offset:offset + 4])[0]


def _cb_u64(data, offset):
    return struct.unpack('>Q', data[offset:offset + 8])[0]


def _cb_box(box_type, payload):
    return struct.pack('>I', 8 + len(payload)) + box_type + payload


def _cb_fullbox(box_type, version, flags, payload):
    return _cb_box(
        box_type,
        struct.pack('>I', (version << 24) | (flags & 0x00FFFFFF)) + payload,
    )


def _cb_boxes(data, start=0, end=None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = _cb_u32(data, pos)
        box_type = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            size = _cb_u64(data, pos + 8)
            header = 16
        elif size == 0:
            size = end - pos
        if size < 8 or pos + size > end:
            break
        yield {
            "offset": pos,
            "size": size,
            "type": box_type,
            "header": header,
            "end": pos + size,
            "payload": data[pos + header:pos + size],
        }
        pos += size


def _cb_upgrade_mvhd(mvhd):
    body = mvhd[8:]
    version = body[0]
    if version == 1:
        updated = bytearray(body)
        updated[24:32] = b"\xff" * 8
        return _cb_box(b"mvhd", bytes(updated))
    if version != 0 or len(body) < 100:
        return mvhd
    updated = bytearray(112)
    updated[0:4] = struct.pack(">I", 1 << 24)
    updated[4:12] = struct.pack(">Q", _cb_u32(body, 4))
    updated[12:20] = struct.pack(">Q", _cb_u32(body, 8))
    updated[20:24] = body[12:16]
    updated[24:32] = b"\xff" * 8
    updated[32:36] = body[20:24]
    updated[36:38] = body[24:26]
    updated[38:48] = body[26:36]
    updated[48:84] = body[36:72]
    updated[84:108] = body[72:96]
    updated[108:112] = body[96:100]
    return _cb_box(b"mvhd", bytes(updated))


def _cb_comment_entry(value=WATERMARK):
    data = _cb_box(
        b"data",
        struct.pack(">II", 1, 0) + value.encode("utf-8"),
    )
    return _cb_box(b"\xa9cmt", data)


def _cb_patch_udta(udta):
    children = list(_cb_boxes(udta, 8, len(udta)))
    output = []
    has_meta = False
    for child in children:
        if child["type"] != b"meta":
            output.append(udta[child["offset"]:child["end"]])
            continue
        has_meta = True
        meta_payload = udta[child["offset"] + 8:child["end"]]
        meta_children = list(_cb_boxes(meta_payload, 4, len(meta_payload)))
        new_meta = []
        has_ilst = False
        for meta_child in meta_children:
            if meta_child["type"] != b"ilst":
                new_meta.append(meta_payload[meta_child["offset"]:meta_child["end"]])
                continue
            has_ilst = True
            ilst_payload = meta_payload[meta_child["offset"] + 8:meta_child["end"]]
            entries = list(_cb_boxes(ilst_payload, 0, len(ilst_payload)))
            new_entries = []
            has_comment = False
            for entry in entries:
                if entry["type"] == b"\xa9cmt":
                    new_entries.append(_cb_comment_entry())
                    has_comment = True
                else:
                    new_entries.append(ilst_payload[entry["offset"]:entry["end"]])
            if not has_comment:
                new_entries.append(_cb_comment_entry())
            new_meta.append(_cb_box(b"ilst", b"".join(new_entries)))
        if not has_ilst:
            new_meta.append(_cb_box(b"ilst", _cb_comment_entry()))
        output.append(_cb_fullbox(b"meta", 0, 0, b"".join(new_meta)))
    if not has_meta:
        hdlr = _cb_fullbox(
            b"hdlr", 0, 0,
            struct.pack(">I", 0) + b"mdir" + b"\x00" * 12 + b"\x00",
        )
        output.append(_cb_fullbox(
            b"meta", 0, 0,
            hdlr + _cb_box(b"ilst", _cb_comment_entry()),
        ))
    return _cb_box(b"udta", b"".join(output))


def _cb_shift_offsets(data, start, end, delta):
    for box in _cb_boxes(data, start, end):
        if box["type"] in (b"stco", b"co64"):
            count = _cb_u32(data, box["offset"] + 12)
            is64 = box["type"] == b"co64"
            for index in range(count):
                offset = box["offset"] + 16 + index * (8 if is64 else 4)
                value = _cb_u64(data, offset) if is64 else _cb_u32(data, offset)
                value += delta
                if is64:
                    data[offset:offset + 8] = struct.pack(">Q", value)
                else:
                    data[offset:offset + 4] = struct.pack(">I", value & 0xFFFFFFFF)
        elif box["type"] in (b"moov", b"trak", b"mdia", b"minf", b"stbl"):
            _cb_shift_offsets(data, box["offset"] + 8, box["end"], delta)


def _cb_rebuild(data, delta=0):
    output = []
    for box in _cb_boxes(data, 0, len(data)):
        raw = data[box["offset"]:box["end"]]
        if box["type"] == b"moov":
            moov_children = []
            for child in _cb_boxes(raw, 8, len(raw)):
                child_raw = raw[child["offset"]:child["end"]]
                if child["type"] == b"mvhd":
                    moov_children.append(_cb_upgrade_mvhd(child_raw))
                elif child["type"] == b"udta":
                    moov_children.append(_cb_patch_udta(child_raw))
                else:
                    moov_children.append(child_raw)
            moov = _cb_box(b"moov", b"".join(moov_children))
            if delta:
                mutable = bytearray(moov)
                _cb_shift_offsets(mutable, 8, len(mutable), delta)
                moov = bytes(mutable)
            output.append(moov)
        else:
            output.append(raw)
    return b"".join(output)


def patch_bytes_mvhd(data):
    old_moov = next(b for b in _cb_boxes(data, 0, len(data)) if b["type"] == b"moov")
    old_mdat = next(b for b in _cb_boxes(data, 0, len(data)) if b["type"] == b"mdat")
    probe = _cb_rebuild(data, 0)
    new_moov = next(b for b in _cb_boxes(probe, 0, len(probe)) if b["type"] == b"moov")
    delta = new_moov["size"] - old_moov["size"]
    if old_moov["offset"] > old_mdat["offset"]:
        delta = 0
    return _cb_rebuild(data, delta)


def patch_mp4_mvhd(input_path, cfg=None):
    with open(input_path, "rb") as handle:
        data = handle.read()
    with open(input_path, "wb") as handle:
        handle.write(patch_bytes_mvhd(data))
    return True


def patch_mp4_slsr(input_path, cfg=None):
    with open(input_path, "rb") as handle:
        data = handle.read()
    with open(input_path, "wb") as handle:
        handle.write(patch_bytes_slsr(data))
    return True


def patch_mp4_obx(input_path, cfg=None):
    with open(input_path, "rb") as handle:
        data = handle.read()
    with open(input_path, "wb") as handle:
        handle.write(patch_bytes_obx(data))
    return True


# silent-tail audio method: extends the audio track with a silent AAC tail and
# duplicates it, leaving the video stream untouched.




_SLSR_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}
_SLSR_AAC_RATES = [96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350]
_SLSR_SILENT_FRAME = bytes([33, 16, 4, 96, 140, 28])


def _slsr_u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


def _slsr_u64(data, off):
    return struct.unpack_from(">Q", data, off)[0]


def _slsr_p32(value):
    return struct.pack(">I", value & 0xFFFFFFFF)


def _slsr_p64(value):
    return struct.pack(">Q", value)


def _slsr_fourcc(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value[:4])
    return str(value).encode("latin-1")[:4].ljust(4, b" ")


def _slsr_box(name, payload):
    return _slsr_p32(len(payload) + 8) + _slsr_fourcc(name) + payload


def _slsr_p32s(*values):
    return b"".join(_slsr_p32(v) for v in values)


@dataclass
class _SlSrBox:
    name: bytes
    data: bytes
    raw: Optional[bytes] = None
    children: Optional[list] = field(default=None)

    def __deepcopy__(self, memo):
        return _SlSrBox(
            self.name,
            self.data,
            self.raw,
            None if self.children is None else [c.__deepcopy__(memo) for c in self.children],
        )


def _slsr_read_header(data, available):
    assert len(data) >= 8
    size = _slsr_u32(data, 0)
    header = 16 if size == 1 else 8
    assert len(data) >= header
    total = _slsr_u64(data, 8) if size == 1 else (size or available)
    assert isinstance(total, int) and total >= header and total <= available
    return _slsr_fourcc(data[4:8]), header, total, size == 0


def _slsr_parse_boxes(data, depth=0):
    assert depth < 16
    result = []
    pos = 0
    while pos < len(data):
        name, header, size, _zero = _slsr_read_header(data[pos:], len(data) - pos)
        raw = data[pos:pos + size]
        payload = raw[header:]
        result.append(_SlSrBox(name, payload, raw, _slsr_parse_boxes(payload, depth + 1) if name in _SLSR_CONTAINERS else None))
        pos += size
    return result


def _slsr_serialize_box(item):
    if item.children is not None:
        return _slsr_box(item.name, b"".join(_slsr_serialize_box(c) for c in item.children))
    if item.raw is not None:
        return item.raw
    return _slsr_box(item.name, item.data)


def _slsr_child(item, name):
    for c in item.children or []:
        if c.name == name:
            return c
    raise AssertionError("missing MP4 box: " + name.decode("latin-1"))


def _slsr_find_child(item, name):
    for c in item.children or []:
        if c.name == name:
            return c
    return None


def _slsr_set_data(item, data):
    item.data = bytes(data)
    item.raw = None


def _slsr_write_int(item, offset, width, value):
    assert value >= 0 and (width == 8 or value < 0xFFFFFFFF)
    data = bytearray(item.data)
    data[offset:offset + width] = _slsr_p64(value) if width == 8 else _slsr_p32(value)
    _slsr_set_data(item, data)


def _slsr_read_int(data, offset, width):
    return _slsr_u64(data, offset) if width == 8 else _slsr_u32(data, offset)


def _slsr_descriptor(data, start):
    assert start < len(data)
    tag = data[start]
    pos = start + 1
    length = 0
    for _ in range(4):
        assert pos < len(data)
        value = data[pos]
        pos += 1
        length = length * 128 + (value & 0x7F)
        if not value & 0x80:
            assert pos + length <= len(data)
            return tag, pos, pos + length
    raise AssertionError("invalid descriptor")


def _slsr_descriptor_bytes(tag, payload):
    length = len(payload)
    return bytes([tag, 0x80 | (length >> 21), 0x80 | ((length >> 14) & 0x7F),
                  0x80 | ((length >> 7) & 0x7F), length & 0x7F]) + payload


def _slsr_patch_audio_description(stsd):
    data = stsd.data
    assert len(data) >= 44 and _slsr_u32(data, 0) == 0 and _slsr_u32(data, 4) == 1
    sample = data[8:]
    assert _slsr_fourcc(sample[4:8]) == b"mp4a" and _slsr_u32(sample, 0) == len(sample)
    assert sample[16] == 0 and sample[17] == 0 and sample[24] == 0 and sample[25] == 2
    entries = _slsr_parse_boxes(sample[36:])
    esds = next((e for e in entries if e.name == b"esds"), None)
    assert esds is not None and not any(e.name in (b"sinf", b"wave") for e in entries)
    esds_data = esds.data
    assert len(esds_data) >= 4 and _slsr_u32(esds_data, 0) == 0
    decoder = _slsr_descriptor(esds_data, 4)
    assert decoder[0] == 3 and decoder[2] - decoder[1] >= 3 and esds_data[decoder[1] + 2] == 0

    rate_index = -1
    rebuilt = []
    pos = decoder[1] + 3
    while pos < decoder[2]:
        item = _slsr_descriptor(esds_data[:decoder[2]], pos)
        if item[0] != 4:
            rebuilt.append(esds_data[pos:item[2]])
        else:
            assert item[2] - item[1] >= 13 and esds_data[item[1]] == 64
            nested = []
            nested_pos = item[1] + 13
            while nested_pos < item[2]:
                sub = _slsr_descriptor(esds_data[:item[2]], nested_pos)
                if sub[0] != 5:
                    nested.append(esds_data[nested_pos:sub[2]])
                else:
                    config = esds_data[sub[1]:sub[2]]
                    assert rate_index < 0 and len(config) >= 2
                    assert config[0] >> 3 == 2 and (config[1] & 127) == 16
                    rate_index = ((config[0] & 7) << 1) | (config[1] >> 7)
                    bits = "".join(format(b, "08b") for b in config[2:])
                    assert rate_index < len(_SLSR_AAC_RATES)
                    assert "1" not in bits or re.fullmatch(r"01010110111001010*", bits)
                    table = format(rate_index, "04b")
                    config_bits = ("00010" + table + "0000000000001" + table
                                   + "00000001000000000000000010000")
                    padded = config_bits.ljust(math.ceil(len(config_bits) / 8) * 8, "0") + "00000000"
                    nested.append(_slsr_descriptor_bytes(
                        5, bytes(int(padded[i:i + 8], 2) for i in range(0, len(padded), 8))
                    ))
                nested_pos = sub[2]
            rebuilt.append(_slsr_descriptor_bytes(
                4, esds_data[item[1]:item[1] + 13] + b"".join(nested)
            ))
        pos = item[2]

    assert rate_index >= 0
    _slsr_set_data(esds, esds_data[:4] + _slsr_descriptor_bytes(
        3, esds_data[decoder[1]:decoder[1] + 3] + b"".join(rebuilt)
    ) + esds_data[decoder[2]:])
    replacement = _slsr_box(b"mp4a", sample[8:36] + b"".join(_slsr_serialize_box(e) for e in entries))
    return _SLSR_AAC_RATES[rate_index], _slsr_p32s(0, 2) + sample + replacement


def _slsr_table_count(item, stride):
    assert len(item.data) >= 8 and _slsr_u32(item.data, 0) == 0
    count = _slsr_u32(item.data, 4)
    assert len(item.data) == 8 + count * stride and count <= 5_000_000
    return count


def _slsr_header_fields(item):
    assert item.data[0] in (0, 1)
    version1 = item.data[0] == 1
    is_tkhd = item.name == b"tkhd"
    offset = (28 if version1 else 20) if is_tkhd else (24 if version1 else 16)
    width = 8 if version1 else 4
    return {
        "offset": offset,
        "width": width,
        "duration": _slsr_read_int(item.data, offset, width),
        "scale": 0 if is_tkhd else _slsr_u32(item.data, offset - 4),
    }


def _slsr_extend_edit(item, audio_duration, audio_scale, movie_scale, sample_rate, duration, tail):
    edts = _slsr_find_child(item, b"edts")
    if edts is None:
        assert abs(duration - round(audio_duration * movie_scale / audio_scale)) <= 1
        return tail
    elst = _slsr_child(edts, b"elst")
    data = elst.data
    assert data[0] in (0, 1)
    width = 8 if data[0] == 1 else 4
    count = _slsr_u32(data, 4)
    stride = 2 * width + 4
    assert count in (1, 2) and len(data) == 8 + count * stride
    offset = 8 + (count - 1) * stride
    segment = _slsr_read_int(data, offset, width)
    media_time = _slsr_read_int(data, offset + width, width)
    assert media_time >= 0 and _slsr_u32(data, offset + 2 * width) == 65536
    if count == 2:
        assert all(b == 255 for b in data[8 + width:8 + 2 * width])
        assert _slsr_u32(data, 8 + 2 * width) == 65536
    assert abs(duration - segment - (_slsr_read_int(data, 8, width) if count == 2 else 0)) <= 1
    difference = (audio_duration - media_time) * movie_scale - segment * audio_scale
    assert abs(difference) <= math.ceil(2048 * audio_scale / sample_rate) * movie_scale
    extra = tail + max(0, math.ceil(difference / audio_scale))
    _slsr_write_int(elst, offset, width, segment + extra)
    return extra


def patch_bytes_slsr(data):
    tops = []
    pos = 0
    while pos < len(data):
        name, header, size, zero = _slsr_read_header(data[pos:pos + 16], len(data) - pos)
        assert len(tops) < 10000 and name not in (b"moof", b"sidx", b"mfra", b"ssix")
        tops.append({"name": name, "at": pos, "size": size, "width": header, "zero": zero})
        pos += size

    moovs = [b for b in tops if b["name"] == b"moov"]
    mdats = [b for b in tops if b["name"] == b"mdat"]
    assert len(moovs) == 1 and mdats and moovs[0]["width"] == 8 and moovs[0]["size"] <= 32 * 1024 * 1024
    moov = moovs[0]
    root = _slsr_parse_boxes(data[moov["at"]:moov["at"] + moov["size"]])[0]
    tracks = [b for b in root.children if b.name == b"trak"]
    audio_tracks = []
    for track in tracks:
        hdlr = _slsr_child(_slsr_child(track, b"mdia"), b"hdlr")
        if hdlr.data[8:12] == b"soun":
            audio_tracks.append(track)
    assert len(audio_tracks) == 1
    audio = audio_tracks[0]
    media = _slsr_child(audio, b"mdia")
    minf = _slsr_child(media, b"minf")
    dref = _slsr_child(_slsr_child(minf, b"dinf"), b"dref").data
    refs = _slsr_parse_boxes(dref[8:])
    assert _slsr_u32(dref, 0) == 0 and _slsr_u32(dref, 4) == len(refs) and refs
    assert all(ref.name == b"url " and _slsr_u32(ref.data, 0) == 1 for ref in refs)

    stbl = _slsr_child(minf, b"stbl")
    allowed = {b"stsd", b"stsz", b"stsc", b"stts", b"stco", b"co64", b"sbgp", b"sgpd",
               b"subs", b"free", b"skip", b"ctts", b"stss", b"stps", b"sdtp", b"stdp"}
    assert all(c.name in allowed for c in stbl.children)
    stsd = _slsr_child(stbl, b"stsd")
    sample_rate, patched_stsd = _slsr_patch_audio_description(stsd)

    stsz = _slsr_child(stbl, b"stsz")
    assert len(stsz.data) >= 12 and _slsr_u32(stsz.data, 0) == 0
    uniform = _slsr_u32(stsz.data, 4)
    sample_count = _slsr_u32(stsz.data, 8)
    assert sample_count > 0 and sample_count <= 5_000_000
    assert len(stsz.data) == 12 + (0 if uniform else 4 * sample_count)

    def sample_size(index):
        return uniform or _slsr_u32(stsz.data, 12 + 4 * index)

    stsc = _slsr_child(stbl, b"stsc")
    runs = _slsr_table_count(stsc, 12)
    if _slsr_find_child(stbl, b"stco") is not None:
        assert _slsr_find_child(stbl, b"co64") is None
        offsets = _slsr_find_child(stbl, b"stco")
    else:
        offsets = _slsr_child(stbl, b"co64")
    offset_width = 4 if offsets.name == b"stco" else 8
    chunks = _slsr_table_count(offsets, offset_width)
    assert runs > 0 and chunks > 0

    def first_chunk(index):
        return _slsr_u32(stsc.data, 8 + 12 * index)

    assert first_chunk(0) == 1
    for index in range(runs):
        assert first_chunk(index) <= chunks
        assert index == 0 or first_chunk(index) > first_chunk(index - 1)
        assert _slsr_u32(stsc.data, 12 + 12 * index) > 0
        assert _slsr_u32(stsc.data, 16 + 12 * index) == 1

    run = 0
    sampled = 0
    for index in range(chunks):
        if run + 1 < runs and first_chunk(run + 1) == index + 1:
            run += 1
        per_chunk = _slsr_u32(stsc.data, 12 + 12 * run)
        assert sampled + per_chunk <= sample_count
        chunk_bytes = 0
        for _ in range(per_chunk):
            size = sample_size(sampled)
            sampled += 1
            assert size > 0
            chunk_bytes += size
        offset = _slsr_read_int(offsets.data, 8 + index * offset_width, offset_width)
        assert any(offset >= m["at"] + m["width"] and offset + chunk_bytes <= m["at"] + m["size"]
                   for m in mdats)
    assert sampled == sample_count

    stts = _slsr_child(stbl, b"stts")
    timing_count = _slsr_table_count(stts, 8)
    audio_duration = 0
    timed_samples = 0
    timing = []
    for index in range(timing_count):
        count = _slsr_u32(stts.data, 8 + 8 * index)
        delta = _slsr_u32(stts.data, 12 + 8 * index)
        assert count > 0 and delta > 0
        timing.append([count, delta])
        timed_samples += count
        audio_duration += count * delta
    assert timed_samples == sample_count

    mdhd = _slsr_child(media, b"mdhd")
    mvhd = _slsr_child(root, b"mvhd")
    tkhd = _slsr_child(audio, b"tkhd")
    audio_header = _slsr_header_fields(mdhd)
    movie_header = _slsr_header_fields(mvhd)
    track_header = _slsr_header_fields(tkhd)
    assert audio_header["scale"] and movie_header["scale"] and audio_header["duration"] > 0
    assert abs(audio_header["duration"] - audio_duration) <= math.ceil(
        2048 * audio_header["scale"] / sample_rate)
    assert tkhd.data[3] & 1 and track_header["duration"] > 0

    tail_frames = max(10, math.ceil(0.35 * sample_rate / 1024))
    for item in stbl.children:
        payload = item.data
        if item.name == b"sbgp":
            header = 12 if payload[0] == 1 else 8
            assert payload[0] in (0, 1) and len(payload) >= header + 4
            groups = _slsr_u32(payload, header)
            assert len(payload) == header + 4 + groups * 8
            covered = sum(_slsr_u32(payload, header + 4 + 8 * g) for g in range(groups))
            assert covered <= sample_count
            extra = sample_count - covered + tail_frames
            if groups and _slsr_u32(payload, len(payload) - 4) == 0:
                at = len(payload) - 8
                _slsr_write_int(item, at, 4, _slsr_u32(payload, at) + extra)
            else:
                _slsr_set_data(item, payload[:header] + _slsr_p32(groups + 1) + payload[header + 4:]
                         + _slsr_p32s(extra, 0))
        elif item.name == b"ctts":
            count = _slsr_table_count(item, 8)
            covered = 0
            for index in range(count):
                covered += _slsr_u32(payload, 8 + 8 * index)
                assert _slsr_u32(payload, 12 + 8 * index) == 0
            assert covered == sample_count
            _slsr_set_data(item, _slsr_p32s(0, count + 1) + payload[8:] + _slsr_p32s(tail_frames, 0))
        elif item.name in (b"stss", b"stps"):
            count = _slsr_table_count(item, 4)
            previous = 0
            for index in range(count):
                value = _slsr_u32(payload, 8 + 4 * index)
                assert value > previous and value <= sample_count
                previous = value
            _slsr_set_data(item, _slsr_p32s(0, count + tail_frames) + payload[8:]
                     + _slsr_p32s(*[sample_count + i + 1 for i in range(tail_frames)]))
        elif item.name in (b"sdtp", b"stdp"):
            stride = 1 if item.name == b"sdtp" else 2
            assert len(payload) == 4 + stride * sample_count
            _slsr_set_data(item, payload + bytes(stride * tail_frames))

    tail_extra = tail_frames - 8
    audio_tail = math.floor(tail_frames * 1024 * audio_header["scale"] / sample_rate)
    movie_tail = round(audio_tail * movie_header["scale"] / audio_header["scale"])
    assert movie_tail > 0
    edit_extra = _slsr_extend_edit(audio, audio_duration, audio_header["scale"], movie_header["scale"],
                             sample_rate, track_header["duration"], movie_tail)
    _slsr_write_int(tkhd, track_header["offset"], track_header["width"],
              track_header["duration"] + edit_extra)
    _slsr_write_int(mdhd, audio_header["offset"], audio_header["width"],
              audio_header["duration"] + audio_tail)

    for index in range(tail_frames):
        delta = (math.floor((index + 1) * 1024 * audio_header["scale"] / sample_rate)
                 - math.floor(index * 1024 * audio_header["scale"] / sample_rate))
        assert 0 < delta <= 0xFFFFFFFF
        if timing[-1][1] == delta:
            timing[-1][0] += 1
        else:
            timing.append([1, delta])
    new_stts = bytearray(8 + len(timing) * 8)
    struct.pack_into(">I", new_stts, 4, len(timing))
    for index, (count, delta) in enumerate(timing):
        struct.pack_into(">II", new_stts, 8 + index * 8, count, delta)
    _slsr_set_data(stts, new_stts)

    new_stsz = bytearray(12 + 4 * (sample_count + tail_frames))
    struct.pack_into(">I", new_stsz, 8, sample_count + tail_frames)
    for index in range(sample_count + tail_frames):
        struct.pack_into(">I", new_stsz, 12 + 4 * index,
                         sample_size(index) if index < sample_count else len(_SLSR_SILENT_FRAME))
    _slsr_set_data(stsz, new_stsz)

    old_offset_width = offset_width
    first_offset = len(data) + 8
    second_offset = first_offset + tail_extra * len(_SLSR_SILENT_FRAME)
    offset_width = 8 if old_offset_width == 8 or second_offset > 0xFFFFFFFF else 4
    new_offsets = bytearray(8 + (chunks + 2) * offset_width)
    struct.pack_into(">I", new_offsets, 4, chunks + 2)
    for index in range(chunks + 2):
        if index < chunks:
            value = _slsr_read_int(offsets.data, 8 + index * old_offset_width, old_offset_width)
        else:
            value = first_offset if index == chunks else second_offset
        if offset_width == 8:
            struct.pack_into(">Q", new_offsets, 8 + index * 8, value)
        else:
            struct.pack_into(">I", new_offsets, 8 + index * 4, value)
    offsets.name = b"co64" if offset_width == 8 else b"stco"
    _slsr_set_data(offsets, new_offsets)

    stsc_payload = stsc.data[8:]

    def rebuilt_stsc(description):
        return (_slsr_p32s(0, runs + 2) + stsc_payload
                + _slsr_p32s(chunks + 1, tail_extra, 1, chunks + 2, 8, description))

    _slsr_set_data(stsc, rebuilt_stsc(1))
    clone = audio.__deepcopy__({})
    _slsr_set_data(stsc, rebuilt_stsc(2))
    _slsr_set_data(stsd, patched_stsd)

    def track_id(item):
        tkhd_box = _slsr_child(item, b"tkhd")
        return _slsr_u32(tkhd_box.data, 20 if tkhd_box.data[0] == 1 else 12)

    ids = [track_id(track) for track in tracks]
    assert all(ids) and len(set(ids)) == len(ids)
    next_id = max(ids) + 1
    clone_tkhd = _slsr_child(clone, b"tkhd")
    _slsr_write_int(clone_tkhd, 20 if clone_tkhd.data[0] == 1 else 12, 4, next_id)
    next_offset = 108 if mvhd.data[0] == 1 else 96
    previous_next = _slsr_u32(mvhd.data, next_offset)
    _slsr_write_int(mvhd, next_offset, 4, max(next_id + 1, 0 if previous_next == 0xFFFFFFFF else previous_next))
    if (movie_header["duration"] > 0
            and not (movie_header["width"] == 4 and movie_header["duration"] == 0xFFFFFFFF)):
        longest = movie_header["duration"]
        for track in tracks:
            fields = _slsr_header_fields(_slsr_child(track, b"tkhd"))
            if fields["duration"] > 0 and not (
                    fields["width"] == 4 and fields["duration"] == 0xFFFFFFFF):
                longest = max(longest, fields["duration"])
        _slsr_write_int(mvhd, movie_header["offset"], movie_header["width"], longest)

    root.children.insert(root.children.index(audio) + 1, clone)
    silent = _SLSR_SILENT_FRAME * tail_frames
    output = []
    for item in tops:
        if item is moov:
            output.append(_slsr_p32(item["size"]) + b"free" + data[item["at"] + 8:item["at"] + item["size"]])
        elif item["zero"]:
            assert item["size"] <= 0xFFFFFFFF
            output.append(_slsr_p32(item["size"]) + data[item["at"] + 4:item["at"] + item["size"]])
        else:
            output.append(data[item["at"]:item["at"] + item["size"]])
    output.append(_slsr_box(b"mdat", silent))
    output.append(_slsr_serialize_box(root))
    return b"".join(output)

# obx method: rewrites or appends the four-sample audio tail and rebuilds the movie box.

def _obx_ct(rate_index):
    bits = []

    def add(value, width):
        bits.append(format(value, f"0{width}b"))

    for value, width in [
        (5, 3), (0, 4), (1, 2), (rate_index, 4), (0, 4), (0, 4), (0, 4),
        (2, 2), (0, 3), (0, 4), (0, 1), (0, 1), (0, 1), (2, 4), (5, 4),
    ]:
        add(value, width)
    text = "".join(bits)
    text = text.ljust(math.ceil(len(text) / 8) * 8, "0")
    text += "0" * 8
    for a in (2, 5):
        for value, width in [(3, 3), (a, 4), (0, 8), (0, 11), (0, 3)]:
            text += format(value, f"0{width}b")
    text += format(7, "03b")
    text = text.ljust(math.ceil(len(text) / 8) * 8, "0")
    return bytes(int(text[i:i + 8], 2) for i in range(0, len(text), 8))


def _obx_dt(t):
    e = _slsr_child(t, b"stsd").data
    assert len(e) >= 44 and _slsr_u32(e, 0) == 0 and _slsr_u32(e, 4) == 1
    n = e[8:]
    assert _slsr_u32(n, 0) == len(n) and _slsr_fourcc(n[4:8]) == b"mp4a"
    assert n[16] == 0 and n[17] == 0
    assert n[24] == 0 and n[25] in (1, 2)
    a = _slsr_parse_boxes(n[36:])
    assert not any(x.name in (b"wave", b"sinf") for x in a)
    r = [x for x in a if x.name == b"esds"]
    assert len(r) == 1 and _slsr_u32(r[0].data, 0) == 0
    o = r[0].data
    c = _slsr_descriptor(o, 4)
    assert c[0] == 3 and o[c[1] + 2] == 0
    found = []
    pos = c[1] + 3
    while pos < c[2]:
        d = _slsr_descriptor(o[:c[2]], pos)
        if d[0] == 4:
            assert d[2] - d[1] >= 13 and o[d[1]] == 64
            inner = d[1] + 13
            while inner < d[2]:
                g = _slsr_descriptor(o[:d[2]], inner)
                if g[0] == 5:
                    found.append(o[g[1]:g[2]])
                inner = g[2]
        pos = d[2]
    assert len(found) == 1
    p = found[0]
    assert len(p) >= 2
    rate_index = ((p[0] & 7) << 1) | (p[1] >> 7)
    assert p[0] >> 3 == 2 and rate_index < len(_SLSR_AAC_RATES)
    assert ((p[1] >> 3) & 15) in (1, 2) and (p[1] & 7) == 0
    bits = "".join(format(b, "08b") for b in p[2:])
    assert "1" not in bits or re.fullmatch(r"01010110111001010*", bits)
    return rate_index


def _obx_at(t):
    e = _slsr_find_child(t, b"stsz") or _slsr_find_child(t, b"stz2")
    assert e and not (_slsr_find_child(t, b"stsz") and _slsr_find_child(t, b"stz2"))
    n = e.data
    assert len(n) >= 12 and n[0] == 0
    count = _slsr_u32(n, 8)
    assert 4 < count <= 5_000_000
    if e.name == b"stsz":
        uniform = _slsr_u32(n, 4)
        assert len(n) == 12 + (0 if uniform else count * 4)
        values = [uniform or _slsr_u32(n, 12 + 4 * i) for i in range(count)]
    else:
        width = n[7]
        assert width in (4, 8, 16) and len(n) == 12 + math.ceil(count * width / 8)
        if width == 4:
            values = [(n[12 + (i >> 1)] >> (0 if i % 2 else 4)) & 15 for i in range(count)]
        elif width == 8:
            values = [n[12 + i] for i in range(count)]
        else:
            values = [_slsr_u32(n, 12 + i * 2) & 0xFFFF for i in range(count)]
    assert all(values)
    return {"box": e, "values": values}


def _obx_rt(t, sizes, mdats):
    a = _slsr_find_child(t, b"stco") or _slsr_find_child(t, b"co64")
    assert a and not (_slsr_find_child(t, b"stco") and _slsr_find_child(t, b"co64"))
    width = 4 if a.name == b"stco" else 8
    chunks = _slsr_table_count(a, width)
    c = _slsr_child(t, b"stsc")
    runs = [
        [_slsr_u32(c.data, 8 + 12 * i), _slsr_u32(c.data, 12 + 12 * i), _slsr_u32(c.data, 16 + 12 * i)]
        for i in range(_slsr_table_count(c, 12))
    ]
    assert chunks and runs and runs[0][0] == 1 and runs[-1][0] <= chunks
    assert all(per > 0 and desc == 1 and (i == 0 or first > runs[i - 1][0])
               for i, (first, per, desc) in enumerate(runs))
    run = 0
    sampled = 0
    result = []
    for index in range(chunks):
        if run + 1 < len(runs) and runs[run + 1][0] <= index + 1:
            run += 1
        per = runs[run][1]
        offset = _slsr_read_int(a.data, 8 + index * width, width)
        assert sampled + per <= len(sizes)
        length = sum(sizes[sampled:sampled + per])
        assert any(offset >= m["at"] + m["width"] and offset + length <= m["at"] + m["size"]
                   for m in mdats)
        result.append({"offset": offset, "first": sampled, "count": per, "length": length})
        sampled += per
    assert sampled == len(sizes)
    return {"offsets": a, "sc": c, "list": result}


def patch_bytes_obx(data, comment=WATERMARK):
    tops = []
    pos = 0
    while pos < len(data):
        name, header, size, zero = _slsr_read_header(data[pos:pos + 16], len(data) - pos)
        assert len(tops) < 10000 and name not in (b"moof", b"mfra", b"sidx", b"ssix")
        tops.append({"name": name, "at": pos, "size": size, "width": header, "zero": zero})
        pos += size
    moovs = [x for x in tops if x["name"] == b"moov"]
    mdats = [x for x in tops if x["name"] == b"mdat"]
    assert len(moovs) == 1 and mdats and moovs[0]["size"] <= 256 * 1024 * 1024
    moov = moovs[0]
    root = _slsr_parse_boxes(data[moov["at"]:moov["at"] + moov["size"]])[0]
    forbidden = {b"mvex", b"pssh", b"senc", b"saio", b"saiz", b"iloc", b"encv", b"enca", b"tenc", b"sinf"}

    def check(item):
        assert item.name not in forbidden
        for c in item.children or []:
            check(c)

    check(root)
    tracks = [x for x in root.children if x.name == b"trak"]

    def handler(track):
        return _slsr_child(_slsr_child(track, b"mdia"), b"hdlr").data[8:12]

    audio = [x for x in tracks if handler(x) == b"soun"]
    assert audio and any(handler(x) == b"vide" for x in tracks)
    ids = []
    for track in tracks:
        t = _slsr_child(track, b"tkhd").data
        assert t[0] in (0, 1)
        ids.append(_slsr_u32(t, 20 if t[0] == 1 else 12))
    assert all(ids) and len(set(ids)) == len(ids)
    g = audio[0]
    v = _slsr_child(_slsr_child(g, b"mdia"), b"minf")
    dref = _slsr_child(_slsr_child(v, b"dinf"), b"dref").data
    refs = _slsr_parse_boxes(dref[8:])
    assert _slsr_u32(dref, 0) == 0 and _slsr_u32(dref, 4) == len(refs) and refs
    assert all(x.name == b"url " and _slsr_u32(x.data, 0) == 1 for x in refs)
    stbl = _slsr_child(v, b"stbl")
    size_table = _obx_at(stbl)
    sizes_box, sizes = size_table["box"], size_table["values"]
    runs = _obx_rt(stbl, sizes, mdats)
    tail = _obx_ct(_obx_dt(stbl))
    last = len(sizes) - 4
    existing = []
    for chunk in runs["list"]:
        offset = chunk["offset"]
        for index in range(chunk["first"], chunk["first"] + chunk["count"]):
            if index >= last:
                existing.append((offset, sizes[index]))
            offset += sizes[index]
    assert len(existing) == 4
    in_place = all(length >= len(tail) for _, length in existing)
    if in_place:
        for track in tracks:
            if track is g:
                continue
            try:
                m = _slsr_child(_slsr_child(_slsr_child(track, b"mdia"), b"minf"), b"stbl")
                other = _obx_at(m)["values"]
                other_runs = _obx_rt(m, other, mdats)["list"]
                if any(a < b["offset"] + b["length"] and b["offset"] < a + length
                       for a, length in existing for b in other_runs):
                    in_place = False
            except Exception:
                in_place = False
    edits = []
    extra_mdat = None
    if in_place:
        for offset, length in existing:
            edits.append((offset, offset + length, tail + bytes(length - len(tail))))
    else:
        kept = []
        for chunk in runs["list"]:
            count = min(chunk["count"], max(0, last - chunk["first"]))
            if count:
                kept.append({"offset": chunk["offset"], "count": count})
        kept.append({"offset": len(data) + 8, "count": 4})
        wide = runs["offsets"].name == b"co64" or any(x["offset"] > 0xFFFFFFFF for x in kept)
        runs["offsets"].name = b"co64" if wide else b"stco"
        _slsr_set_data(runs["offsets"], _slsr_p32s(0, len(kept)) + b"".join(
            (_slsr_p64 if wide else _slsr_p32)(x["offset"]) for x in kept
        ))
        groups = []
        for index, item in enumerate(kept):
            if not groups or groups[-1][1] != item["count"]:
                groups.append([index + 1, item["count"], 1])
        _slsr_set_data(runs["sc"], _slsr_p32s(0, len(groups)) + b"".join(
            _slsr_p32s(*group) for group in groups
        ))
        sizes_box.name = b"stsz"
        new_sizes = bytearray(12 + 4 * len(sizes))
        struct.pack_into(">I", new_sizes, 8, len(sizes))
        for index, value in enumerate(sizes):
            struct.pack_into(">I", new_sizes, 12 + 4 * index, value if index < last else len(tail))
        _slsr_set_data(sizes_box, new_sizes)
        extra_mdat = _slsr_box(b"mdat", tail * 4)

    udta = _slsr_find_child(root, b"udta")
    if udta is None:
        udta = _SlSrBox(b"udta", b"", None, None)
        root.children.append(udta)
    existing_udta = udta.data
    hdlr = _slsr_box(b"hdlr", _slsr_p32s(0, 0) + b"mdir" + bytes(12) + b"\x00")
    comment_box = _slsr_box(b"\xa9cmt", _slsr_box(b"data", _slsr_p32s(1, 0) + comment.encode("utf-8")))
    _slsr_set_data(udta, existing_udta + _slsr_box(b"meta", _slsr_p32s(0) + hdlr + _slsr_box(b"ilst", comment_box)))

    edits.append((moov["at"] + 4, moov["at"] + 8, b"free"))
    for item in tops:
        if item["zero"]:
            assert item["size"] <= 0xFFFFFFFF
            edits.append((item["at"], item["at"] + 4, _slsr_p32(item["size"])))
    edits.sort(key=lambda x: x[0])
    output = []
    cursor = 0
    for start, end, replacement in edits:
        assert start >= cursor
        output.append(data[cursor:start])
        output.append(replacement)
        cursor = end
    output.append(data[cursor:])
    if extra_mdat is not None:
        output.append(extra_mdat)
    output.append(_slsr_serialize_box(root))
    return b"".join(output)


def patch_mp4(input_path: str, cfg: dict, source_path: str = None) -> bool:
    method = cfg.get("method", "obx")
    if method == "obx":
        return patch_mp4_obx(input_path, cfg)
    if method == "slsr":
        return patch_mp4_slsr(input_path, cfg)
    if method == "mvhd":
        return patch_mp4_mvhd(input_path, cfg)
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

    if source_path and not cfg.get("re_encode", True):
        data = copy_avcc_from_source(source_path, data)

    # detect video codec and set appropriate dummy size
    video_codec = detect_video_codec(data)
    global STSZ_INFLATE_FACTOR, DUMMY_SAMPLE_SIZE
    STSZ_INFLATE_FACTOR = cfg.get("inflation_rate", DEFAULTS["inflation_rate"])
    DUMMY_SAMPLE_SIZE = CODEC_DUMMY_SIZES.get(video_codec, 8)
    print(f"       \033[1mcodec\033[0m: {video_codec.decode('ascii', errors='replace')} (dummy={DUMMY_SAMPLE_SIZE})")

    print("       \033[1m[1/7]\033[0m parsing box structure...")
    top_boxes = parse_boxes(data, 0, len(data))

    ftyp_box = moov_box = mdat_box = None
    for box in top_boxes:
        if box["type"] == b'ftyp': ftyp_box = box
        elif box["type"] == b'moov': moov_box = box
        elif box["type"] == b'mdat': mdat_box = box

    if not moov_box or not mdat_box:
        print("       ERROR: missing moov or mdat, aborting")
        return False

    print("       \033[1m[2/7]\033[0m reconstructing layout (ftyp → moov → mdat)...")
    ftyp_data = data[ftyp_box["offset"]:ftyp_box["end"]] if ftyp_box else b''
    moov_data = bytearray(data[moov_box["offset"]:moov_box["end"]])

    mdat_header_size = 16 if read_u32be(data, mdat_box["offset"]) == 1 else 8
    mdat_payload = data[mdat_box["offset"] + mdat_header_size:mdat_box["end"]]
    mdat_data = build_box(b'mdat', mdat_payload)

    print("       \033[1m[3/7]\033[0m patching moov...")
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

    new_moov_children = []
    for i, child in enumerate(moov_children):
        if child["type"] == b'mvhd':
            mvhd = bytearray(moov_data[child["offset"]:child["end"]])
            mvhd = upgrade_mvhd_to_v1(mvhd)
            new_moov_children.append(bytes(mvhd))
        elif child["type"] == b'trak':
            trak_data = bytearray(moov_data[child["offset"]:child["end"]])
            is_tmcd = (i == tmcd_trak_idx)
            is_audio = (i == audio_trak_idx)
            is_video = (i == video_trak_idx)
            if is_audio:
                primary = patch_trak(trak_data, is_video, True, inflate=False, track_id=2, is_clone=False)
                new_moov_children.append(bytes(primary))
                clone = bytearray(trak_data)
                clone_patched = patch_trak(clone, False, True, inflate=True, track_id=4, is_clone=True)
                new_moov_children.append(bytes(clone_patched))
            elif is_tmcd:
                patched = patch_trak(trak_data, False, False, inflate=False, track_id=3, is_clone=False)
                new_moov_children.append(bytes(patched))
            else:
                patched = patch_trak(trak_data, is_video, False, inflate=False, track_id=1, is_clone=False)
                new_moov_children.append(bytes(patched))
        elif child["type"] == b'udta':
            continue
        else:
            new_moov_children.append(bytes(moov_data[child["offset"]:child["end"]]))

    combined_udta = build_combined_udta(cfg)
    new_moov_children.append(combined_udta)

    moov_payload = b''.join(new_moov_children)
    new_moov = build_box(b'moov', moov_payload)

    print("       \033[1m[4/7]\033[0m assembling output...")
    output_data = bytearray(ftyp_data) + bytearray(new_moov) + mdat_data

    print("       \033[1m[5/7]\033[0m fixing chunk offsets...")
    new_mdat_offset = len(ftyp_data) + len(new_moov) + 8
    old_mdat_payload_offset = mdat_box["offset"] + mdat_header_size
    offset_delta = new_mdat_offset - old_mdat_payload_offset
    if offset_delta != 0:
        fix_chunk_offsets(output_data, offset_delta)

    print("       \033[1m[6/7]\033[0m post-patch fixes...")
    zero_mp4a_samplerate(output_data)
    if source_path:
        spoof_audio_bitrate(output_data, source_path)
        fix_btrt_from_source(source_path, output_data)
    output_data = strip_free_boxes(output_data)

    print("       \033[1m[7/7]\033[0m appending trailing data...")
    garbage = build_trailing_garbage(cfg["trailing_bytes"])
    output_data += garbage

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
               inflate: bool = False, track_id: int = 0, is_clone: bool = False) -> bytearray:
    trak_children = parse_boxes(trak_data, 8, len(trak_data))
    new_children = []

    for tc in trak_children:
        if tc["type"] == b'tkhd':
            tkhd = bytearray(trak_data[tc["offset"]:tc["end"]])
            ver = tkhd[8]
            if ver == 0:
                write_u32be(tkhd, 12, 0)
                write_u32be(tkhd, 16, 0)
            else:
                write_u64be(tkhd, 12, 0)
                write_u64be(tkhd, 20, 0)
            if track_id > 0:
                tid_off = 20 if ver == 0 else 28
                write_u32be(tkhd, tid_off, track_id)
            new_children.append(bytes(tkhd))
        elif tc["type"] == b'tref':
            if is_clone:
                continue
            new_children.append(bytes(trak_data[tc["offset"]:tc["end"]]))
        elif tc["type"] == b'edts':
            if is_clone:
                continue
            if is_video:
                edts_data = bytearray(trak_data[tc["offset"]:tc["end"]])
                edts_data = patch_elst_plus_one(edts_data)
                new_children.append(bytes(edts_data))
            else:
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
            mdhd = bytearray(mdia_data[mc["offset"]:mc["end"]])
            ver = mdhd[8]
            if ver == 0:
                write_u32be(mdhd, 12, 0)
                write_u32be(mdhd, 16, 0)
            else:
                write_u64be(mdhd, 12, 0)
                write_u64be(mdhd, 20, 0)
            new_children.append(bytes(mdhd))
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

    new_sizes = list(original_sizes) + [dummy_size] * extra_count
    new_count = len(new_sizes)
    new_stsz_payload = struct.pack('>II', 0, new_count)
    for sz in new_sizes:
        new_stsz_payload += struct.pack('>I', sz)
    new_stsz = build_fullbox(b'stsz', 0, 0, new_stsz_payload)
    print(f"       \033[1mstsz\033[0m (clone): {real_count} → {new_count} (dummy={dummy_size})")

    orig_stts_pay = stbl_data[stts_box["offset"] + 12:stts_box["end"]]
    orig_tc = read_u32be(orig_stts_pay, 0) if len(orig_stts_pay) >= 4 else 0
    ext_payload = struct.pack('>I', orig_tc + 1) + orig_stts_pay[4:]
    ext_payload += struct.pack('>II', extra_count, 1)
    new_stts = build_fullbox(b'stts', 0, 0, ext_payload)

    new_stsc_entries = list(stsc_entries)
    if extra_count > 0:
        new_stsc_entries.append((orig_chunks + 1, extra_count, last_sdi))
    new_stsc_payload = struct.pack('>I', len(new_stsc_entries))
    for fc, spc, sdi in new_stsc_entries:
        new_stsc_payload += struct.pack('>III', fc, spc, sdi)
    new_stsc = build_fullbox(b'stsc', 0, 0, new_stsc_payload)

    new_stco_offsets = list(stco_offsets)
    if extra_count > 0:
        new_stco_offsets.append(first_dummy_offset)
    new_stco_payload = struct.pack('>I', len(new_stco_offsets))
    for off in new_stco_offsets:
        new_stco_payload += struct.pack('>I', off)
    new_stco = build_fullbox(b'stco', 0, 0, new_stco_payload)

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


def encode_for_tiktok(input_path: str, output_path: str, crf: int = 18, codec: str = "h264") -> bool:
    if codec == "h264":
        video_args = [
            "-c:v", "libx264", "-preset", "medium",
            "-crf", str(crf), "-level", "4.2",
            "-pix_fmt", "yuv420p",
            "-g", "9999", "-bf", "0",
            "-force_key_frames", "0,9.1,18.2",
        ]
    else:
        video_args = [
            "-c:v", "libx265", "-preset", "medium",
            "-crf", str(crf), "-pix_fmt", "yuv420p10le",
            "-x265-params", "log-level=error",
            "-g", "9999", "-bf", "0",
        ]

    cmd = [
        "ffmpeg", "-i", input_path,
        *video_args,
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata:s:v", "0:s:v",
        "-map_metadata:s:a", "0:s:a",
        "-y", output_path
    ]

    print(f"       \033[3m{codec} crf{crf} yuv420p level4.2\033[0m\n")
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


def remux_for_tiktok(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart",
        "-metadata:s:v", "handler_name=VideoHandler",
        "-metadata:s:a", "handler_name=SoundHandler",
        "-map_metadata:s:v", "0:s:v",
        "-map_metadata:s:a", "0:s:a",
        "-avoid_negative_ts", "disabled",
        "-copyinkf",
        "-y", output_path
    ]
    print(f"       \033[3mremux passthrough\033[0m\n")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = result.stderr[-800:] if len(result.stderr) > 800 else result.stderr
        print(f"       ✗ {err}")
        return False
    if os.path.exists(output_path):
        mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"       ✓ \033[1mremuxed\033[0m ({mb:.1f} MB)")
        return True
    print(f"       ✗ output not created")
    return False


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

    cfg = get_config()
    direct = (
        cfg.get("method", "obx") in ("obx", "slsr", "mvhd")
        and Path(input_file).suffix.lower() == ".mp4"
    )

    if direct:
        print(f"\033[1m[1/1] Patching...\033[0m\n")
        shutil.copy2(input_file, output_file)
    else:
        print(f"\033[1m[1/2] Encoding...\033[0m\n")
        if not encode_for_tiktok(input_file, output_file, cfg.get("crf", 18), cfg.get("codec", "h264")):
            print("\n  ✗ encoding failed")
            sys.exit(1)
        print(f"\n\033[1m[2/2] Patching...\033[0m\n")

    global STSZ_INFLATE_FACTOR, DUMMY_SAMPLE_SIZE
    STSZ_INFLATE_FACTOR = cfg["inflation_rate"]
    DUMMY_SAMPLE_SIZE = cfg["dummy_sample_size"]

    if patch_mp4(output_file, cfg, input_file):
        print(f"\n  ✓ \033[1mdone\033[0m: {output_file}")
        return 0
    else:
        print(f"\n  ✗ patch failed (file may still work)")
        return 1


if __name__ == '__main__':
    sys.exit(main())
