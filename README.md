# Video Patcher for TikTok to allow lossless uploading.
Patch your videos to upload them to TikTok without TikTok re-encoding them (lossless), but now fully open source.

### Why?

Methods like itzcrih's on-website video patching or Editing News' extensions are highly credible for being functional, BUT:
- They are closed source.
- Their processing is done on their servers, which raises privacy concerns.

This simple project brings lossless uploading into an open source manner.

### Usage:
#### Requirements:
- Python 3.6+. No pip packages required.

```py
# Patch in place (overwrites original)
python3 tiktok_lossless_patch.py video.mp4

# Or save to new file
python3 tiktok_lossless_patch.py input.mp4 output.mp4
```

After patching, upload the result into TikTok via any Chrome-based browser on **desktop**. No extra steps from here.

For mobile, using Microsoft Edge & going into Desktop Mode and proceeding to upload it into TikTok might do.

### How it works
- Inflates ELST boxes from 0x1 to 0x10000001 (thanks to MASKA's extension!)
- Inflates sample boxes (thanks to `irgifebry/NoBlur`!)

### ⚠️ Disclaimer
- Published **strictly** for educational purposes.
- TikTok may patch this at anytime. (Works as of *August 10th, 2026*)
- Additionally to what's said above, I may choose to not update this repo to keep up with their patches.
- Works only on Chrome-based browsers (Gecko/Firefox fails to process the cover due to ELST entry count patching, which makes the Post button grayed out)

### Special Thanks
- MASKA's OSS browser extension for ELST box inflation technique
- irgifebry's NoBlur repo for sample table inflation technique

> For legal inquiries, contact @buwryy on Discord.
