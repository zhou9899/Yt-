from flask import Flask, request, jsonify, send_file
import yt_dlp
import os
import uuid
import time
import subprocess
import logging
import re
from threading import Thread

# =========================
# CONFIG
# =========================

DOWNLOAD_DIR = "./downloads"
PORT = int(os.environ.get("PORT", 5000))
BASE_URL = os.environ.get("RAILWAY_STATIC_URL", "").strip()

if BASE_URL and not BASE_URL.startswith("http"):
    BASE_URL = "https://" + BASE_URL

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("YT")

# =========================
# APP
# =========================

app = Flask(__name__)

# =========================
# CLEANUP THREAD
# =========================

FILE_LIFETIME = 30 * 60

def cleanup_loop():
    while True:
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(path) and now - os.path.getmtime(path) > FILE_LIFETIME:
                try:
                    os.remove(path)
                except:
                    pass
        time.sleep(300)

Thread(target=cleanup_loop, daemon=True).start()

# =========================
# UTILS
# =========================

def normalize_url(url: str) -> str:
    url = url.replace("voutu.be", "youtu.be").replace("ww.youtube.com", "www.youtube.com")
    m = re.search(r"shorts/([A-Za-z0-9_-]+)", url)
    if m:
        return f"https://www.youtube.com/watch?v={m.group(1)}"
    return url

def ffprobe_height(path: str) -> str:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height", "-of", "csv=p=0", path],
            capture_output=True, text=True
        )
        if r.stdout.strip().isdigit():
            return f"{r.stdout.strip()}p"
    except:
        pass
    return "HD"

# =========================
# ROUTES
# =========================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "files": len(os.listdir(DOWNLOAD_DIR)),
        "time": int(time.time())
    })

@app.route("/download", methods=["POST"])
def download():
    data = request.json
    if not data or "url" not in data:
        return jsonify({"error": "URL required"}), 400

    url = normalize_url(data["url"])
    uid = uuid.uuid4().hex

    video_path = os.path.join(DOWNLOAD_DIR, f"{uid}_video.mp4")
    audio_path = os.path.join(DOWNLOAD_DIR, f"{uid}_audio.m4a")
    final_path = os.path.join(DOWNLOAD_DIR, f"{uid}.mp4")

    try:
        # -------------------------
        # DOWNLOAD VIDEO (≤1080p, AVC)
        # -------------------------
        ydl_video = {
            "format": "bestvideo[vcodec^=avc1][height<=1080][ext=mp4]",
            "outtmpl": video_path,
            "quiet": True,
            "no_warnings": True
        }

        with yt_dlp.YoutubeDL(ydl_video) as ydl:
            info = ydl.extract_info(url, download=True)

        title = info.get("title", "YouTube Video")

        # -------------------------
        # DOWNLOAD AUDIO (AAC)
        # -------------------------
        ydl_audio = {
            "format": "bestaudio[acodec^=mp4a]/bestaudio",
            "outtmpl": audio_path,
            "quiet": True,
            "no_warnings": True
        }

        with yt_dlp.YoutubeDL(ydl_audio) as ydl:
            ydl.download([url])

        # -------------------------
        # MUX + FASTSTART (WHATSAPP FIX)
        # -------------------------
        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-movflags", "+faststart",
            final_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if not os.path.exists(final_path) or os.path.getsize(final_path) == 0:
            raise RuntimeError("Final MP4 invalid")

        res = ffprobe_height(final_path)
        size = os.path.getsize(final_path)

        return jsonify({
            "success": True,
            "title": title[:200],
            "resolution": res,
            "size_mb": round(size / (1024 * 1024), 2),
            "download_url": f"{BASE_URL}/file/{uid}.mp4" if BASE_URL else f"/file/{uid}.mp4",
            "codec": "H.264 + AAC",
            "container": "MP4 faststart",
            "whatsapp": "guaranteed"
        })

    except Exception as e:
        log.error(e, exc_info=True)
        return jsonify({"error": "Download failed"}), 500

    finally:
        for f in (video_path, audio_path):
            if os.path.exists(f):
                os.remove(f)

@app.route("/file/<name>")
def serve(name):
    path = os.path.join(DOWNLOAD_DIR, name)
    if not path.endswith(".mp4") or not os.path.exists(path):
        return jsonify({"error": "Not found"}), 404

    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=False,
        conditional=True
    )

# =========================
# RUN
# =========================

if __name__ == "__main__":
    log.info(f"Server running on {PORT}")
    app.run(host="0.0.0.0", port=PORT)
