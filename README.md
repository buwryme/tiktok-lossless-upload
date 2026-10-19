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

1.  encodes or remuxes video/audio with optimized ffmpeg settings.
2.  removes all `udta` metadata and writes one `©too` watermark.
3.  rebuilds the video sample table with codec-sized dummy samples and updates `stts`, `stsc`, `stsz`, and `stco`.
4.  rewrites the `btrt` bitrate fields from the real sample sizes.
5.  moves `moov` before `mdat` (faststart) and appends the dummy sample payload.

tiktok's transcoders choke on the structural mismatch and skip re-encoding. mobile decoders ignore the dummy samples and play normally.

## configuration

patcher tuning (sample multiplier, dummy sample sizes and payloads for every codec, watermark, and layout flags) is fetched from a public `.toml` gist. if the fetch fails, the built-in copy is used. encode settings remain available in the gui and cli.

---

for inquiries contact **@buwryy** on discord
