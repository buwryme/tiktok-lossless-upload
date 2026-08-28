
<div align="center">

# tiktok-lossless-upload

bypasses tiktok server-side re-encoding, so your uploads stay uncompressed and as-is

[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

</div>

---

## requirements
- python 3.10+
- ffmpeg (in `$PATH`)

## usage

```bash
python3 tiktok_lossless_patch.py input.mp4 [output.mp4]
```

encodes to **h.264 crf 18** (visually lossless) + patches container structure to force passthrough.

> **tip:** your video is best up to 1080p60. other formats may fail

## gui

requires `PyGObject`. run from the same directory as the script.

```bash
python3 app.py
```

check out [tikutils](https://github.com/buwryme/tikutils) for an installable app!

## how it works

1. encodes video/audio with optimized ffmpeg settings.
2. inflates audio `stsz` table to create a mismatch.
3. strips `tmcd`/`tref` tracks.
4. injects custom metadata.

tiktok's strict transcoders choke on the mismatch and skip re-encoding. mobile decoders ignore it and play normally.


---

> works as of aug 2026. use at your own risk.

for inquiries contact **@buwryy** on discord
