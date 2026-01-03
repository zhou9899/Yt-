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

BASE_URL = os.environ.get('RAILWAY_STATIC_URL', 'web-production-c7a2e.up.railway.app')
if not BASE_URL.startswith('http'):
    BASE_URL = f'https://{BASE_URL}'
logger.info(f"Base URL: {BASE_URL}")

CLEANUP_INTERVAL = 30 * 60
FILE_LIFETIME = 30 * 60

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

def get_available_formats(info):
    """Get all available formats and print them for debugging"""
    formats = info.get('formats', [])
    logger.info("=== AVAILABLE FORMATS ===")
    
    # Group by resolution
    by_resolution = {}
    for fmt in formats:
        height = fmt.get('height', 0)
        if height not in by_resolution:
            by_resolution[height] = []
        
        by_resolution[height].append({
            'format_id': fmt.get('format_id'),
            'ext': fmt.get('ext'),
            'vcodec': fmt.get('vcodec'),
            'acodec': fmt.get('acodec'),
            'filesize': fmt.get('filesize') or fmt.get('filesize_approx') or 0,
            'tbr': fmt.get('tbr', 0),
            'protocol': fmt.get('protocol', ''),
        })
    
    # Log all formats by resolution
    for height in sorted(by_resolution.keys(), reverse=True):
        if height > 0:
            logger.info(f"\n{height}p formats:")
            for fmt in by_resolution[height]:
                logger.info(f"  ID: {fmt['format_id']} | Codec: {fmt['vcodec']}/{fmt['acodec']} | "
                          f"Size: {fmt['filesize']/1024/1024:.1f}MB | Ext: {fmt['ext']}")

def select_720p_or_higher(info):
    """
    FORCE 720p or higher quality
    Priority: 720p → 1080p → 480p → 360p
    """
    try:
        formats = info.get('formats', [])
        
        # DEBUG: Show all available formats
        get_available_formats(info)
        
        if not formats:
            return 'best[height<=1080]', 'Unknown'
        
        # FIRST: Try to get 720p (format 22) - MP4 with AAC audio
        for fmt in formats:
            if fmt.get('format_id') == '22':
                logger.info("🎯 FOUND format 22 (720p MP4)")
                return '22', '720p'
        
        # SECOND: Try any 720p progressive MP4
        for fmt in formats:
            height = fmt.get('height', 0)
            ext = fmt.get('ext', '').lower()
            has_video = fmt.get('vcodec') != 'none'
            has_audio = fmt.get('acodec') != 'none'
            
            if height == 720 and ext == 'mp4' and has_video and has_audio:
                logger.info(f"🎯 Found 720p MP4: {fmt.get('format_id')}")
                return fmt['format_id'], '720p'
        
        # THIRD: Try 1080p progressive MP4
        for fmt in formats:
            height = fmt.get('height', 0)
            ext = fmt.get('ext', '').lower()
            has_video = fmt.get('vcodec') != 'none'
            has_audio = fmt.get('acodec') != 'none'
            
            if height >= 1080 and ext == 'mp4' and has_video and has_audio:
                logger.info(f"🎯 Found {height}p MP4: {fmt.get('format_id')}")
                return fmt['format_id'], f"{height}p"
        
        # FOURTH: Try combined format for 720p/1080p
        # Check if we have separate video and audio streams
        has_720p_video = any(
            fmt.get('height', 0) >= 720 and 
            fmt.get('vcodec') != 'none' and 
            fmt.get('acodec') == 'none'
            for fmt in formats
        )
        
        has_audio = any(
            fmt.get('acodec') != 'none' and 
            fmt.get('vcodec') == 'none'
            for fmt in formats
        )
        
        if has_720p_video and has_audio:
            logger.info("🎯 Using combined 720p+ format")
            return 'bestvideo[height>=720]+bestaudio', '720p+'
        
        # FIFTH: Use yt-dlp's best 720p selector
        logger.info("⚠️ Using fallback: best[height>=720]")
        return 'best[height>=720]', '720p'
        
    except Exception as e:
        logger.error(f"Error selecting format: {e}")
        # Fallback to 720p selection
        return 'best[height>=720]', '720p'

@app.route('/download', methods=['POST'])
def download_video():
    """Download YouTube video - FORCE 720p or higher"""
    data = request.json
    if not data:
        return jsonify({'error': 'No JSON data provided'}), 400
    
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400
    
    # Fix URL
    url = url.replace('voutu.be', 'youtu.be').replace('ww.youtube.com', 'www.youtube.com')
    
    try:
        # Handle shorts URLs
        if 'shorts/' in url:
            video_id = url.split('shorts/')[-1].split('?')[0]
            url = f"https://www.youtube.com/watch?v={video_id}"
        
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

        # Select 720p or higher format
        format_string, resolution = select_720p_or_higher(info)
        logger.info(f"📥 Downloading {resolution} with format: {format_string}")

        # SIMPLE download options - no complex processing
        ydl_opts = {
            'format': format_string,
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

        # Verify download
        if not os.path.exists(output_path):
            return jsonify({'error': 'Download failed'}), 500
        
        file_size = os.path.getsize(output_path)
        if file_size == 0:
            os.remove(output_path)
            return jsonify({'error': 'Download failed - empty file'}), 500

        file_size_mb = file_size / (1024 * 1024)
        
        # Check if we actually got a decent file size
        # 11-second 720p video should be >1MB
        if duration > 5 and file_size_mb < 0.5:
            logger.warning(f"File too small for {resolution}: {file_size_mb}MB")
            # Might have gotten low quality despite our selection
        
        # Prepare response
        return jsonify({
            'success': True,
            'title': title[:200],
            'thumbnail': thumbnail,
            'duration': duration,
            'uploader': uploader[:100],
            'resolution': resolution,
            'size_bytes': file_size,
            'size_mb': round(file_size_mb, 2),
            'quality': 'high',
            'message': f"✅ {resolution} video ready: {file_size_mb:.2f}MB",
            'download_url': f"{BASE_URL}/downloads/{filename}",
            'id': unique_id,
            'filename': filename,
            'format': format_string
        })

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        error_msg = str(e)
        
        if "format not available" in error_msg.lower():
            # Try again with simpler format
            logger.info("Retrying with simpler format...")
            return jsonify({'error': 'Format not available, trying alternative...'}), 400
        elif "private" in error_msg.lower() or "restricted" in error_msg.lower():
            return jsonify({'error': 'Video is private or age-restricted'}), 400
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
        'service': 'YouTube 720p+ Downloader',
        'downloads_dir': True,
        'files_count': files_count,
        'base_url': BASE_URL,
        'timestamp': time.time()
    })

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded files"""
    if not filename or '..' in filename or '/' in filename or not filename.endswith('.mp4'):
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
    """Background cleanup"""
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
