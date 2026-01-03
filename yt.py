from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import time
import logging
from threading import Thread

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# FIXED: Always ensure BASE_URL has https://
BASE_URL = os.environ.get('RAILWAY_STATIC_URL', 'web-production-c7a2e.up.railway.app')
if not BASE_URL.startswith('http'):
    BASE_URL = f'https://{BASE_URL}'
logger.info(f"Base URL: {BASE_URL}")

# Cleanup settings
CLEANUP_INTERVAL = 30 * 60  # 30 minutes
FILE_LIFETIME = 30 * 60     # 30 minutes

def convert_shorts_url(url: str) -> str:
    """Convert YouTube Shorts URLs to regular watch URLs"""
    patterns = [
        r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)',
        r'(https?://)?(www\.)?youtu\.be/([a-zA-Z0-9_-]+)',
        r'(https?://)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)'
    ]

    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(3) if match.lastindex >= 3 else match.group(2)
            return f"https://www.youtube.com/watch?v={video_id}"
    return url

def get_video_info(url):
    """Get video information without downloading"""
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
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        raise

def select_best_format(info, max_height=1080):
    """
    Select BEST quality up to max_height (default 1080p)
    Returns format_id and resolution
    """
    try:
        formats = info.get('formats', [])

        if not formats:
            return 'best[height<=1080]', 'Unknown'

        # Find progressive formats (video+audio in one file)
        progressive_formats = []
        for fmt in formats:
            # Check if it has both video and audio
            has_video = fmt.get('vcodec') != 'none'
            has_audio = fmt.get('acodec') != 'none'
            height = fmt.get('height', 0)
            
            if has_video and has_audio and 0 < height <= max_height:
                # Get filesize safely
                filesize = fmt.get('filesize') or fmt.get('filesize_approx') or 0
                
                progressive_formats.append({
                    'format_id': fmt['format_id'],
                    'height': height,
                    'width': fmt.get('width', 0),
                    'filesize': filesize,
                    'tbr': fmt.get('tbr', 0),
                    'ext': fmt.get('ext', ''),
                    'resolution': f"{height}p"
                })

        if not progressive_formats:
            # Fallback to simple format
            return f'best[height<={max_height}]', 'Unknown'

        # Sort by: height (highest first), then filesize (larger = better quality)
        progressive_formats.sort(key=lambda x: (
            -x['height'],      # Highest resolution first
            -x['tbr'],         # Highest bitrate first
            -x['filesize']     # Largest file (usually better quality)
        ))

        best = progressive_formats[0]
        logger.info(f"Selected format: {best['format_id']} ({best['resolution']})")
        return best['format_id'], best['resolution']

    except Exception as e:
        logger.error(f"Error selecting format: {e}")
        return 'best[height<=1080]', 'Unknown'

@app.route('/download', methods=['POST'])
def download_video():
    """Download YouTube video - NO COMPRESSION, just download and return link"""
    data = request.json
    if not data:
        return jsonify({'error': 'No JSON data provided'}), 400
    
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    # Fix common URL typos
    if 'voutu.be' in url:
        url = url.replace('voutu.be', 'youtu.be')
    if 'ww.youtube.com' in url:
        url = url.replace('ww.youtube.com', 'www.youtube.com')
    
    try:
        # Convert URL if needed
        url = convert_shorts_url(url)
        unique_id = str(uuid.uuid4())
        filename = f"{unique_id}.mp4"
        output_path = os.path.join(DOWNLOAD_DIR, filename)

        # Get video info
        logger.info(f"Fetching info for: {url}")
        info = get_video_info(url)

        title = info.get('title', 'YouTube Video')
        duration = info.get('duration', 0)
        thumbnail = info.get('thumbnail', '')
        uploader = info.get('uploader', '')

        # Check duration limit (10 minutes max)
        if duration > 600:
            return jsonify({
                'error': f'Video too long ({duration//60}min). Max 10 minutes.',
                'duration': duration,
                'title': title
            }), 400

        # Select best format (1080p max)
        format_id, resolution = select_best_format(info, max_height=1080)
        logger.info(f"Downloading {resolution} with format: {format_id}")

        # Download with yt-dlp - SIMPLE & RELIABLE
        ydl_opts = {
            'format': format_id,
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'no_check_certificate': True,
            'socket_timeout': 30,
            'retries': 3,
            'continuedl': True,
            'noprogress': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Check if download succeeded
        if not os.path.exists(output_path):
            return jsonify({'error': 'Download failed'}), 500

        # Get file info
        file_size = os.path.getsize(output_path)
        file_size_mb = file_size / (1024 * 1024)

        # Prepare response
        return jsonify({
            'success': True,
            'title': title[:200],  # Limit title length
            'thumbnail': thumbnail,
            'duration': duration,
            'uploader': uploader[:100],
            'resolution': resolution,
            'size_bytes': file_size,
            'size_mb': round(file_size_mb, 2),
            'message': f"✅ {resolution} video ready: {file_size_mb:.2f}MB",
            'download_url': f"{BASE_URL}/downloads/{filename}",  # HAS https://
            'id': unique_id,
            'filename': filename
        })

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        error_msg = str(e)
        if "Private" in error_msg or "Restricted" in error_msg:
            return jsonify({'error': 'Video is private or age-restricted'}), 400
        elif "Unavailable" in error_msg:
            return jsonify({'error': 'Video is unavailable'}), 400
        else:
            return jsonify({'error': 'Failed to download video'}), 400
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    files_count = 0
    if os.path.exists(DOWNLOAD_DIR):
        files_count = len([f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.mp4')])
    
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube Downloader',
        'downloads_dir': True,
        'files_count': files_count,
        'base_url': BASE_URL,
        'timestamp': time.time()
    })

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded files"""
    # Security check
    if '..' in filename or '/' in filename or not filename.endswith('.mp4'):
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    try:
        response = send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=True,
            mimetype='video/mp4'
        )
        response.headers['Cache-Control'] = 'public, max-age=300'
        response.headers['Content-Disposition'] = f'attachment; filename="video.mp4"'
        return response
    except Exception as e:
        logger.error(f"Error serving file: {e}")
        return jsonify({'error': 'File serving error'}), 500

def cleanup_files():
    """Background cleanup of old files"""
    while True:
        try:
            now = time.time()
            if os.path.exists(DOWNLOAD_DIR):
                for f in os.listdir(DOWNLOAD_DIR):
                    if f.endswith('.mp4'):
                        filepath = os.path.join(DOWNLOAD_DIR, f)
                        if os.path.isfile(filepath):
                            file_age = now - os.path.getmtime(filepath)
                            if file_age > FILE_LIFETIME:
                                try:
                                    os.remove(filepath)
                                    logger.info(f"Cleaned: {f}")
                                except Exception as e:
                                    logger.error(f"Failed to delete {f}: {e}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        
        time.sleep(CLEANUP_INTERVAL)

# Start cleanup thread
try:
    Thread(target=cleanup_files, daemon=True).start()
    logger.info("Cleanup thread started")
except Exception as e:
    logger.error(f"Failed to start cleanup thread: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
