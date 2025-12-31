from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import time
from threading import Thread

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

BASE_URL = "https://web-production-73a3d.up.railway.app"

# Cleanup settings
CLEANUP_INTERVAL = 60 * 60  # Run cleanup every hour
FILE_LIFETIME = 60 * 60     # Delete files older than 1 hour

# Convert Shorts URLs to normal watch URLs
def convert_shorts_url(url: str) -> str:
    match = re.match(r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)', url)
    if match:
        video_id = match.group(3)
        return f"https://www.youtube.com/watch?v={video_id}"
    return url

# Download video synchronously
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

# Background cleanup thread
def cleanup_files():
    while True:
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > FILE_LIFETIME:
                try:
                    os.remove(filepath)
                    print(f"Deleted old file: {filepath}")
                except Exception as e:
                    print(f"Failed to delete {filepath}: {e}")
        time.sleep(CLEANUP_INTERVAL)

Thread(target=cleanup_files, daemon=True).start()

# Download endpoint
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
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            return jsonify({'error': f"Failed to fetch video info: {str(e)}"}), 400

    # Download video synchronously
    try:
        download_video(url, temp_filename)
    except Exception as e:
        return jsonify({'error': f"Failed to download video: {str(e)}"}), 500

    return jsonify({
        'message': f"Downloaded '{info.get('title')}' successfully.",
        'title': info.get('title'),
        'thumbnail': info.get('thumbnail'),
        'download_url': f"{BASE_URL}/downloads/{os.path.basename(temp_filename)}"
    })

# Serve downloads
@app.route('/downloads/<filename>')
def serve_download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
