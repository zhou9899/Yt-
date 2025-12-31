from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import time
import logging
from threading import Thread
from urllib.parse import urlparse

app = Flask(__name__)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Get BASE_URL from environment or use default
BASE_URL = os.environ.get('BASE_URL', 'https://web-production-73a3d.up.railway.app')
if not BASE_URL.startswith(('http://', 'https://')):
    BASE_URL = f'https://{BASE_URL}'

logger.info(f"Using BASE_URL: {BASE_URL}")

# Cleanup settings
CLEANUP_INTERVAL = 60 * 60  # Run cleanup every hour
FILE_LIFETIME = 60 * 60     # Delete files older than 1 hour

# WhatsApp limits
WHATSAPP_MAX_SIZE = 50 * 1024 * 1024  # 50MB

def convert_shorts_url(url: str) -> str:
    """Convert various YouTube URL formats to standard watch URL"""
    if not url:
        return url
    
    patterns = [
        r'(https?://)?(www\.|m\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(https?://)?(www\.|m\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(https?://)?(www\.|m\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.match(pattern, url, re.IGNORECASE)
        if match:
            # Extract video ID based on pattern
            if 'shorts' in pattern:
                video_id = match.group(3)
            elif 'youtu.be' in pattern:
                video_id = match.group(2)
            elif 'embed' in pattern:
                video_id = match.group(3)
            else:
                video_id = match.group(3)
            return f"https://www.youtube.com/watch?v={video_id}"
    
    return url

def validate_youtube_url(url: str) -> bool:
    """Validate if URL is a valid YouTube URL"""
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return False
        
        # Check domain
        domain = parsed.netloc.lower()
        if not any(yt_domain in domain for yt_domain in ['youtube.com', 'youtu.be']):
            return False
        
        # Check for video ID patterns
        patterns = [
            r'v=([a-zA-Z0-9_-]{11})',
            r'shorts/([a-zA-Z0-9_-]{11})',
            r'youtu\.be/([a-zA-Z0-9_-]{11})',
            r'embed/([a-zA-Z0-9_-]{11})'
        ]
        
        for pattern in patterns:
            if re.search(pattern, url):
                return True
        
        return False
    except Exception:
        return False

def get_best_format_for_whatsapp():
    """Get format string optimized for WhatsApp"""
    # Try 720p first, then fallback to lower qualities
    return 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'

def download_video(url, filename, max_retries=2):
    """Download video with retry logic"""
    ydl_opts = {
        'format': get_best_format_for_whatsapp(),
        'noplaylist': True,
        'outtmpl': filename,
        'quiet': False,  # Set to True for production
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        'continuedl': True,
        'consoletitle': False,
        'progress_hooks': [lambda d: None],  # Disable progress hooks
        'logger': logger,
    }
    
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            # Verify download
            if os.path.exists(filename) and os.path.getsize(filename) > 1024:  # At least 1KB
                file_size = os.path.getsize(filename)
                logger.info(f"Download successful: {filename}, Size: {file_size/1024/1024:.2f}MB")
                return True
            else:
                logger.warning(f"Download created empty file, retrying...")
                
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"Download error (attempt {attempt + 1}): {str(e)}")
            if "Private video" in str(e):
                raise Exception("This video is private and cannot be downloaded")
            elif "Members-only" in str(e):
                raise Exception("This is a members-only video")
            elif "Sign in" in str(e):
                raise Exception("This video requires sign in")
        except Exception as e:
            logger.error(f"Unexpected error (attempt {attempt + 1}): {str(e)}")
        
        if attempt < max_retries - 1:
            time.sleep(2)  # Wait before retry
    
    return False

def get_video_info(url):
    """Get video information"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Sanitize title
            title = info.get('title', 'Unknown Video')
            title = re.sub(r'[<>:"/\\|?*]', '', title)  # Remove invalid filename chars
            
            return {
                'title': title[:100],  # Limit title length
                'thumbnail': info.get('thumbnail', ''),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown')[:50],
                'view_count': info.get('view_count', 0),
                'description': (info.get('description', '')[:150] + '...') if info.get('description') else '',
                'webpage_url': info.get('webpage_url', url),
            }
    except Exception as e:
        logger.error(f"Error getting video info: {str(e)}")
        raise

def cleanup_files():
    """Background cleanup of old files"""
    while True:
        try:
            now = time.time()
            deleted = 0
            
            for filename in os.listdir(DOWNLOAD_DIR):
                if not filename.endswith('.mp4'):
                    continue
                    
                filepath = os.path.join(DOWNLOAD_DIR, filename)
                if os.path.isfile(filepath):
                    file_age = now - os.path.getmtime(filepath)
                    if file_age > FILE_LIFETIME:
                        try:
                            os.remove(filepath)
                            deleted += 1
                            logger.debug(f"Deleted old file: {filename}")
                        except Exception as e:
                            logger.error(f"Failed to delete {filename}: {e}")
            
            if deleted > 0:
                logger.info(f"Cleanup deleted {deleted} files")
                
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        
        time.sleep(CLEANUP_INTERVAL)

# Start cleanup thread
Thread(target=cleanup_files, daemon=True).start()

@app.route('/')
def index():
    return jsonify({
        'status': 'active',
        'service': 'YouTube to WhatsApp Video Downloader',
        'whatsapp_limit': '50MB',
        'max_resolution': '720p',
        'endpoints': {
            'download': 'POST /download',
            'serve': 'GET /downloads/<filename>'
        }
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': time.time()})

@app.route('/download', methods=['POST'])
def download_video_endpoint():
    """Download YouTube video"""
    try:
        data = request.json or {}
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'error': 'URL required'}), 400
        
        logger.info(f"Download request for URL: {url[:100]}...")
        
        # Validate URL
        if not validate_youtube_url(url):
            return jsonify({'error': 'Invalid YouTube URL. Please provide a valid YouTube video URL.'}), 400
        
        # Convert to standard format
        original_url = url
        url = convert_shorts_url(url)
        logger.info(f"Converted URL: {url}")
        
        # Get video info
        try:
            info = get_video_info(url)
        except Exception as e:
            logger.error(f"Failed to get video info: {str(e)}")
            return jsonify({'error': f'Failed to get video information: {str(e)}'}), 400
        
        # Check duration
        if info['duration'] > 600:  # 10 minutes
            logger.warning(f"Long video detected: {info['duration']} seconds")
        
        # Generate unique filename
        file_id = str(uuid.uuid4())
        filename = f"{file_id}.mp4"
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        # Download video
        logger.info(f"Starting download: {info['title']}")
        try:
            success = download_video(url, filepath)
            if not success:
                return jsonify({'error': 'Failed to download video. Please try again.'}), 500
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            return jsonify({'error': str(e)}), 500
        
        # Get file info
        file_size = os.path.getsize(filepath)
        file_size_mb = file_size / 1024 / 1024
        whatsapp_compatible = file_size <= WHATSAPP_MAX_SIZE
        
        # Build response
        response_data = {
            'success': True,
            'video': {
                'title': info['title'],
                'duration': info['duration'],
                'uploader': info['uploader'],
                'thumbnail': info['thumbnail'],
                'original_url': original_url,
            },
            'download': {
                'filename': filename,
                'download_url': f"{BASE_URL}/downloads/{filename}",
                'size_bytes': file_size,
                'size_mb': round(file_size_mb, 2),
                'whatsapp_compatible': whatsapp_compatible,
                'expires_in_seconds': FILE_LIFETIME,
            }
        }
        
        if not whatsapp_compatible:
            response_data['warning'] = 'Video exceeds WhatsApp 50MB limit. It may not send properly.'
        
        logger.info(f"Download completed: {info['title']} ({file_size_mb:.2f}MB)")
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Unexpected error in download endpoint: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error. Please try again later.'}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded files"""
    try:
        # Security: Only allow MP4 files with UUID names
        if not re.match(r'^[a-f0-9\-]{36}\.mp4$', filename):
            return jsonify({'error': 'Invalid filename'}), 400
        
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        # Check if file exists
        if not os.path.exists(filepath):
            return jsonify({'error': 'File not found or expired'}), 404
        
        # Check file age
        file_age = time.time() - os.path.getmtime(filepath)
        if file_age > FILE_LIFETIME:
            try:
                os.remove(filepath)
            except:
                pass
            return jsonify({'error': 'File expired'}), 410
        
        # Get safe filename
        safe_filename = f"video_{filename[:8]}.mp4"
        
        return send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=True,
            download_name=safe_filename,
            mimetype='video/mp4'
        )
        
    except Exception as e:
        logger.error(f"Error serving file {filename}: {str(e)}")
        return jsonify({'error': 'Failed to serve file'}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"500 error: {str(e)}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Get port from environment (for Railway/Heroku)
    port = int(os.environ.get('PORT', 5000))
    
    logger.info(f"Starting server on port {port}")
    logger.info(f"Download directory: {DOWNLOAD_DIR}")
    logger.info(f"Base URL: {BASE_URL}")
    
    # For production, use waitress or gunicorn instead
    app.run(host='0.0.0.0', port=port, debug=False)
