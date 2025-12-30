from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re, os, uuid, threading, time
from datetime import datetime, timedelta

app = Flask(__name__)

DOWNLOAD_DIR = "./downloads"
CLEANUP_INTERVAL = 3600
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def convert_shorts_url(url: str) -> str:
    patterns = [
        r"(https?://)?(www\.)?youtube\.com/shorts/([A-Za-z0-9_-]+)",
        r"(https?://)?youtu\.be/([A-Za-z0-9_-]+)"
    ]
    for p in patterns:
        m = re.match(p, url)
        if m:
            vid = m.group(3) if "shorts" in p else m.group(2)
            return f"https://www.youtube.com/watch?v={vid}"
    return url

def cleanup_old_files():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = datetime.now()
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(path):
                if now - datetime.fromtimestamp(os.path.getmtime(path)) > timedelta(seconds=CLEANUP_INTERVAL):
                    os.remove(path)

def download_video(url, filepath):
    try:
        ydl_opts = {
            "format": "best[acodec!=none][vcodec!=none][height<=720]/best",
            "outtmpl": filepath,
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0"
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception:
        if os.path.exists(filepath):
            os.remove(filepath)

@app.route("/download", methods=["POST"])
def download():
    data = request.get_json(force=True)
    url = data.get("url")
    if not url:
        return jsonify({"error": "URL required"}), 400

    url = convert_shorts_url(url)
    file_id = str(uuid.uuid4())
    filename = f"{file_id}.mp4"
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    threading.Thread(
        target=download_video,
        args=(url, filepath),
        daemon=True
    ).start()

    return jsonify({
        "success": True,
        "download_id": file_id,
        "filename": filename
    })

@app.route("/status/<file_id>")
def status(file_id):
    path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return jsonify({
            "status": "ready",
            "download_url": f"/downloads/{file_id}.mp4"
        })
    return jsonify({"status": "processing"})

@app.route("/downloads/<filename>")
def serve(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    threading.Thread(target=cleanup_old_files, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
