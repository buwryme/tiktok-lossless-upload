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

for MP4 input, the default method copies the file and patches the container directly. no re-encode.

```bash
python3 tiktok_lossless_patch.py input.mp4
```

the default method needs a video track and at least one AAC audio track. other inputs are converted first.

```bash
python3 tiktok_lossless_patch.py input.mov output.mp4
```

to skip re-encoding non-MP4 input, call the Python API with `re_encode` disabled:

```bash
python3 -c "from tiktok_lossless_patch import patch_video; patch_video('input.mov', {'re_encode': False})"
```

> **tip:** supports *any* video resolution, but up to 1080p60fps is best

## gui

requires `PyGObject`. run from the same directory as the script.

```bash
python3 app.py
```

check out [tikutils](https://git.buwryy.net/buwryy/tikutils) for an installable app!

## how it works

the default method is **obx**:

1.  requires a non-fragmented MP4 with a video track and at least one AAC audio track.
2.  reads the last four audio samples. if they are large enough, it rewrites them with the method's audio tail; otherwise it appends a new media box with four tail samples and updates the audio tables.
3.  renames the original movie box to a free box and appends a rebuilt movie box.
4.  adds a `©cmt` comment with the project watermark.

the video samples are copied without re-encoding. the original media stays in place, so existing chunk offsets still resolve.

the older silent-tail method is available with `method = "slsr"`, the header-only method with `method = "mvhd"`, and the audio-inflation method with `method = "legacy"`.

## configuration

patcher tuning (method, inflation rate, codec-specific dummy sizes, metadata, and trailing payload) is fetched from a public `.toml` gist. if the fetch fails, the built-in copy is used. encode settings remain available in the gui and cli.

---

for inquiries contact **@buwryy** on discord
