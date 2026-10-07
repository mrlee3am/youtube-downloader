# YouTube Downloader

A tiny local web app. Paste a YouTube link, see the direct stream (source) URLs, and download as **MP4** or **MP3**.

## Easiest way (Windows)

1. Put `ytdl_app.py`, `start.bat`, and `README.md` together in one folder.
2. **Double-click `start.bat`.**

That's it. The script checks for Python, yt-dlp, and ffmpeg, installs anything
missing automatically (via winget/pip), then launches the app and opens
http://127.0.0.1:8765 in your browser. Keep the black window open while you use it.

## Manual setup (any OS)

### Requirements

- Python 3 (3.9+)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp): `pip install yt-dlp` (or `pipx install yt-dlp`)
- [ffmpeg](https://ffmpeg.org/download.html) — needed for MP3 conversion and for merging MP4 streams
  - macOS: `brew install ffmpeg`
  - Windows: `winget install ffmpeg` (or grab a build from ffmpeg.org)
  - Linux: `sudo apt install ffmpeg`

### Run it

```bash
python3 ytdl_app.py
```

Then open **http://127.0.0.1:8765** in your browser.

## How to use

**Single video:** paste any YouTube URL (`youtube.com/watch`, `youtu.be`, Shorts, live, music links all work), hit **Fetch** — you'll see the title, thumbnail, one-click **Download MP4** / **Download MP3** buttons (each shows the resolution and bitrate you'll actually get), and a table of **direct source URLs** for every stream (format ID, resolution, size, copyable link). Downloads show a live progress bar while they run.

**Batch:** open the **Batch download** page, paste one URL per line, hit **Fetch all**. Every row shows its bitrates with its own MP4/MP3 buttons, or use **Download all as MP4 / MP3** to queue the whole list.

**Download folder:** set it on the home page — it's remembered between runs. Files download there and stay there; that's the one and only copy (the page downloads in the background, nothing extra lands in your browser's folder).

## Notes

- The server only listens on `127.0.0.1`, so it's only reachable from your own machine.
- Direct stream URLs expire (usually within a few hours) — copy and use them right away.
- Change the port with `YTDL_PORT=9000 python3 ytdl_app.py`, or the save folder with `YTDL_DIR=/path/to/dir`.

## Full quality (1080p/4K) and the YouTube bot check

YouTube sometimes asks datacenter or VPN connections to sign in ("Sign in to confirm
you're not a bot"). When that happens the app automatically falls back to YouTube's
mobile player feed, which caps streams around 720p — and tells you so on the page.

For full-quality streams:

1. Install the **"Get cookies.txt LOCALLY"** extension in your browser.
2. While logged in to YouTube, export cookies for youtube.com to a file.
3. Run the app with `YTDL_COOKIES=/path/to/youtube.com_cookies.txt python3 ytdl_app.py`.

- Download responsibly: respect creators' rights and YouTube's terms — this is meant for videos you're allowed to keep.
