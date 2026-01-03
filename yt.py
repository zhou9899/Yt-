from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import os
import uuid
import time
import logging
from threading import Thread, Lock
import signal
import sys

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Thread-safe directory creation
DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Railway settings - FIXED: Use your actual Railway URL
BASE_URL = os.environ.get('RAILWAY_STATIC_URL', 'web-production-c7a2e.up.railway.app')
if not BASE_URL.startswith('http'):
    BASE_URL = f'https://{BASE_URL}'
logger.info(f"Base URL: {BASE_URL}")

# Cleanup settings
CLEANUP_INTERVAL = 30 * 60  # Clean every 30 minutes
FILE_LIFETIME = 30 * 60     # Delete files older than 30 minutes

# Thread safety for file operations
file_lock = Lock()
active_downloads = set()

# Graceful shutdown handler
def shutdown_handler(signum, frame):
    logger.info("Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

def sanitize_filename(filename):
    """Remove invalid characters from filename"""
    return "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_', '-')).rstrip()

def get_video_info(url):
    """Get video information with timeout"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
        'no_check_certificate': True,
        'socket_timeout': 30,
        'extractor_args': {
            'youtube': {
                'skip': ['hls', 'dash'],
            }
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Add timeout to prevent hanging
            info = ydl.extract_info(url, download=False)
            
            # Validate required fields
            if not info.get('title'):
                raise ValueError("No title found in video info")
                
            return info
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"YT-DLP error for {url}: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to get video info for {url}: {e}")
        raise

def get_highest_quality_format(info):
    """
    Get format ID for highest quality video that's reasonable
    Returns: (format_id, resolution, is_progressive)
    """
    try:
        formats = info.get('formats', [])
        
        if not formats:
            return 'best[height<=1080]', 'Unknown', False
        
        # Filter out problematic formats
        valid_formats = []
        for fmt in formats:
            # Skip formats that are likely to fail
            if not fmt.get('protocol') or fmt.get('protocol') not in ['http', 'https']:
                continue
            if fmt.get('vcodec') == 'none':
                continue
            if fmt.get('ext') in ['webm', '3gp']:  # Prefer mp4
                continue
                
            height = fmt.get('height', 0)
            width = fmt.get('width', 0)
            
            # Skip if resolution is too high (causes large files)
            if height > 2160:  # Skip 4K+ to avoid huge files
                continue
                
            valid_formats.append({
                'format_id': fmt['format_id'],
                'height': height,
                'width': width,
                'resolution': f"{height}p" if height else "Unknown",
                'fps': fmt.get('fps', 0),
                'tbr': fmt.get('tbr', 0),
                'ext': fmt.get('ext', ''),
                'acodec': fmt.get('acodec'),
                'vcodec': fmt.get('vcodec'),
                'protocol': fmt.get('protocol', ''),
                'is_progressive': fmt.get('acodec') != 'none' and fmt.get('vcodec') != 'none',
                'filesize': fmt.get('filesize', 0) or fmt.get('filesize_approx', 0)
            })
        
        if not valid_formats:
            # Fallback to safe format
            return 'best[height<=1080]', 'Unknown', False
        
        # Sort by: height (desc), but prefer progressive formats
        valid_formats.sort(key=lambda x: (
            -x['height'],  # Higher resolution first
            0 if x['is_progressive'] else 1,  # Progressive first
            -x['tbr']  # Higher bitrate first
        ))
        
        # Take the best format
        best_format = valid_formats[0]
        
        # If progressive, use directly
        if best_format['is_progressive']:
            logger.info(f"Selected progressive format: {best_format['format_id']} ({best_format['resolution']})")
            return best_format['format_id'], best_format['resolution'], True
        
        # Otherwise find matching audio
        audio_formats = []
        for fmt in formats:
            if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                if fmt.get('abr', 0) >= 128:  # Decent audio quality
                    audio_formats.append({
                        'format_id': fmt['format_id'],
                        'abr': fmt.get('abr', 0)
                    })
        
        if audio_formats:
            audio_formats.sort(key=lambda x: -x['abr'])
            video_id = best_format['format_id']
            audio_id = audio_formats[0]['format_id']
            format_id = f"{video_id}+{audio_id}"
            logger.info(f"Selected combined format: {format_id} ({best_format['resolution']})")
            return format_id, best_format['resolution'], False
        
        # Fallback to safe format
        return 'best[height<=1080]', best_format['resolution'], False
        
    except Exception as e:
        logger.error(f"Error selecting format: {e}")
        # Safe fallback
        return 'best[height<=1080]', 'Unknown', False

@app.route('/download', methods=['POST'])
def download_video():
    """Download highest quality YouTube video"""
    data = request.json
    if not data:
        return jsonify({'error': 'No JSON data provided'}), 400
    
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    # Basic URL validation
    if 'youtube.com' not in url and 'youtu.be' not in url:
        return jsonify({'error': 'Invalid YouTube URL'}), 400
    
    unique_id = str(uuid.uuid4())
    filename = f"{unique_id}.mp4"
    output_path = os.path.join(DOWNLOAD_DIR, filename)
    
    # Track active download
    active_downloads.add(unique_id)
    
    try:
        # Get video info
        logger.info(f"Fetching info for: {url}")
        info = get_video_info(url)
        
        title = info.get('title', 'YouTube Video')
        duration = info.get('duration', 0)
        thumbnail = info.get('thumbnail', '')
        uploader = info.get('uploader', '')
        
        # Check duration (prevent huge files)
        if duration > 600:  # 10 minutes max
            return jsonify({
                'error': f'Video too long ({duration//60}min). Max 10 minutes.',
                'duration': duration,
                'title': title
            }), 400
        
        # Get highest quality format
        format_id, resolution, is_progressive = get_highest_quality_format(info)
        logger.info(f"Downloading {resolution} with format: {format_id}")
        
        # Download options - SIMPLIFIED for reliability
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
            'extract_flat': False,
            'postprocessor_args': {
                'ffmpeg': ['-loglevel', 'error']
            },
            'concurrent_fragment_downloads': 2,  # Limit concurrency
        }
        
        # Download video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Verify download
        if not os.path.exists(output_path):
            return jsonify({'error': 'Download failed - no output file'}), 500
        
        file_size = os.path.getsize(output_path)
        if file_size == 0:
            os.remove(output_path)
            return jsonify({'error': 'Download failed - empty file'}), 500
        
        file_size_mb = file_size / (1024 * 1024)
        
        # Get resolution (simplified - no ffprobe dependency)
        # Use the resolution from format selection
        actual_resolution = resolution
        
        # Prepare response
        response = {
            'success': True,
            'title': sanitize_filename(title),
            'thumbnail': thumbnail,
            'duration': duration,
            'uploader': uploader,
            'resolution': actual_resolution,
            'size_bytes': file_size,
            'size_mb': round(file_size_mb, 2),
            'quality': 'highest',
            'format': format_id,
            'progressive': is_progressive,
            'message': f"✅ {actual_resolution} video ready: {file_size_mb:.2f}MB",
            'download_url': f"{BASE_URL}/downloads/{filename}",
            'id': unique_id,
            'filename': filename
        }
        
        return jsonify(response)
        
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error for {url}: {e}")
        # Clean up partial file
        if os.path.exists(output_path):
            os.remove(output_path)
        return jsonify({'error': 'Failed to download video. It might be private, age-restricted, or unavailable.'}), 400
    except Exception as e:
        logger.error(f"Unexpected error for {url}: {e}", exc_info=True)
        # Clean up partial file
        if os.path.exists(output_path):
            os.remove(output_path)
        return jsonify({'error': 'Internal server error'}), 500
    finally:
        # Remove from active downloads
        active_downloads.discard(unique_id)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check with more info"""
    try:
        files = []
        if os.path.exists(DOWNLOAD_DIR):
            files = [f for f in os.listdir(DOWNLOAD_DIR) if os.path.isfile(os.path.join(DOWNLOAD_DIR, f))]
        
        return jsonify({
            'status': 'healthy',
            'service': 'YouTube Highest Quality Downloader',
            'downloads_dir': os.path.isdir(DOWNLOAD_DIR),
            'files_count': len(files),
            'active_downloads': len(active_downloads),
            'timestamp': time.time(),
            'base_url': BASE_URL
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded videos with security checks"""
    # Security check - only allow alphanumeric, dash, dot, underscore
    import re
    if not re.match(r'^[a-zA-Z0-9_.\-]+\.mp4$', filename):
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
        logger.error(f"Error serving file {filename}: {e}")
        return jsonify({'error': 'File serving error'}), 500

def cleanup_files():
    """Cleanup old files, avoiding active downloads"""
    while True:
        try:
            now = time.time()
            if os.path.exists(DOWNLOAD_DIR):
                files = os.listdir(DOWNLOAD_DIR)
                for f in files:
                    # Skip files that might be actively downloading
                    file_id = f.replace('.mp4', '')
                    if file_id in active_downloads:
                        continue
                        
                    filepath = os.path.join(DOWNLOAD_DIR, f)
                    if os.path.isfile(filepath):
                        file_age = now - os.path.getmtime(filepath)
                        if file_age > FILE_LIFETIME:
                            try:
                                with file_lock:
                                    os.remove(filepath)
                                logger.info(f"Cleaned up old file: {f}")
                            except Exception as e:
                                logger.error(f"Failed to delete {f}: {e}")
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

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({'error': 'Method not allowed'}), 405

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
