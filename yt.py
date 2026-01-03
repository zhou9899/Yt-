from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import os
import uuid
import time
import logging
import re
from threading import Thread
from werkzeug.utils import safe_join
import subprocess

# ---------------------------
# Configuration & Logging
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

BASE_URL = os.environ.get('RAILWAY_STATIC_URL', 'web-production-c7a2e.up.railway.app')
if not BASE_URL.startswith('http'):
    BASE_URL = f'https://{BASE_URL}'
logger.info(f"Base URL: {BASE_URL}")

PORT = int(os.environ.get('PORT', 5000))

CLEANUP_INTERVAL = 30 * 60  # seconds
FILE_LIFETIME = 30 * 60     # seconds

# ---------------------------
# Utility Functions
# ---------------------------

def get_video_info(url: str):
    """Extract video info using yt_dlp."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
        'no_check_certificate': True,
        'socket_timeout': 30,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        raise

def sanitize_filename(filename: str) -> str:
    """Remove unsafe characters."""
    return re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)

def get_actual_resolution(filepath: str) -> str:
    """Get video resolution via ffprobe."""
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=height',
            '-of', 'csv=p=0',
            filepath
        ], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip().isdigit():
            return f"{result.stdout.strip()}p"
    except Exception as e:
        logger.warning(f"FFprobe failed: {e}")
    return "HD"

def cleanup_files():
    """Background cleanup thread to delete old files."""
    while True:
        try:
            now = time.time()
            for f in os.listdir(DOWNLOAD_DIR):
                if f.endswith('.mp4'):
                    filepath = os.path.join(DOWNLOAD_DIR, f)
                    if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > FILE_LIFETIME:
                        try:
                            os.remove(filepath)
                            logger.info(f"Cleaned old file: {f}")
                        except Exception as e:
                            logger.error(f"Failed to delete {f}: {e}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        time.sleep(CLEANUP_INTERVAL)

# Start cleanup thread
Thread(target=cleanup_files, daemon=True).start()
logger.info("Cleanup thread started")

# ---------------------------
# Flask Routes
# ---------------------------

@app.route('/health', methods=['GET'])
def health_check():
    """Return server health status."""
    files_count = len([f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.mp4')])
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Downloader (WhatsApp Compatible)',
        'downloads_dir_exists': os.path.exists(DOWNLOAD_DIR),
        'files_count': files_count,
        'base_url': BASE_URL,
        'timestamp': int(time.time())
    })

@app.route('/download', methods=['POST'])
def download_video():
    """Download YouTube video in WhatsApp-compatible format."""
    data = request.json
    if not data or 'url' not in data:
        return jsonify({'error': 'URL required'}), 400

    url = data['url'].replace('voutu.be', 'youtu.be').replace('ww.youtube.com', 'www.youtube.com')

    # Handle shorts
    match = re.search(r'shorts/([a-zA-Z0-9_-]+)', url)
    if match:
        video_id = match.group(1)
        url = f"https://www.youtube.com/watch?v={video_id}"

    unique_id = str(uuid.uuid4())
    filename = f"{unique_id}.mp4"
    output_path = os.path.join(DOWNLOAD_DIR, filename)

    try:
        # Fetch video info
        logger.info(f"Fetching video info for {url}")
        info = get_video_info(url)
        title = info.get('title', 'YouTube Video')
        duration = info.get('duration', 0)
        thumbnail = info.get('thumbnail', '')
        uploader = info.get('uploader', '')

        # WhatsApp-compatible format
        format_string = 'best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]'
        resolution = f"{info.get('height', 0)}p" if info.get('height') else "HD"

        # Download video
        logger.info(f"Downloading {title} ({resolution})")
        ydl_opts = {
            'format': format_string,
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'continuedl': True,
            'noprogress': True,
            'retries': 3,
            'concurrent_fragment_downloads': 1,
            'socket_timeout': 30,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return jsonify({'error': 'Download failed or empty file'}), 500

        file_size = os.path.getsize(output_path)
        file_size_mb = round(file_size / (1024*1024), 2)
        actual_resolution = get_actual_resolution(output_path)

        return jsonify({
            'success': True,
            'id': unique_id,
            'filename': filename,
            'title': title[:200],
            'uploader': uploader[:100],
            'duration': duration,
            'thumbnail': thumbnail,
            'resolution': actual_resolution,
            'size_bytes': file_size,
            'size_mb': file_size_mb,
            'download_url': f"{BASE_URL}/downloads/{filename}",
            'quality': 'whatsapp_compatible',
            'format': format_string,
            'message': f"✅ {actual_resolution} video ready: {file_size_mb}MB"
        })

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        err = str(e).lower()
        if "private" in err or "restricted" in err:
            return jsonify({'error': 'Video is private or age-restricted'}), 400
        elif "unavailable" in err:
            return jsonify({'error': 'Video is unavailable'}), 400
        else:
            return jsonify({'error': f'Failed to download: {str(e)[:100]}'}), 400

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/downloads/<path:filename>', methods=['GET'])
def serve_download(filename):
    """Serve downloaded files safely."""
    safe_path = safe_join(DOWNLOAD_DIR, filename)
    if not safe_path or not os.path.exists(safe_path) or not safe_path.endswith('.mp4'):
        return jsonify({'error': 'File not found or invalid filename'}), 404
    try:
        response = send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=True,
            mimetype='video/mp4'
        )
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        logger.error(f"Error serving file {filename}: {e}")
        return jsonify({'error': 'File serving error'}), 500

# ---------------------------
# Run Server
# ---------------------------
if __name__ == '__main__':
    logger.info(f"Starting server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
