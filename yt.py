from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
from threading import Thread

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

BASE_URL = "https://web-production-73a3d.up.railway.app"

def convert_shorts_url(url: str) -> str:
    match = re.match(r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)', url)
    if match:
        video_id = match.group(3)
        return f"https://www.youtube.com/watch?v={video_id}"
    return url

def download_video(url, filename):
    ydl_opts = {
        'format': 'best[height<=720]',
        'noplaylist': True,
        'outtmpl': filename,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

@app.route('/download', methods=['POST'])
def download_short():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400

    url = convert_shorts_url(url)
    temp_filename = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.mp4")

    # Extract info without downloading first
    ydl_opts_info = {'format': 'best[height<=720]', 'noplaylist': True, 'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
        info = ydl.extract_info(url, download=False)

    # Start background download
    Thread(target=download_video, args=(url, temp_filename), daemon=True).start()

    # Return thumbnail and info immediately
    return jsonify({
        'message': f"Downloading '{info.get('title')}'...",
        'title': info.get('title'),
        'thumbnail': info.get('thumbnail'),
        'download_url': f"{BASE_URL}/downloads/{os.path.basename(temp_filename)}"
    })

@app.route('/downloads/<filename>')
def serve_download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
