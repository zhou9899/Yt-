from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import time
import json
from threading import Thread
import subprocess
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
COMPRESSED_DIR = './compressed'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COMPRESSED_DIR, exist_ok=True)

# Use environment variable for base URL or default
BASE_URL = os.environ.get('RAILWAY_STATIC_URL', 'https://web-production-c7a2e.up.railway.app')

# Cleanup settings
CLEANUP_INTERVAL = 30 * 60  # Run cleanup every 30 minutes
FILE_LIFETIME = 30 * 60     # Delete files older than 30 minutes (Railway has ephemeral storage)
# REMOVED size limits - bot will handle compression

# Convert Shorts URLs to normal watch URLs
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
        'extract_flat': False,  # Get full info for duration
        'no_check_certificate': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        raise

def is_ffmpeg_available():
    """Check if ffmpeg is available for compression"""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning("ffmpeg not available, compression disabled")
        return False

# REMOVED compression functions - bot will handle if needed

def select_best_format(info):
    """
    Select the BEST available quality (original resolution)
    Returns format ID for highest quality progressive download
    """
    try:
        formats = info.get('formats', [])
        
        if not formats:
            return 'best'  # Fallback to yt-dlp's best
        
        # Find progressive formats (video+audio in one file)
        progressive_formats = [
            f for f in formats
            if f.get('protocol') == 'https'
            and f.get('vcodec') != 'none'
            and f.get('acodec') != 'none'  # Has both video and audio
            and f.get('format_note', '').lower() not in ['dash', 'webm']  # Avoid DASH/WebM
        ]
        
        if progressive_formats:
            # Sort by: resolution (highest first), filesize (largest first for quality)
            progressive_formats.sort(key=lambda x: (
                -x.get('height', 0),  # Highest resolution first
                -x.get('width', 0),   # Then widest
                -x.get('tbr', 0),     # Then highest bitrate
                -x.get('filesize', 0) # Then largest file (usually better quality)
            ))
            
            logger.info(f"Selected format: {progressive_formats[0]['format_id']} "
                       f"({progressive_formats[0].get('height', '?')}p, "
                       f"{progressive_formats[0].get('ext', '?')})")
            return progressive_formats[0]['format_id']
        
        # If no progressive format found, use best combined format
        return 'bestvideo+bestaudio/best'
        
    except Exception as e:
        logger.error(f"Error selecting format: {e}")
        return 'best'  # Fallback to absolute best

# Download endpoint
@app.route('/download', methods=['POST'])
def download_video():
    """Main download endpoint - NO COMPRESSION, BEST QUALITY"""
    data = request.json
    url = data.get('url')

    if not url:
        return jsonify({'error': 'URL required'}), 400

    try:
        # Convert URL if needed
        url = convert_shorts_url(url)
        unique_id = str(uuid.uuid4())
        filename = f"{unique_id}.mp4"
        output_path = os.path.join(DOWNLOAD_DIR, filename)

        # Get video info first
        logger.info(f"Fetching info for: {url}")
        info = get_video_info(url)

        title = info.get('title', 'YouTube Video')
        duration = info.get('duration', 0)
        thumbnail = info.get('thumbnail', '')
        
        # Get resolution info
        resolution = 'Unknown'
        if info.get('height'):
            resolution = f"{info.get('height')}p"
        elif info.get('formats'):
            # Find max resolution from formats
            heights = [f.get('height', 0) for f in info.get('formats', []) if f.get('height')]
            if heights:
                resolution = f"{max(heights)}p"

        # Select BEST format (no compression logic)
        format_id = select_best_format(info)
        logger.info(f"Selected BEST format: {format_id} for {resolution}")

        # Download with yt-dlp - BEST QUALITY
        ydl_opts = {
            'format': format_id,
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',  # Force MP4 output
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

        # Get file size
        file_size = os.path.getsize(output_path)
        file_size_mb = file_size / (1024 * 1024)

        # Prepare response with FULL quality info
        return jsonify({
            'success': True,
            'title': title,
            'thumbnail': thumbnail,
            'duration': duration,
            'resolution': resolution,
            'size_bytes': file_size,
            'size_mb': round(file_size_mb, 1),
            'quality': 'best',
            'message': f"Video ready: {resolution}, {file_size_mb:.1f}MB",
            'download_url': f"{BASE_URL}/downloads/{filename}",
            'id': unique_id,
            'format': format_id
        })

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        return jsonify({'error': 'Failed to download video. It might be private or restricted.'}), 400
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({'error': str(e)}), 500

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    """Health check for Railway"""
    return jsonify({
        'status': 'healthy',
        'downloads_dir': os.path.isdir(DOWNLOAD_DIR),
        'compressed_dir': os.path.isdir(COMPRESSED_DIR),
        'ffmpeg_available': is_ffmpeg_available(),
        'timestamp': time.time()
    })

# Serve downloads
@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded files - ORIGINAL QUALITY"""
    # Security check
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    if os.path.exists(filepath):
        # Set cache headers for Railway
        response = send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=True,
            mimetype='video/mp4'
        )
        response.headers['Cache-Control'] = 'public, max-age=300'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    
    return jsonify({'error': 'File not found'}), 404

# Cleanup function
def cleanup_files():
    """Background cleanup of old files"""
    while True:
        try:
            now = time.time()
            if os.path.exists(DOWNLOAD_DIR):
                for f in os.listdir(DOWNLOAD_DIR):
                    filepath = os.path.join(DOWNLOAD_DIR, f)
                    if os.path.isfile(filepath):
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > FILE_LIFETIME:
                            try:
                                os.remove(filepath)
                                logger.info(f"Cleaned up: {filepath}")
                            except Exception as e:
                                logger.error(f"Failed to delete {filepath}: {e}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

        time.sleep(CLEANUP_INTERVAL)

# Start cleanup thread
try:
    cleanup_thread = Thread(target=cleanup_files, daemon=True)
    cleanup_thread.start()
    logger.info("Cleanup thread started")
except Exception as e:
    logger.error(f"Failed to start cleanup thread: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
