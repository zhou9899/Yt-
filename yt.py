from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import time
from threading import Thread
import logging

app = Flask(__name__)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# WhatsApp limits
WHATSAPP_MAX_SIZE = 50 * 1024 * 1024  # 50MB for chats
WHATSAPP_STATUS_MAX_SIZE = 64 * 1024 * 1024  # 64MB for status

BASE_URL = "https://web-production-73a3d.up.railway.app"

# Cleanup settings
CLEANUP_INTERVAL = 60 * 60  # Run cleanup every hour
FILE_LIFETIME = 60 * 60     # Delete files older than 1 hour

def convert_shorts_url(url: str) -> str:
    """Convert various YouTube URL formats to standard watch URL"""
    patterns = [
        r'(https?://)?(www\.|m\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(https?://)?(www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(3) if 'shorts' in pattern else match.group(2) if 'youtu.be' in pattern else match.group(3)
            return f"https://www.youtube.com/watch?v={video_id}"
    return url

def get_best_format_for_whatsapp():
    """Get format string optimized for WhatsApp"""
    # Merge audio+video, prefer mp4, under 50MB if possible
    return 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best[ext=mp4]/best'

def download_video(url, filename, max_retries=3):
    """Download video with retry logic and WhatsApp optimization"""
    ydl_opts = {
        'format': get_best_format_for_whatsapp(),
        'noplaylist': True,
        'outtmpl': filename,
        'quiet': True,
        'merge_output_format': 'mp4',
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        'continuedl': True,
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
        'prefer_ffmpeg': True,
        'ffmpeg_location': '/usr/bin/ffmpeg' if os.path.exists('/usr/bin/ffmpeg') else None,
    }
    
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            if os.path.exists(filename) and os.path.getsize(filename) > 0:
                file_size = os.path.getsize(filename)
                logger.info(f"Download successful: {filename}, Size: {file_size/1024/1024:.2f}MB")
                return True
        except Exception as e:
            logger.error(f"Download attempt {attempt + 1} failed: {str(e)}")
            time.sleep(2)
    return False

def get_video_info(url):
    """Get video information including available formats"""
    ydl_opts = {'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get('formats', [])
        best_format = None
        for f in formats:
            if f.get('vcodec') != 'none' and f.get('ext') == 'mp4':
                if best_format is None or f.get('height', 0) > best_format.get('height', 0):
                    best_format = f
        return {
            'title': info.get('title', 'Unknown'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader', 'Unknown'),
            'view_count': info.get('view_count', 0),
            'best_resolution': best_format.get('height', 'Unknown') if best_format else 'Unknown',
            'description': info.get('description', '')[:200] + '...' if info.get('description') else ''
        }

def cleanup_files():
    """Background cleanup of old files"""
    while True:
        try:
            now = time.time()
            for f in os.listdir(DOWNLOAD_DIR):
                filepath = os.path.join(DOWNLOAD_DIR, f)
                if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > FILE_LIFETIME:
                    try:
                        os.remove(filepath)
                        logger.info(f"Deleted old file: {f}")
                    except Exception as e:
                        logger.error(f"Failed to delete {f}: {e}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        time.sleep(CLEANUP_INTERVAL)

Thread(target=cleanup_files, daemon=True).start()

@app.route('/')
def index():
    return jsonify({
        'status': 'active',
        'service': 'YouTube to WhatsApp Video Downloader',
        'limits': {
            'whatsapp_chat': '50MB',
            'whatsapp_status': '64MB',
            'max_resolution': '1080p'
        },
        'endpoints': {
            'download': 'POST /download',
            'serve': 'GET /downloads/<filename>'
        }
    })

@app.route('/download', methods=['POST'])
def download_video_endpoint():
    data = request.json or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL required'}), 400

    original_url = url
    url = convert_shorts_url(url)
    if 'youtube.com' not in url and 'youtu.be' not in url:
        return jsonify({'error': 'Invalid YouTube URL'}), 400

    try:
        info = get_video_info(url)
    except Exception as e:
        logger.error(f"Failed to get video info: {str(e)}")
        return jsonify({'error': f'Failed to fetch video info: {str(e)}'}), 400

    file_id = str(uuid.uuid4())
    temp_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    logger.info(f"Starting download: {info['title']}")
    try:
        success = download_video(url, temp_filename)
        if not success:
            return jsonify({'error': 'Failed to download video after multiple attempts'}), 500
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        return jsonify({'error': f'Download failed: {str(e)}'}), 500

    file_size = os.path.getsize(temp_filename)
    file_size_mb = file_size / 1024 / 1024
    whatsapp_compatible = file_size <= WHATSAPP_MAX_SIZE
    whatsapp_status_compatible = file_size <= WHATSAPP_STATUS_MAX_SIZE

    return jsonify({
        'success': True,
        'video': info,
        'download': {
            'filename': f"{file_id}.mp4",
            'download_url': f"{BASE_URL}/downloads/{file_id}.mp4",
            'size_bytes': file_size,
            'size_mb': round(file_size_mb, 2),
            'whatsapp_compatible': whatsapp_compatible,
            'whatsapp_status_compatible': whatsapp_status_compatible,
            'expires_in': '1 hour'
        }
    })

@app.route('/downloads/<filename>')
def serve_download(filename):
    if not re.match(r'^[a-f0-9\-]{36}\.mp4$', filename):
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found or expired'}), 404

    file_age = time.time() - os.path.getmtime(filepath)
    if file_age > FILE_LIFETIME:
        try:
            os.remove(filepath)
        except:
            pass
        return jsonify({'error': 'File expired'}), 410

    safe_filename = f"whatsapp_video_{filename[:8]}.mp4"
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True, download_name=safe_filename, mimetype='video/mp4')

if __name__ == '__main__':
    ffmpeg_paths = ['/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg']
    for path in ffmpeg_paths:
        if os.path.exists(path):
            logger.info(f"FFmpeg found at: {path}")
            break
    else:
        logger.warning("FFmpeg not found. Video processing may be limited.")
    
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
