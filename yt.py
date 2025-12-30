from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import threading
import time
from datetime import datetime, timedelta

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
CLEANUP_INTERVAL = 3600  # 1 hour
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def convert_shorts_url(url: str) -> str:
    """Convert Shorts URL to regular YouTube URL"""
    patterns = [
        r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)',
        r'(https?://)?youtu\.be/([a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(3) if 'youtube.com' in pattern else match.group(2)
            return f"https://www.youtube.com/watch?v={video_id}"
    return url

def download_video(url, output_path):
    """Download video capped at 720p"""
    try:
        ydl_opts = {
            'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print(f"Downloaded: {output_path}")
    except Exception as e:
        print(f"Download failed: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)

def cleanup_old_files():
    """Remove old files periodically"""
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = datetime.now()
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(path) and (now - datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() > CLEANUP_INTERVAL:
                try:
                    os.remove(path)
                    print(f"Deleted old file: {f}")
                except:
                    pass

@app.route('/download', methods=['POST'])
def download_short():
    data = request.get_json()
    if not data or not data.get('url'):
        return jsonify({'error': 'URL required'}), 400

    url = convert_shorts_url(data['url'])
    file_id = str(uuid.uuid4())
    filename = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    # Start download in background
    threading.Thread(target=download_video, args=(url, filename), daemon=True).start()

    return jsonify({
        'success': True,
        'download_id': file_id,
        'filename': f"{file_id}.mp4"
    })

@app.route('/downloads/<filename>')
def serve_download(filename):
    if not re.match(r'^[a-f0-9\-]{36}\.mp4$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found'}), 404
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True, download_name='video.mp4')

@app.route('/status/<file_id>')
def check_status(file_id):
    path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
    if os.path.exists(path):
        size = os.path.getsize(path)
        return jsonify({'status': 'ready', 'download_url': f"/downloads/{file_id}.mp4", 'size': size})
    return jsonify({'status': 'processing'})

if __name__ == '__main__':
    threading.Thread(target=cleanup_old_files, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true')
