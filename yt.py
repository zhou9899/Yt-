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
BASE_URL = os.environ.get('RAILWAY_STATIC_URL', 'https://web-production-73a3d.up.railway.app')

# Cleanup settings
CLEANUP_INTERVAL = 30 * 60  # Run cleanup every 30 minutes
FILE_LIFETIME = 30 * 60     # Delete files older than 30 minutes (Railway has ephemeral storage)
MAX_SIZE_MB = 45  # Keep under 50MB with buffer
MAX_DURATION = 300  # Max 5 minutes to avoid huge files

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

def compress_video_railway(input_path, output_path, target_size_mb):
    """
    Compress video for Railway environment
    Uses simpler compression if ffmpeg is available
    """
    if not is_ffmpeg_available():
        return False
    
    try:
        # Get video duration
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 
               'format=duration', '-of', 'json', input_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration = float(json.loads(result.stdout)['format']['duration'])
        
        # Skip if too long
        if duration > MAX_DURATION:
            logger.warning(f"Video too long ({duration}s), skipping compression")
            return False
        
        # Calculate target bitrate (simplified for Railway)
        target_bitrate = int((target_size_mb * 8000) / duration)  # Rough calculation
        
        # Limit bitrate ranges
        if duration < 60:  # Short videos
            target_bitrate = min(target_bitrate, 1500)
        else:  # Longer videos
            target_bitrate = min(target_bitrate, 1000)
        
        # Use simpler compression for Railway
        cmd = [
            'ffmpeg', '-i', input_path,
            '-c:v', 'libx264',
            '-preset', 'fast',  # Faster encoding
            '-crf', '28',  # Slightly higher for smaller size
            '-maxrate', f'{target_bitrate}k',
            '-bufsize', f'{target_bitrate * 2}k',
            '-c:a', 'aac',
            '-b:a', '96k',  # Lower audio bitrate
            '-movflags', '+faststart',
            '-threads', '2',  # Limit threads for Railway
            '-y',
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            # Verify output file exists and is smaller
            if os.path.exists(output_path):
                original_size = os.path.getsize(input_path)
                compressed_size = os.path.getsize(output_path)
                
                if compressed_size < original_size and compressed_size > 0:
                    logger.info(f"Compressed from {original_size/1e6:.1f}MB to {compressed_size/1e6:.1f}MB")
                    return True
        
        return False
        
    except subprocess.TimeoutExpired:
        logger.error("Compression timed out")
        return False
    except Exception as e:
        logger.error(f"Compression error: {e}")
        return False

def select_optimal_format(info, max_height=720):
    """
    Select optimal format considering size and quality
    Prioritizes smaller formats for WhatsApp
    """
    try:
        formats = info.get('formats', [])
        
        if not formats:
            return 'best[height<=720]/best'
        
        # Filter for progressive downloads (single file)
        progressive_formats = [
            f for f in formats 
            if f.get('protocol') == 'https' 
            and f.get('vcodec') != 'none'
            and f.get('acodec') != 'none'
            and f.get('height', 0) <= max_height
        ]
        
        if progressive_formats:
            # Sort by filesize if available, then by resolution
            progressive_formats.sort(key=lambda x: (
                x.get('filesize', float('inf')),
                -x.get('height', 0),
                x.get('tbr', 0)
            ))
            return progressive_formats[0]['format_id']
        
        # Fallback to standard format
        return f'best[height<={max_height}]/best'
        
    except Exception as e:
        logger.error(f"Error selecting format: {e}")
        return 'best[height<=720]/best'

# Download endpoint
@app.route('/download', methods=['POST'])
def download_video():
    """Main download endpoint with compression"""
    data = request.json
    url = data.get('url')
    
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    try:
        # Convert URL if needed
        url = convert_shorts_url(url)
        unique_id = str(uuid.uuid4())
        temp_filename = os.path.join(DOWNLOAD_DIR, f"{unique_id}.mp4")
        compressed_filename = os.path.join(COMPRESSED_DIR, f"{unique_id}.mp4")
        
        # Get video info first
        logger.info(f"Fetching info for: {url}")
        info = get_video_info(url)
        
        title = info.get('title', 'YouTube Video')
        duration = info.get('duration', 0)
        thumbnail = info.get('thumbnail', '')
        
        # Check duration limit
        if duration > MAX_DURATION:
            return jsonify({
                'error': f'Video too long ({duration}s). Max allowed: {MAX_DURATION}s',
                'duration': duration,
                'title': title
            }), 400
        
        # Select optimal format
        format_id = select_optimal_format(info)
        logger.info(f"Selected format: {format_id}")
        
        # Download with yt-dlp
        ydl_opts = {
            'format': format_id,
            'outtmpl': temp_filename,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'no_check_certificate': True,
            'socket_timeout': 30,
            'retries': 3,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Check if download succeeded
        if not os.path.exists(temp_filename):
            return jsonify({'error': 'Download failed'}), 500
        
        # Get file size
        original_size = os.path.getsize(temp_filename)
        original_size_mb = original_size / (1024 * 1024)
        
        final_path = temp_filename
        compressed = False
        message = f"Video ready: {original_size_mb:.1f}MB"
        
        # Try compression if needed and available
        if original_size_mb > MAX_SIZE_MB and is_ffmpeg_available():
            logger.info(f"Attempting compression: {original_size_mb:.1f}MB > {MAX_SIZE_MB}MB")
            
            if compress_video_railway(temp_filename, compressed_filename, MAX_SIZE_MB):
                compressed_size = os.path.getsize(compressed_filename) / (1024 * 1024)
                
                if compressed_size < original_size_mb:
                    final_path = compressed_filename
                    compressed = True
                    message = f"Compressed: {original_size_mb:.1f}MB → {compressed_size:.1f}MB"
        
        # Prepare response
        file_size_mb = os.path.getsize(final_path) / (1024 * 1024)
        
        return jsonify({
            'success': True,
            'title': title,
            'thumbnail': thumbnail,
            'duration': duration,
            'size_mb': round(file_size_mb, 1),
            'compressed': compressed,
            'message': message,
            'download_url': f"{BASE_URL}/downloads/{os.path.basename(final_path)}",
            'id': unique_id
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
    """Serve downloaded files"""
    # Security check
    if '..' in filename or filename.startswith('/'):
        return jsonify({'error': 'Invalid filename'}), 400
    
    # Try compressed first, then downloads
    paths_to_try = [
        os.path.join(COMPRESSED_DIR, filename),
        os.path.join(DOWNLOAD_DIR, filename)
    ]
    
    for filepath in paths_to_try:
        if os.path.exists(filepath):
            # Set cache headers for Railway
            response = send_from_directory(
                os.path.dirname(filepath),
                os.path.basename(filepath),
                as_attachment=True
            )
            response.headers['Cache-Control'] = 'public, max-age=300'
            return response
    
    return jsonify({'error': 'File not found'}), 404

# Cleanup function
def cleanup_files():
    """Background cleanup of old files"""
    while True:
        try:
            now = time.time()
            for directory in [DOWNLOAD_DIR, COMPRESSED_DIR]:
                if os.path.exists(directory):
                    for f in os.listdir(directory):
                        filepath = os.path.join(directory, f)
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
