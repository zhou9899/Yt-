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

# Convert shorts/short links to regular watch URLs
def convert_shorts_url(url: str) -> str:
    patterns = [
        r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)',
        r'(https?://)?youtu\.be/([a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(3) if 'shorts' in pattern else match.group(2)
            return f"https://www.youtube.com/watch?v={video_id}"
    return url

# Cleanup old files
def cleanup_old_files():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = datetime.now()
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(path) and now - datetime.fromtimestamp(os.path.getmtime(path)) > timedelta(seconds=CLEANUP_INTERVAL):
                try:
                    os.remove(path)
                    print(f"Cleaned: {f}")
                except:
                    pass

# Background download
def download_media(url, filename, audio_only=False):
    try:
        ydl_opts = {
            'format': 'bestaudio/best' if audio_only else 'best[height<=1080]/best',
            'outtmpl': filename,
            'quiet': True,
            'no_warnings': True,
            'postprocessors': [],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0'
            }
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print(f"Downloaded: {filename}")
    except Exception as e:
        print(f"Failed {url}: {e}")
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except:
            pass

@app.route('/download', methods=['POST'])
def download_short():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    url = data.get('url')
    audio_only = data.get('audio', False)  # New param: download audio only
    if not url:
        return jsonify({'error': 'URL required'}), 400

    url = convert_shorts_url(url)

    try:
        # Extract info
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        if not info:
            return jsonify({'error': 'Could not extract info'}), 400

        file_id = str(uuid.uuid4())
        ext = 'mp3' if audio_only else 'mp4'
        temp_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}.{ext}")

        # Start background download
        thread = threading.Thread(
            target=download_media,
            args=(url, temp_filename, audio_only),
            daemon=True
        )
        thread.start()

        return jsonify({
            'success': True,
            'message': f"Downloading '{info.get('title', 'video')}'...",
            'title': info.get('title', 'Unknown Title'),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader', 'Unknown'),
            'thumbnail': info.get('thumbnail'),
            'download_id': file_id,
            'filename': f"{file_id}.{ext}"
        })

    except Exception as e:
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    # Security check
    if not re.match(r'^[a-f0-9\-]{36}\.(mp4|mp3)$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found or expired'}), 404
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

@app.route('/status/<file_id>')
def check_status(file_id):
    for ext in ['mp4', 'mp3']:
        path = os.path.join(DOWNLOAD_DIR, f"{file_id}.{ext}")
        if os.path.exists(path):
            return jsonify({'status': 'ready', 'download_url': f"/downloads/{file_id}.{ext}"})
    return jsonify({'status': 'processing'})

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    deleted = 0
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        if os.path.isfile(path):
            try:
                os.remove(path)
                deleted += 1
            except:
                pass
    return jsonify({'message': f'Cleaned {deleted} files'})

if __name__ == '__main__':
    threading.Thread(target=cleanup_old_files, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
