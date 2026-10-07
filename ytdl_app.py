#!/usr/bin/env python3
"""
YouTube Downloader — local web app.
Paste a YouTube URL, grab the direct stream (source) URLs, or download as MP4 / MP3.

Requirements: Python 3, yt-dlp (pip install yt-dlp), ffmpeg.
Run:  python3 ytdl_app.py
Open: http://127.0.0.1:8765
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("YTDL_PORT", "8765"))
DOWNLOAD_DIR = os.environ.get("YTDL_DIR") or os.path.join(os.path.expanduser("~"), "Downloads", "ytdl")
COOKIES_FILE = os.environ.get("YTDL_COOKIES")  # path to a cookies.txt exported from your browser
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def find_ffmpeg_dir():
    """Locate ffmpeg's directory. shutil.which misses it when the installer
    refreshed PATH after this process started (common with winget)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return os.path.dirname(os.path.abspath(exe))
    if os.name == "nt":
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                            "Microsoft", "WinGet", "Packages")
        try:
            for d in os.listdir(base):
                if d.startswith("Gyan.FFmpeg"):
                    for root, _dirs, files in os.walk(os.path.join(base, d)):
                        if "ffmpeg.exe" in files:
                            return root
        except OSError:
            pass
    return None

FFMPEG_DIR = find_ffmpeg_dir()

YT_RE = re.compile(
    r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch|shorts|live|embed)|youtu\.be/)[\w\-?=&%/.#;:@+~]+$"
)

BOT_CHECK = "Sign in to confirm you’re not a bot"

def base_args():
    args = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings"]
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        args += ["--cookies", COOKIES_FILE]
    return args

def run_capture(url, extra):
    """Run yt-dlp, capturing output. Falls back to the android player client
    when YouTube's bot check blocks the web client (quality caps ~720p then).
    Returns (CompletedProcess, limited: bool)."""
    attempts = [[], ["--extractor-args", "youtube:player_client=android"]]
    last = None
    for extra_client in attempts:
        proc = subprocess.run(
            base_args() + extra + extra_client + [url],
            capture_output=True, text=True, timeout=120)
        last = proc
        if proc.returncode == 0:
            return proc, bool(extra_client)
        if BOT_CHECK in (proc.stderr or "") and not extra_client:
            continue  # retry with android client
        break
    return last, True

def valid_url(url: str) -> bool:
    return bool(YT_RE.match((url or "").strip()))

def yt_dlp_json(url: str):
    proc, limited = run_capture(url, ["--dump-single-json"])
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "yt-dlp failed")[-800:])
    info = json.loads(proc.stdout)
    info["_limited_quality"] = limited
    return info

def run_yt_dlp(url: str, args: list) -> str:
    """Download to DOWNLOAD_DIR. Returns the downloaded file path."""
    outtmpl = os.path.join(DOWNLOAD_DIR, "%(title)s [%(id)s].%(ext)s")
    ffmpeg_args = ["--ffmpeg-location", FFMPEG_DIR] if FFMPEG_DIR else []
    proc, limited = run_capture(url, ["--restrict-filenames", "-o", outtmpl] + ffmpeg_args + args)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "download failed")[-800:])
    # Find newest file matching the video id
    vid = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(vid.query).get("v", [""])[0] or vid.path.strip("/").split("/")[-1]
    best, best_mtime = None, 0
    for f in os.listdir(DOWNLOAD_DIR):
        if q and q not in f:
            continue
        p = os.path.join(DOWNLOAD_DIR, f)
        m = os.path.getmtime(p)
        if m > best_mtime:
            best, best_mtime = p, m
    if not best:
        raise RuntimeError("yt-dlp finished but no file was found.")
    return best

def fmt_size(n):
    if not n:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

def fmt_duration(s):
    if s is None:
        return "—"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube Downloader</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:860px;margin:0 auto;padding:24px;color:#1c1c1e;background:#fff}
h1{font-size:1.6rem} input[type=text]{width:70%;padding:10px;font-size:1rem;border:1px solid #ccc;border-radius:8px}
button{padding:10px 16px;font-size:1rem;border:0;border-radius:8px;background:#0a84ff;color:#fff;cursor:pointer;margin-left:6px}
button.alt{background:#34c759} button.ghost{background:#f2f2f7;color:#0a84ff}
button:disabled{opacity:.5;cursor:wait}
.card{border:1px solid #e5e5ea;border-radius:12px;padding:16px;margin-top:18px;background:#fbfbfc}
.thumb{max-width:320px;border-radius:8px}
.meta{color:#555;margin:6px 0}
table{border-collapse:collapse;width:100%;margin-top:12px;font-size:.9rem}
th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}
.urlbox{font-family:monospace;font-size:.75rem;word-break:break-all;background:#f2f2f7;padding:6px;border-radius:6px;max-width:420px}
.err{color:#d70015;background:#fde8ea;padding:12px;border-radius:8px;margin-top:16px}
.note{color:#666;font-size:.85rem;margin-top:12px}
#status{margin-top:12px;color:#666}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
</style></head><body>
<h1>YouTube Downloader</h1>
<p class="note">Paste a link below. The download goes straight to your browser's download folder — nothing is left behind on this page's server.</p>
<form method="GET" action="/info">
<input type="text" name="url" placeholder="https://www.youtube.com/watch?v=…" required autofocus>
<button type="submit">Fetch</button>
</form>"""

PAGE_FOOT = """<script>
function copyUrl(btn){navigator.clipboard.writeText(btn.dataset.url).then(()=>{btn.textContent="Copied";setTimeout(()=>btn.textContent="Copy",1500)})}
function downloading(kind){const s=document.getElementById('status');s.textContent='Downloading '+kind.toUpperCase()+'… this can take a bit, hang tight.';document.querySelectorAll('button.dl').forEach(b=>b.disabled=true)}
</script></body></html>"""

def fmt_br(kbps):
    if not kbps:
        return "bitrate unknown"
    if kbps >= 1000:
        return f"~{kbps/1000:.1f} Mbps"
    return f"~{int(round(kbps))} kbps"

def stream_br(f, duration):
    t = f.get("tbr")
    if t:
        return t
    fs = f.get("filesize") or f.get("filesize_approx")
    if fs and duration:
        return fs * 8 / duration / 1000
    return f.get("abr") or f.get("vbr") or 0

def res_label(f):
    r = f.get("resolution")
    if r and r != "audio":
        return r.split("x")[-1] + "p" if "x" in r else r
    if f.get("height"):
        return f"{f['height']}p"
    return "audio"

def pick_streams(info):
    """Mirror the download buttons' format picks so the UI can show what
    bitrate each button will actually get."""
    fmts = [f for f in info.get("formats", []) if (f.get("url") or "").startswith("http")]
    dur = info.get("duration")
    vids = [f for f in fmts if (f.get("vcodec") or "none") != "none"]
    auds = [f for f in fmts if (f.get("vcodec") or "none") == "none"
            and (f.get("acodec") or "none") != "none"]

    def best_v(cands):
        c = [f for f in cands if f.get("height")]
        return max(c, key=lambda f: (f["height"], stream_br(f, dur) or 0), default=None)

    def best_a(cands):
        return max(cands, key=lambda f: stream_br(f, dur) or 0, default=None)

    # MP4 pick mirrors: bv*[ext=mp4]+ba*[ext=m4a] / b[ext=mp4] / bv*+ba* / b
    v = best_v([f for f in vids if f.get("ext") == "mp4"])
    a = best_a([f for f in auds if f.get("ext") == "m4a"])
    if v and a:
        mp4 = f'{res_label(v)} \u00b7 {fmt_br(stream_br(v, dur) + stream_br(a, dur))}'
    else:
        prog = [f for f in fmts if (f.get("vcodec") or "none") != "none"
                and (f.get("acodec") or "none") != "none" and f.get("ext") == "mp4"]
        p = best_v(prog)
        if p:
            mp4 = f'{res_label(p)} \u00b7 {fmt_br(stream_br(p, dur))}'
        else:
            v, a = best_v(vids), best_a(auds)
            mp4 = (f'{res_label(v)} \u00b7 {fmt_br(stream_br(v, dur) + stream_br(a, dur))}'
                   if v and a else "bitrate unknown")

    a = best_a(auds)
    if a:
        mp3 = f'from {fmt_br(stream_br(a, dur))} audio'
    else:
        # No audio-only streams (e.g. mobile player feed): audio comes from
        # the progressive stream, so name the source stream instead.
        prog = [f for f in fmts if (f.get("vcodec") or "none") != "none"
                and (f.get("acodec") or "none") != "none"]
        p = best_v(prog)
        mp3 = f'from {res_label(p)} stream audio' if p else "bitrate unknown"
    return mp4, mp3

def page_info(url, info):
    esc = html.escape
    title = info.get("title", "Untitled")
    thumb = info.get("thumbnail", "")
    rows = []
    for f in info.get("formats", []):
        furl = f.get("url") or ""
        if not furl.startswith("http"):
            continue
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        kind = "video+audio" if vcodec != "none" and acodec != "none" else ("video" if vcodec != "none" else "audio")
        res = f.get("resolution") or (f"{f.get('width')}x{f.get('height')}" if f.get("width") else "audio")
        rows.append((f.get("format_id"), f.get("ext"), kind, res, f.get("fps"),
                     f.get("filesize") or f.get("filesize_approx"), vcodec, acodec, furl))
    # best progressive / best audio for quick picks
    body = [PAGE_HEAD]
    if info.get("_limited_quality"):
        body.append('<div class="err">YouTube asked this network to sign in (bot check), '
                    'so streams are capped around 720p. For full quality, export your browser cookies '
                    'and set <code>YTDL_COOKIES=/path/to/cookies.txt</code> — see README.</div>')
    mp4_info, mp3_info = pick_streams(info)
    body.append(f'<div class="card"><div class="row">')
    if thumb:
        body.append(f'<img class="thumb" src="{esc(thumb)}">')
    body.append(f'<div><h2 style="margin:0">{esc(title)}</h2>'
                f'<div class="meta">{esc(str(info.get("uploader") or ""))} · {fmt_duration(info.get("duration"))}</div>'
                f'<div class="row" style="margin-top:10px">'
                f'<div><a href="/download?kind=mp4&url={urllib.parse.quote(url, safe="")}"><button class="alt dl" onclick="downloading(\'mp4\')">Download MP4</button></a>'
                f'<div class="note" style="margin:4px 0 0">{esc(mp4_info)}</div></div>'
                f'<div><a href="/download?kind=mp3&url={urllib.parse.quote(url, safe="")}"><button class="dl" onclick="downloading(\'mp3\')">Download MP3</button></a>'
                f'<div class="note" style="margin:4px 0 0">{esc(mp3_info)}</div></div>'
                f'</div><div id="status"></div></div></div></div>')
    body.append('<div class="card"><h3>Direct source URLs</h3>'
                '<p class="note">Right-click copy or hit Copy. These expire, so use them soon.</p>'
                '<table><tr><th>ID</th><th>Format</th><th>Type</th><th>Size</th><th>Stream URL</th></tr>')
    for fid, ext, kind, res, fps, size, vcodec, acodec, furl in rows:
        detail = f"{ext} · {res}" + (f" · {fps}fps" if fps else "") + f" · {kind}"
        body.append(f'<tr><td>{esc(str(fid))}</td><td>{esc(detail)}</td><td>{esc(kind)}</td>'
                    f'<td>{fmt_size(size)}</td>'
                    f'<td><div class="urlbox">{esc(furl[:140])}{"…" if len(furl) > 140 else ""}</div>'
                    f'<button class="ghost" data-url="{esc(furl)}" onclick="copyUrl(this)">Copy</button></td></tr>')
    body.append("</table></div>")
    body.append(PAGE_FOOT)
    return "".join(body)

class Handler(BaseHTTPRequestHandler):
    server_version = "YTDL/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        url = (q.get("url") or [""])[0].strip()
        try:
            if parsed.path in ("/", ""):
                return self._send(200, PAGE_HEAD + PAGE_FOOT)
            if parsed.path == "/info":
                if not valid_url(url):
                    return self._send(400, PAGE_HEAD + '<div class="err">That doesn\'t look like a YouTube URL.</div>' + PAGE_FOOT)
                info = yt_dlp_json(url)
                return self._send(200, page_info(url, info))
            if parsed.path == "/download":
                kind = (q.get("kind") or ["mp4"])[0]
                if not valid_url(url) or kind not in ("mp4", "mp3"):
                    return self._send(400, PAGE_HEAD + '<div class="err">Bad request.</div>' + PAGE_FOOT)
                if kind == "mp3":
                    path = run_yt_dlp(url, ["-x", "--audio-format", "mp3", "--audio-quality", "0"])
                    mime, dlname = "audio/mpeg", os.path.basename(path)
                else:
                    path = run_yt_dlp(url, ["-f", "bv*[ext=mp4]+ba*[ext=m4a]/b[ext=mp4]/bv*+ba*/b",
                                            "--merge-output-format", "mp4"])
                    mime, dlname = "video/mp4", os.path.basename(path)
                size = os.path.getsize(path)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", f'attachment; filename="{dlname}"')
                self.end_headers()
                try:
                    with open(path, "rb") as fh:
                        while True:
                            chunk = fh.read(1024 * 1024)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                finally:
                    # The browser has its own copy now; don't leave a second
                    # one behind in the download folder.
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                return
            return self._send(404, PAGE_HEAD + '<div class="err">Not found.</div>' + PAGE_FOOT)
        except subprocess.TimeoutExpired:
            return self._send(504, PAGE_HEAD + '<div class="err">Timed out talking to YouTube. Try again.</div>' + PAGE_FOOT)
        except Exception as e:
            return self._send(500, PAGE_HEAD + f'<div class="err">Error: {html.escape(str(e)[:600])}</div>' + PAGE_FOOT)

def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"YouTube Downloader running → http://127.0.0.1:{PORT}")
    print(f"Files save to: {DOWNLOAD_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")

if __name__ == "__main__":
    main()
