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
    """Get video information"""
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

def get_working_format(info):
    """
    Get format that WORKS with WhatsApp
    Uses the same format as your working bot
    """
    try:
        formats = info.get('formats', [])
        
        # EXACT format from your working bot:
        # "best[ext=mp4][vcodec!=none][acodec!=none]"
        
        # Find progressive MP4 formats (video+audio in one)
        mp4_formats = []
        for fmt in formats:
            has_video = fmt.get('vcodec') != 'none'
            has_audio = fmt.get('acodec') != 'none'
            ext = fmt.get('ext', '').lower()
            height = fmt.get('height', 0)
            
            if has_video and has_audio and ext == 'mp4':
                # Get resolution
                resolution = f"{height}p" if height > 0 else "Unknown"
                
                mp4_formats.append({
                    'format_id': fmt['format_id'],
                    'height': height,
                    'resolution': resolution,
                    'filesize': fmt.get('filesize') or fmt.get('filesize_approx') or 0,
                    'tbr': fmt.get('tbr', 0),
                    'vcodec': fmt.get('vcodec', ''),
                    'acodec': fmt.get('acodec', '')
                })
        
        if mp4_formats:
            # Sort by: height (highest first), bitrate (highest first)
            mp4_formats.sort(key=lambda x: (
                -x['height'],  # Highest resolution
                -x['tbr']      # Highest bitrate
            ))
            
            best = mp4_formats[0]
            height = best['height']
            
            # Choose format based on resolution
            if height >= 720:
                # For 720p+, use format 22 if available, else best
                for fmt in formats:
                    if fmt.get('format_id') == '22':
                        logger.info("🎯 Using format 22 (720p MP4)")
                        return '22', f"{height}p"
                
                logger.info(f"🎯 Using best MP4: {height}p")
                return f'best[ext=mp4][height<={height}][vcodec!=none][acodec!=none]', f"{height}p"
            else:
                logger.info(f"🎯 Using format 18 (360p MP4)")
                return '18', f"{height}p"
        
        # Fallback: Use the EXACT format from your working bot
        logger.info("⚠️ Using fallback: best[ext=mp4][vcodec!=none][acodec!=none]")
        return 'best[ext=mp4][vcodec!=none][acodec!=none]', 'Unknown'
        
    except Exception as e:
        logger.error(f"Error selecting format: {e}")
        # Ultimate fallback - your bot's working format
        return 'best[ext=mp4][vcodec!=none][acodec!=none]', 'Unknown'

@app.route('/download', methods=['POST'])
def download_video():
    """Download YouTube video - GUARANTEED WORKING FORMAT"""
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

        # Get WORKING format (same as your bot)
        format_string, resolution = get_working_format(info)
        logger.info(f"📥 Downloading with format: {format_string}")

        # SIMPLE download - same as your working bot
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
            'concurrent_fragment_downloads': 1,
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
            'quality': 'guaranteed',
            'message': f"✅ {resolution} video ready: {file_size_mb:.2f}MB",
            'download_url': f"{BASE_URL}/downloads/{filename}",
            'id': unique_id,
            'filename': filename,
            'format': format_string
        })

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download error: {e}")
        error_msg = str(e).lower()
        
        if "private" in error_msg or "restricted" in error_msg:
            return jsonify({'error': 'Video is private or age-restricted'}), 400
        elif "unavailable" in error_msg:
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
        'service': 'YouTube Downloader (Guaranteed Format)',
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
