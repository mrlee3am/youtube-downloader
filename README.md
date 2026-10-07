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

1. Paste any YouTube URL (`youtube.com/watch`, `youtu.be`, Shorts, live, music links all work).
2. Hit **Fetch** — you'll see the title, thumbnail, one-click **Download MP4** / **Download MP3** buttons (each shows the resolution and bitrate you'll actually get), and a table of **direct source URLs** for every stream (format ID, resolution, size, copyable link).
3. Clicking a download button fetches and converts the file, then your browser saves it to its usual download folder — the app cleans up after itself, so there's only ever the one copy.

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
