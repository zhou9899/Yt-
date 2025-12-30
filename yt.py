from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import threading
import time
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
DOWNLOAD_DIR = './downloads'
CLEANUP_INTERVAL = 3600  # 1 hour
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Convert shorts/short links to regular YouTube watch URLs
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

# Cleanup old files in background
def cleanup_old_files():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = datetime.now()
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(path) and now - datetime.fromtimestamp(os.path.getmtime(path)) > timedelta(seconds=CLEANUP_INTERVAL):
                try:
                    os.remove(path)
                    logger.info(f"Cleaned up: {f}")
                except:
                    pass

# Download video in background
def download_video(url, output_filename):
    try:
        logger.info(f"Starting download: {url}")
        # Get video info
        ydl_opts_info = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])

            # Pick **combined streams only** (video + audio)
            combined_formats = [
                f for f in formats
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none' and f.get('height', 0) <= 1080
            ]
            if not combined_formats:
                raise Exception("No combined streams <=1080p available")

            # Pick the highest resolution available <= 1080p
            combined_formats.sort(key=lambda f: f.get('height', 0), reverse=True)
            best_format_id = combined_formats[0]['format_id']

            ydl_opts_download = {
                'format': best_format_id,
                'outtmpl': output_filename,
                'quiet': True,
                'no_warnings': True,
                'http_headers': {'User-Agent': 'Mozilla/5.0'},
            }

            with yt_dlp.YoutubeDL(ydl_opts_download) as ydl:
                ydl.download([url])

        logger.info(f"Download completed: {output_filename}")

    except Exception as e:
        logger.error(f"Download failed: {e}")
        try:
            if os.path.exists(output_filename):
                os.remove(output_filename)
        except:
            pass

@app.route('/download', methods=['POST'])
def download_endpoint():
    data = request.get_json()
    if not data or not data.get('url'):
        return jsonify({'error': 'URL required'}), 400

    url = convert_shorts_url(data['url'])

    try:
        # Extract info for title, thumbnail, etc.
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return jsonify({'error': 'Could not extract video info'}), 400

        file_id = str(uuid.uuid4())
        filename = f"{file_id}.mp4"
        output_path = os.path.join(DOWNLOAD_DIR, filename)

        # Start background download
        threading.Thread(target=download_video, args=(url, output_path), daemon=True).start()

        return jsonify({
            'success': True,
            'title': info.get('title', 'Unknown'),
            'duration': info.get('duration', 0),
            'thumbnail': info.get('thumbnail'),
            'download_id': file_id,
            'filename': filename
        })

    except Exception as e:
        logger.error(f"Error in download endpoint: {e}")
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    if not re.match(r'^[a-f0-9\-]{36}\.mp4$', filename):
        return jsonify({'error': 'Invalid filename'}), 400
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        return jsonify({'error': 'File not found or expired'}), 404
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

@app.route('/status/<file_id>')
def check_status(file_id):
    path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
    if os.path.exists(path):
        size = os.path.getsize(path)
        return jsonify({'status': 'ready', 'size': size, 'download_url': f"/downloads/{file_id}.mp4"})
    else:
        return jsonify({'status': 'processing'})

if __name__ == '__main__':
    threading.Thread(target=cleanup_old_files, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), threaded=True)
