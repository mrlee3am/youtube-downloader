#!/usr/bin/env python3
"""
YouTube Downloader — local web app.
Paste a YouTube URL, grab the direct stream (source) URLs, or download as MP4 / MP3.

Features:
- Single-video page: direct stream URLs + one-click MP4/MP3 (bitrate shown)
- Batch page: paste many URLs, see bitrates, download one by one or all at once
- Download folder is user-selectable and remembered; files are kept there.

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
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("YTDL_PORT", "8765"))
COOKIES_FILE = os.environ.get("YTDL_COOKIES")  # path to a cookies.txt exported from your browser

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "ytdl_config.json")

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    os.replace(tmp, CONFIG_PATH)

def get_download_dir():
    """User-chosen folder (remembered), else ~/Downloads/ytdl. Env override wins."""
    d = os.environ.get("YTDL_DIR") or load_config().get("download_dir")
    if not d:
        d = os.path.join(os.path.expanduser("~"), "Downloads", "ytdl")
    os.makedirs(d, exist_ok=True)
    return d

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
            capture_output=True, text=True, timeout=180)
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

def safe_info(url):
    """Returns (url, info or None, error or None). Never raises."""
    try:
        return url, yt_dlp_json(url), None
    except Exception as e:
        return url, None, str(e)[:200]

# ---------------- download jobs with live progress ----------------
# A download runs in a background thread; the browser polls /progress?job=ID.
jobs = {}
jobs_lock = threading.Lock()

def _prune_jobs():
    now = time.time()
    with jobs_lock:
        for jid in [k for k, v in jobs.items() if now - v.get("created", now) > 3600]:
            del jobs[jid]

def start_download_job(url: str, kind: str, download_dir: str) -> str:
    _prune_jobs()
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {"status": "starting", "pct": 0, "detail": "Starting…",
                        "created": time.time(), "filename": None, "error": None}
    t = threading.Thread(target=_download_worker,
                         args=(job_id, url, kind, download_dir), daemon=True)
    t.start()
    return job_id

def find_newest(download_dir: str, url: str):
    vid = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(vid.query).get("v", [""])[0] or vid.path.strip("/").split("/")[-1]
    best, best_mtime = None, 0
    for f in os.listdir(download_dir):
        if q and q not in f:
            continue
        p = os.path.join(download_dir, f)
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > best_mtime:
            best, best_mtime = p, m
    return best

def _run_streaming(url, extra, setj):
    """Run yt-dlp, feeding progress lines to setj(). Falls back to the android
    player client when YouTube's bot check blocks the web client.
    Returns (ok: bool, error: str|None)."""
    pct_re = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
    attempts = [[], ["--extractor-args", "youtube:player_client=android"]]
    for client_args in attempts:
        cmd = base_args() + extra + client_args + [url]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, errors="replace")
        killer = threading.Timer(1800, proc.kill)  # hard cap: 30 min
        killer.start()
        bot_hit, err_tail = False, []
        try:
            for line in proc.stdout:
                if BOT_CHECK in line:
                    bot_hit = True
                m = pct_re.search(line)
                if m:
                    setj(status="downloading", pct=float(m.group(1)), detail="Downloading…")
                elif line.startswith(("[ExtractAudio]", "[Merger]", "[VideoConvertor]",
                                       "[AudioConvertor]")):
                    setj(status="converting", detail="Converting…")
                s = line.strip()
                if s:
                    err_tail.append(s)
                    if len(err_tail) > 15:
                        err_tail.pop(0)
        finally:
            killer.cancel()
        proc.wait()
        if proc.returncode == 0:
            return True, None
        if bot_hit and not client_args:
            setj(detail="Retrying…")
            continue  # retry with android client
        return False, " ".join(err_tail)[-400:] or "yt-dlp failed"
    return False, "yt-dlp failed"

def _download_worker(job_id, url, kind, download_dir):
    def setj(**kw):
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id].update(kw)
    try:
        outtmpl = os.path.join(download_dir, "%(title)s [%(id)s].%(ext)s")
        ffmpeg_args = ["--ffmpeg-location", FFMPEG_DIR] if FFMPEG_DIR else []
        if kind == "mp3":
            fmt_args = ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            fmt_args = ["-f", "bv*[ext=mp4]+ba*[ext=m4a]/b[ext=mp4]/bv*+ba*/b",
                        "--merge-output-format", "mp4"]
        extra = ["--newline", "--restrict-filenames", "-o", outtmpl] + ffmpeg_args + fmt_args
        ok, err = _run_streaming(url, extra, setj)
        if not ok:
            setj(status="error", error=err)
            return
        path = find_newest(download_dir, url)
        if not path:
            setj(status="error", error="yt-dlp finished but no file was found")
            return
        setj(status="done", pct=100, detail="Saved", filename=os.path.basename(path), path=path)
    except Exception as e:
        setj(status="error", error=str(e)[:200])

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
        mp4 = f'{res_label(v)} · {fmt_br(stream_br(v, dur) + stream_br(a, dur))}'
    else:
        prog = [f for f in fmts if (f.get("vcodec") or "none") != "none"
                and (f.get("acodec") or "none") != "none" and f.get("ext") == "mp4"]
        p = best_v(prog)
        if p:
            mp4 = f'{res_label(p)} · {fmt_br(stream_br(p, dur))}'
        else:
            v, a = best_v(vids), best_a(auds)
            mp4 = (f'{res_label(v)} · {fmt_br(stream_br(v, dur) + stream_br(a, dur))}'
                   if v and a else "bitrate unknown")

    a = best_a(auds)
    if a:
        mp3 = f'from {fmt_br(stream_br(a, dur))} audio'
    else:
        prog = [f for f in fmts if (f.get("vcodec") or "none") != "none"
                and (f.get("acodec") or "none") != "none"]
        p = best_v(prog)
        mp3 = f'from {res_label(p)} stream audio' if p else "bitrate unknown"
    return mp4, mp3

# ---------------- pages ----------------

PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouTube Downloader</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:24px;color:#1c1c1e;background:#fff}
h1{font-size:1.6rem} input[type=text]{width:65%;padding:10px;font-size:1rem;border:1px solid #ccc;border-radius:8px}
textarea{width:100%;padding:10px;font-size:.95rem;border:1px solid #ccc;border-radius:8px;font-family:inherit}
button{padding:10px 16px;font-size:1rem;border:0;border-radius:8px;background:#0a84ff;color:#fff;cursor:pointer;margin:4px 6px 4px 0}
button.alt{background:#34c759} button.ghost{background:#f2f2f7;color:#0a84ff}
button:disabled{opacity:.5;cursor:wait}
.card{border:1px solid #e5e5ea;border-radius:12px;padding:16px;margin-top:18px;background:#fbfbfc}
.thumb{max-width:320px;border-radius:8px}
.meta{color:#555;margin:6px 0}
table{border-collapse:collapse;width:100%;margin-top:12px;font-size:.9rem}
th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}
.urlbox{font-family:monospace;font-size:.75rem;word-break:break-all;background:#f2f2f7;padding:6px;border-radius:6px;max-width:420px}
.err{color:#d70015;background:#fde8ea;padding:12px;border-radius:8px;margin-top:16px}
.ok{color:#0a6b2d;background:#e6f9ec;padding:12px;border-radius:8px;margin-top:16px}
.note{color:#666;font-size:.85rem;margin-top:12px}
.dlstatus{color:#666;font-size:.85rem;margin-top:6px;min-height:1.2em}
.pbar{height:10px;background:#e5e5ea;border-radius:5px;margin-top:8px;overflow:hidden;display:none;max-width:340px}
.pfill{height:100%;background:#0a84ff;width:0%;border-radius:5px;transition:width .3s}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
a{color:#0a84ff}
</style></head><body>
<h1>YouTube Downloader</h1>
<p class="note">Paste a link to grab its direct stream URLs, or download it as MP4 / MP3 straight into your download folder.</p>
<p><a href="/">Single video</a> · <a href="/batch">Batch download</a></p>"""

PAGE_FOOT = """<script>
function copyUrl(btn){navigator.clipboard.writeText(btn.dataset.url).then(()=>{btn.textContent="Copied";setTimeout(()=>btn.textContent="Copy",1500)})}
function setBar(wrap,pct,text){
  const bar=wrap.querySelector('.pbar'),fill=wrap.querySelector('.pfill'),st=wrap.querySelector('.dlstatus');
  if(bar) bar.style.display='block';
  if(fill) fill.style.width=Math.max(0,Math.min(100,pct))+'%';
  if(st&&text!=null) st.textContent=text;
}
async function dlVideo(btn){
  const kind=btn.dataset.kind,url=btn.dataset.url;
  const wrap=btn.closest('[data-dlwrap]')||document;
  btn.disabled=true;
  setBar(wrap,2,'Starting…');
  let job;
  try{
    const r=await fetch('/download?kind='+encodeURIComponent(kind)+'&url='+encodeURIComponent(url));
    const j=await r.json();
    if(!j.ok){setBar(wrap,0,'Error: '+(j.error||'could not start'));btn.disabled=false;return;}
    job=j.job;
  }catch(e){setBar(wrap,0,'Error: '+e.message);btn.disabled=false;return;}
  const timer=setInterval(async()=>{
    try{
      const pr=await fetch('/progress?job='+encodeURIComponent(job));
      const p=await pr.json();
      if(!p.ok){clearInterval(timer);setBar(wrap,0,'Error: '+(p.error||'lost track'));btn.disabled=false;return;}
      const label=p.status==='done'?'Saved \\u2713 '+(p.filename||''):((p.detail||p.status)+' '+Math.round(p.pct)+'%');
      setBar(wrap,p.pct,label);
      if(p.status==='done'){clearInterval(timer);setBar(wrap,100,'Saved \\u2713 '+(p.filename||''));}
      else if(p.status==='error'){clearInterval(timer);setBar(wrap,0,'Error: '+(p.error||'failed'));btn.disabled=false;}
    }catch(e){clearInterval(timer);setBar(wrap,0,'Error: '+e.message);btn.disabled=false;}
  },1000);
}
async function downloadAll(kind){
  const btns=[...document.querySelectorAll('button[data-kind="'+kind+'"]')].filter(b=>!b.disabled);
  if(!btns.length) return;
  if(!confirm('Download '+btns.length+' '+kind.toUpperCase()+' file(s) to your download folder?')) return;
  for(const b of btns){ b.click(); await new Promise(r=>setTimeout(r,2500)); }
}
</script></body></html>"""

def page_home(msg=""):
    esc = html.escape
    d = get_download_dir()
    body = [PAGE_HEAD]
    if msg:
        body.append(f'<div class="ok">{esc(msg)}</div>')
    body.append(
        '<div class="card"><h3>Single video</h3>'
        '<form method="GET" action="/info">'
        '<input type="text" name="url" placeholder="https://www.youtube.com/watch?v=…" required autofocus>'
        '<button type="submit">Fetch</button></form></div>'
        '<div class="card"><h3>Download folder</h3>'
        '<form method="POST" action="/setdir">'
        f'<input type="text" name="path" value="{esc(d)}">'
        '<button type="submit">Save</button></form>'
        '<p class="note">Files are downloaded here and kept. Enter any full folder path (it will be created if needed).</p></div>'
        + PAGE_FOOT)
    return "".join(body)

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
    body = [PAGE_HEAD]
    if info.get("_limited_quality"):
        body.append('<div class="err">YouTube asked this network to sign in (bot check), '
                    'so streams are capped around 720p. For full quality, export your browser cookies '
                    'and set <code>YTDL_COOKIES=/path/to/cookies.txt</code> — see README.</div>')
    mp4_info, mp3_info = pick_streams(info)
    durl = esc(url)
    body.append('<div class="card" data-dlwrap><div class="row">')
    if thumb:
        body.append(f'<img class="thumb" src="{esc(thumb)}">')
    body.append(
        f'<div><h2 style="margin:0">{esc(title)}</h2>'
        f'<div class="meta">{esc(str(info.get("uploader") or ""))} · {fmt_duration(info.get("duration"))}</div>'
        f'<div class="row" style="margin-top:10px">'
        f'<div data-dlwrap><button class="alt" data-kind="mp4" data-url="{durl}" onclick="dlVideo(this)">Download MP4</button>'
        f'<div class="note" style="margin:4px 0 0">{esc(mp4_info)}</div>'
        f'<div class="pbar"><div class="pfill"></div></div><div class="dlstatus"></div></div>'
        f'<div data-dlwrap><button data-kind="mp3" data-url="{durl}" onclick="dlVideo(this)">Download MP3</button>'
        f'<div class="note" style="margin:4px 0 0">{esc(mp3_info)}</div>'
        f'<div class="pbar"><div class="pfill"></div></div><div class="dlstatus"></div></div>'
        f'</div>'
        f'<div class="note">Saves to: <code>{esc(get_download_dir())}</code> (<a href="/">change</a>)</div>'
        f'</div></div></div>')
    body.append('<div class="card"><h3>Direct source URLs</h3>'
                '<p class="note">Hit Copy on any stream. These expire after a few hours, so use them soon.</p>'
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

def page_batch_form():
    return (PAGE_HEAD +
            '<div class="card"><h3>Batch download</h3>'
            '<p class="note">One YouTube URL per line, then Fetch all. Each row shows the bitrate you\'d get.</p>'
            '<form method="POST" action="/batch">'
            '<textarea name="urls" rows="6" placeholder="https://www.youtube.com/watch?v=…\nhttps://youtu.be/…"></textarea><br>'
            '<button type="submit">Fetch all</button></form></div>' + PAGE_FOOT)

def page_batch_results(items):
    """items: list of (url, info|None, error|None) in input order."""
    esc = html.escape
    body = [PAGE_HEAD]
    ok = [it for it in items if it[1]]
    body.append(
        '<div class="card"><h3>Batch download</h3>'
        f'<p class="note">{len(ok)} of {len(items)} videos found. '
        'Each download saves straight into your download folder '
        f'(<code>{esc(get_download_dir())}</code>).</p>'
        '<div class="row">'
        '<button class="alt" onclick="downloadAll(\'mp4\')">Download all as MP4</button>'
        '<button onclick="downloadAll(\'mp3\')">Download all as MP3</button>'
        '<a href="/batch">← new batch</a></div></div>')
    body.append('<div class="card"><table><tr><th>Video</th><th>MP4</th><th>MP3</th></tr>')
    for url, info, err in items:
        if err or not info:
            body.append(f'<tr><td colspan="3"><div class="err">Could not fetch {esc(url)}: {esc(err or "unknown error")}</div></td></tr>')
            continue
        mp4_info, mp3_info = pick_streams(info)
        durl = esc(url)
        body.append(
            '<tr>'
            f'<td><b>{esc(info.get("title", "Untitled"))}</b>'
            f'<div class="meta">{esc(str(info.get("uploader") or ""))} · {fmt_duration(info.get("duration"))}</div></td>'
            f'<td data-dlwrap><div class="note" style="margin:0 0 6px">{esc(mp4_info)}</div>'
            f'<button class="alt" data-kind="mp4" data-url="{durl}" onclick="dlVideo(this)">MP4</button>'
            f'<div class="pbar"><div class="pfill"></div></div><div class="dlstatus"></div></td>'
            f'<td data-dlwrap><div class="note" style="margin:0 0 6px">{esc(mp3_info)}</div>'
            f'<button data-kind="mp3" data-url="{durl}" onclick="dlVideo(this)">MP3</button>'
            f'<div class="pbar"><div class="pfill"></div></div><div class="dlstatus"></div></td>'
            '</tr>')
    body.append("</table></div>")
    body.append(PAGE_FOOT)
    return "".join(body)

# ---------------- handler ----------------

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

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json")

    def _read_form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return urllib.parse.parse_qs(raw)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        url = (q.get("url") or [""])[0].strip()
        try:
            if parsed.path in ("/", ""):
                return self._send(200, page_home())
            if parsed.path == "/batch":
                return self._send(200, page_batch_form())
            if parsed.path == "/info":
                if not valid_url(url):
                    return self._send(400, PAGE_HEAD + '<div class="err">That doesn\'t look like a YouTube URL.</div>' + PAGE_FOOT)
                info = yt_dlp_json(url)
                return self._send(200, page_info(url, info))
            if parsed.path == "/download":
                kind = (q.get("kind") or ["mp4"])[0]
                if not valid_url(url) or kind not in ("mp4", "mp3"):
                    return self._json(400, {"ok": False, "error": "Bad request"})
                job = start_download_job(url, kind, get_download_dir())
                return self._json(200, {"ok": True, "job": job, "kind": kind})
            if parsed.path == "/progress":
                jid = (q.get("job") or [""])[0]
                with jobs_lock:
                    job = jobs.get(jid)
                if not job:
                    return self._json(404, {"ok": False, "error": "unknown download"})
                return self._json(200, {"ok": True, "status": job["status"], "pct": job["pct"],
                                        "detail": job["detail"], "filename": job["filename"],
                                        "error": job["error"]})
            return self._send(404, PAGE_HEAD + '<div class="err">Not found.</div>' + PAGE_FOOT)
        except subprocess.TimeoutExpired:
            return self._send(504, PAGE_HEAD + '<div class="err">Timed out talking to YouTube. Try again.</div>' + PAGE_FOOT)
        except Exception as e:
            return self._send(500, PAGE_HEAD + f'<div class="err">Error: {html.escape(str(e)[:600])}</div>' + PAGE_FOOT)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        form = self._read_form()
        try:
            if parsed.path == "/setdir":
                p = (form.get("path") or [""])[0].strip().strip('"').strip("'")
                if not p or not os.path.isabs(p):
                    return self._send(400, PAGE_HEAD + '<div class="err">Please enter a full folder path, e.g. C:\\Users\\you\\Videos.</div>' + PAGE_FOOT)
                try:
                    os.makedirs(p, exist_ok=True)
                    probe = os.path.join(p, ".ytdl-write-test")
                    with open(probe, "w") as f:
                        f.write("ok")
                    os.remove(probe)
                except OSError as e:
                    return self._send(400, PAGE_HEAD + f'<div class="err">Can\'t use that folder: {html.escape(str(e)[:200])}</div>' + PAGE_FOOT)
                cfg = load_config()
                cfg["download_dir"] = p
                save_config(cfg)
                return self._send(200, page_home("Download folder saved."))
            if parsed.path == "/batch":
                raw = (form.get("urls") or [""])[0]
                seen, urls = set(), []
                for line in raw.splitlines():
                    u = line.strip()
                    if u and u not in seen:
                        seen.add(u)
                        urls.append(u)
                if not urls:
                    return self._send(400, PAGE_HEAD + '<div class="err">Paste at least one URL.</div>' + PAGE_FOOT)
                # fetch concurrently, keep input order
                with ThreadPoolExecutor(max_workers=4) as ex:
                    items = list(ex.map(lambda u: safe_info(u) if valid_url(u)
                                        else (u, None, "not a YouTube URL"), urls))
                return self._send(200, page_batch_results(items))
            return self._send(404, PAGE_HEAD + '<div class="err">Not found.</div>' + PAGE_FOOT)
        except Exception as e:
            return self._send(500, PAGE_HEAD + f'<div class="err">Error: {html.escape(str(e)[:600])}</div>' + PAGE_FOOT)

def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"YouTube Downloader running → http://127.0.0.1:{PORT}")
    print(f"Download folder: {get_download_dir()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")

if __name__ == "__main__":
    main()
