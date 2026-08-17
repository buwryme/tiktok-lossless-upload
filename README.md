
<div align="center">

# TikTok Lossless Upload Patcher

**Bypass TikTok's server-side re-encoding and upload videos losslessly.**  
Fully open-source, local-only, and privacy-respecting alternative to closed-source web or browser extension patchers.

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Required-green?style=flat-square&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Working%20as%20of%20Aug%202026-brightgreen?style=flat-square)]()

</div>

---

## Why?

Existing tools like itzcrih's web patcher or Editing News' browser extensions are effective but come with significant drawbacks:

- **Closed source** — you cannot audit what they do to your files
- **Server-side processing** — your video is uploaded to a third-party server before being patched, raising serious privacy concerns
- **Proprietary watermarks** — some tools inject their own branding into your content

This project brings lossless uploading into the open, runs entirely on your machine, and lets you control your own metadata.

---

## Requirements

| Dependency | Minimum Version | Notes |
|---|---|---|
| Python | 3.10+ | Uses PEP 604 union type syntax (`X \| Y`) |
| FFmpeg | Any recent build | Must be available on system `$PATH` |

> ⚠️ **Browser Compatibility:** Should work on any browser. WebKit/iOS might fail.

---

## Usage

### Basic Patching

```bash
# Patch in place (outputs to video_tiktok.mp4)
python3 tiktok_lossless_patch.py video.mp4

# Specify a custom output filename
python3 tiktok_lossless_patch.py input.mp4 output.mp4
```

### Mobile Workaround

On Android, use **Firefox in Desktop Mode** (or any Gecko browser) to upload. This is likely to work, but is not guaranteed. iOS is currently untested and likely unsupported, due to Apple enforcing WebKit on every browser

### A note about 120 FPS / 4K...

It's best to post your video as 1080p60 at most. Higher resolutions may get re-encoded by TikTok. (still technically possible!)

---

## How It Works

TikTok's ingest pipeline checks MP4 sample table consistency to decide whether to re-encode. This tool exploits that check through coordinated container-level manipulations:

1. **Encodes** input to H.265 Main 10 Profile, yuv420ple, AAC 256kbps with bitrate values tuned to TikTok's expected ranges (up to 20K!)
2. **Inflates** the audio `stsz` (sample size) table by repeating entries 10×, creating a deliberate mismatch between declared and actual sample counts
3. **Rewrites** the audio `stts` (time-to-sample) table with specific entry values that maintain apparent duration while reinforcing the inconsistency
4. **Strips** timecode tracks (`tmcd`) and track references (`tref`) that can trigger re-encoding
5. **Injects** custom metadata under `moov/udta/meta/ilst` so you retain attribution without third-party watermarks (feel free to change the configurable metadata inside the script)

The result is a file that strict transcoders reject (falling back to passthrough) while lenient players like TikTok's mobile decoder handle normally.

---

## ⚠️ Disclaimer

- Published **strictly for educational and research purposes**.
- TikTok may patch this at any time. **Works as of August 2026.**
- The author may choose not to update this repository if TikTok changes their ingest pipeline.
- Always test with non-critical content first. Results may vary by account, region, and client version.
- Respect TikTok's Terms of Service. Use responsibly.

<div align="center">

**For legal/general inquiries about this repo, contact [@buwryy](https://discord.com) on Discord.**

</div>
