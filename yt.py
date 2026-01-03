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

# FIXED: Use correct Railway URL
BASE_URL = os.environ.get('RAILWAY_STATIC_URL', 'web-production-c7a2e.up.railway.app')
if not BASE_URL.startswith('http'):
    BASE_URL = f'https://{BASE_URL}'
logger.info(f"Base URL: {BASE_URL}")

# Cleanup settings
CLEANUP_INTERVAL = 30 * 60  # 30 minutes
FILE_LIFETIME = 30 * 60     # 30 minutes

def convert_shorts_url(url: str) -> str:
    """Convert YouTube Shorts URLs to regular watch URLs"""
    if 'shorts/' in url:
        # Extract video ID from shorts URL
        video_id = url.split('shorts/')[-1].split('?')[0]
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

def select_best_quality_format(info):
    """
    Select the best quality format that actually works
    Returns: (format_string, resolution)
    """
    try:
        formats = info.get('formats', [])
        
        if not formats:
            # Use known working format combination
            return 'bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]', '1080p'
        
        # List of preferred formats in order of preference
        # Format: (format_id_or_string, resolution_label, is_progressive)
        preferred_formats = [
            # Progressive formats (video+audio in one)
            ('22', '720p', True),           # 720p MP4 with AAC
            ('18', '360p', True),           # 360p MP4 (fallback)
            
            # DASH formats (need merging)
            ('137+140', '1080p', False),    # 1080p video + audio
            ('299+140', '1080p', False),    # 1080p60 video + audio
            ('298+140', '720p', False),     # 720p60 video + audio
            
            # Audio-only formats with best video
            ('bestvideo[height<=1080]+bestaudio', '1080p', False),
            ('bestvideo[height<=720]+bestaudio', '720p', False),
        ]
        
        # Check which preferred formats are available
        for format_str, resolution, is_progressive in preferred_formats:
            if '+' in format_str:
                # Combined format (video+audio)
                video_part, audio_part = format_str.split('+')
                has_video = any(f.get('format_id') == video_part for f in formats)
                has_audio = any(f.get('format_id') == audio_part for f in formats)
                if has_video and has_audio:
                    logger.info(f"✅ Found combined format: {format_str} ({resolution})")
                    return format_str, resolution
            else:
                # Single format
                if any(f.get('format_id') == format_str for f in formats):
                    logger.info(f"✅ Found progressive format: {format_str} ({resolution})")
                    return format_str, resolution
        
        # If no preferred formats found, use smart fallback
        # Find highest quality MP4 progressive format
        mp4_formats = []
        for fmt in formats:
            has_video = fmt.get('vcodec') != 'none'
            has_audio = fmt.get('acodec') != 'none'
            height = fmt.get('height', 0)
            ext = fmt.get('ext', '').lower()
            
            if has_video and has_audio and ext == 'mp4' and height >= 360:
                mp4_formats.append({
                    'format_id': fmt['format_id'],
                    'height': height,
                    'resolution': f"{height}p",
                    'tbr': fmt.get('tbr', 0),
                    'filesize': fmt.get('filesize') or fmt.get('filesize_approx') or 0
                })
        
        if mp4_formats:
            mp4_formats.sort(key=lambda x: (-x['height'], -x['tbr']))
            best = mp4_formats[0]
            logger.info(f"✅ Selected MP4 format: {best['format_id']} ({best['resolution']})")
            return best['format_id'], best['resolution']
        
        # Ultimate fallback
        logger.info("⚠️ Using ultimate fallback format")
        return 'best[height<=1080]', '1080p'
        
    except Exception as e:
        logger.error(f"Error selecting format: {e}")
        # Safe fallback that usually works
        return 'best[height<=1080]', '1080p'

@app.route('/download', methods=['POST'])
def download_video():
    """Download YouTube video - HIGH QUALITY"""
    data = request.json
    if not data:
        return jsonify({'error': 'No JSON data provided'}), 400
    
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    # Fix common URL typos
    url = url.replace('voutu.be', 'youtu.be').replace('ww.youtube.com', 'www.youtube.com')
    
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

        # Check duration limit (optional, can remove)
        if duration > 600:  # 10 minutes
            logger.warning(f"Long video detected: {duration}s")
            # Continue anyway, but warn

        # Select best quality format
        format_string, resolution = select_best_quality_format(info)
        logger.info(f"📥 Downloading {resolution} with format: {format_string}")

        # Download with yt-dlp - RELIABLE settings
        ydl_opts = {
            'format': format_string,
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'no_check_certificate': True,
            'socket_timeout': 30,
            'retries': 3,
            'continuedl': True,
            'noprogress': True,
            'concurrent_fragment_downloads': 2,
            'http_chunk_size': 10485760,  # 10MB chunks
            'fragment_retries': 10,
            'ignoreerrors': False,
        }

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
        
        # Update resolution based on actual file if possible
        actual_resolution = resolution
        
        # Prepare response
        return jsonify({
            'success': True,
            'title': title[:200],
            'thumbnail': thumbnail,
            'duration': duration,
            'uploader': uploader[:100],
            'resolution': actual_resolution,
            'size_bytes': file_size,
            'size_mb': round(file_size_mb, 2),
            'quality': 'high',
            'message': f"✅ {actual_resolution} video ready: {file_size_mb:.2f}MB",
            'download_url': f"{BASE_URL}/downloads/{filename}",
            'id': unique_id,
            'filename': filename,
            'format': format_string
        })

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        error_msg = str(e)
        
        # Clean error messages
        if "Private" in error_msg or "Restricted" in error_msg:
            return jsonify({'error': 'Video is private or age-restricted'}), 400
        elif "Unavailable" in error_msg:
            return jsonify({'error': 'Video is unavailable'}), 400
        elif "ffmpeg" in error_msg or "FFmpeg" in error_msg:
            # Railway doesn't have ffmpeg by default
            return jsonify({'error': 'Server configuration issue. Using simpler format...'}), 500
        else:
            return jsonify({'error': f'Failed to download: {error_msg[:100]}'}), 400
            
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check"""
    files_count = 0
    if os.path.exists(DOWNLOAD_DIR):
        files_count = len([f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.mp4')])
    
    return jsonify({
        'status': 'healthy',
        'service': 'YouTube High Quality Downloader',
        'downloads_dir': True,
        'files_count': files_count,
        'base_url': BASE_URL,
        'timestamp': time.time()
    })

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded files"""
    # Security check
    if not filename or '..' in filename or '/' in filename or not filename.endswith('.mp4'):
        return jsonify({'error': 'Invalid filename'}), 400

    filepath = os.path.join(DOWNLOAD_DIR, filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    try:
        # Get file size for Content-Length header
        file_size = os.path.getsize(filepath)
        
        response = send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=True,
            mimetype='video/mp4'
        )
        response.headers['Cache-Control'] = 'public, max-age=300'
        response.headers['Content-Length'] = file_size
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
