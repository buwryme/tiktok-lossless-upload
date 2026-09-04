<div align="center">

# tiktok-lossless-upload

bypasses tiktok server-side re-encoding, so your uploads stay uncompressed and as-is.

[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## requirements
- python 3.10+
- ffmpeg (in `$PATH`)

## usage

```bash
python3 tiktok_patch.py input.mp4 [output.mp4]
```

encodes to **h.264 crf 18** (visually lossless) + patches container structure to force passthrough.

> **tip:** supports up to 4K@60FPS.

### custom metadata

override defaults via the `patch_video()` api:

```python
from tiktok_patch import patch_video

patch_video("input.mp4", {
    "comment": "my custom tag",
    "name_box_payload": "anything-here",
    "inflation_rate": 9,
})
```

all fields (`encoder`, `comment`, `comment_short`, `name_box_payload`, `inflation_rate`, `dummy_sample_size`, `trailing_bytes`) are configurable.

## gui

requires `PyGObject`. run from the same directory as the script.

```bash
python3 app.py
```

check out [tikutils](https://github.com/buwryme/tikutils) for an installable app!

## how it works

1.  encodes video/audio with optimized ffmpeg settings.
2.  duplicates audio track and inflates its `stsz` table with constant-size dummy samples.
3.  strips `tmcd`/`tref` tracks and normalizes handler names.
4.  injects dual `meta` boxes with custom metadata and an unknown `name` box.
5.  appends a `VOID` box and repeating trailing pattern bytes.

tiktok's transcoders choke on the structural mismatch and skip re-encoding. mobile decoders ignore the dummy track and play normally.

---

> works as of sep 2026. use at your own risk.

for inquiries contact **@buwryy** on discord
