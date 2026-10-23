<div align="center">

# tiktok-lossless-upload

bypasses tiktok server-side re-encoding, so your uploads stay uncompressed and as-is.

[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
![Patcher Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fbuwryy.net%2Fapi%2Fpatcher-status&style=flat-square)

</div>

---

Reverse engineered from Editing News' patcher and made open source.

## requirements
- python 3.10+
- ffmpeg (in `$PATH`)

## usage

```bash
python3 tiktok_lossless_patch.py input.mp4 [output.mp4]
```

encodes to **h.264 crf 18** (visually lossless) + patches the container structure to force passthrough.

```bash
python3 tiktok_lossless_patch.py input.mp4 --no-reencode
python3 tiktok_lossless_patch.py input.mp4 --codec h265 --crf 18
```

> **tip:** supports *any* video resolution, but up to 1080p60fps is best

## gui

requires `PyGObject`. run from the same directory as the script.

```bash
python3 app.py
```

check out [tikutils](https://github.com/buwryme/tikutils) for an installable app!

## how it works

the default method is **mvhd**:

1.  upgrades `mvhd` from version 0 to version 1, preserving creation/modification times, timescale, rate, volume, matrix, and next track ID.
2.  sets the `mvhd` duration to `0xFFFFFFFFFFFFFFFF`.
3.  appends or replaces a `©cmt` entry in `udta/meta/ilst` with `Patched by https://github.com/buwryme/tiktok-lossless-upload`.
4.  shifts `stco`/`co64` chunk offsets by the moov size change.

the `mdat` payload, sample tables, audio/video streams, and trailing data are left byte-for-byte untouched. the container rewrite matches the reference sample; only the project watermark differs.

the previous audio-inflation method is still available by setting `method = "legacy"` in the patcher config.

## configuration

patcher tuning (method, inflation rate, codec-specific dummy sizes, metadata, and trailing payload) is fetched from a public `.toml` gist. if the fetch fails, the built-in copy is used. encode settings remain available in the gui and cli.

---

for inquiries contact **@buwryy** on discord
