from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import threading
import time
from datetime import datetime, timedelta

app = Flask(__name__)

# Enable CORS manually (no flask_cors dependency)
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

DOWNLOAD_DIR = './downloads'
CLEANUP_INTERVAL = 1800  # Cleanup files older than 30 minutes
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def convert_shorts_url(url: str) -> str:
    """Convert YouTube Shorts URL to regular watch URL"""
    patterns = [
        r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)',
        r'(https?://)?youtu\.be/([a-zA-Z0-9_-]+)',
        r'(https?://)?(www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)'
    ]
    
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            if 'youtu.be' in pattern:
                video_id = match.group(2)
            elif 'watch' in pattern:
                video_id = match.group(3)
            else:
                video_id = match.group(3)
            return f"https://www.youtube.com/watch?v={video_id}"
    return url

def cleanup_old_files():
    """Remove files older than CLEANUP_INTERVAL"""
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = datetime.now()
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(filepath):
                file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                if now - file_time > timedelta(seconds=CLEANUP_INTERVAL):
                    try:
                        os.remove(filepath)
                        print(f"Cleaned up: {filename}")
                    except Exception as e:
                        print(f"Error cleaning {filename}: {e}")

def download_video(url, filename, file_id):
    """Download video in background thread"""
    try:
        print(f"Starting download for {file_id}")
        
        # Simple yt-dlp options
        ydl_opts = {
            'format': 'best[height<=720][ext=mp4]',
            'outtmpl': filename,
            'quiet': False,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'skip_unavailable_fragments': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        print(f"Download completed: {filename}")

    except Exception as e:
        print(f"Download failed for {url}: {e}")
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except:
            pass

@app.route('/download', methods=['POST'])
def download_short():
    """Handle download requests"""
    try:
        if not request.is_json:
            return jsonify({'success': False, 'error': 'Content-Type must be application/json'}), 400
            
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Invalid JSON'}), 400

        url = data.get('url')
        if not url:
            return jsonify({'success': False, 'error': 'URL required'}), 400

        # Convert to proper URL
        url = convert_shorts_url(url)
        
        # Simple validation
        if not ('youtube.com' in url or 'youtu.be' in url):
            return jsonify({'success': False, 'error': 'Invalid YouTube URL'}), 400

        print(f"Processing URL: {url}")
        
        # Generate unique ID
        file_id = str(uuid.uuid4())
        temp_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
        
        # Get video info first
        ydl_opts_info = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 10,
            'retries': 2
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    return jsonify({'success': False, 'error': 'Could not get video info'}), 400
                
                duration = info.get('duration', 0)
                # Allow longer videos but warn
                if duration > 300:  # 5 minutes
                    return jsonify({
                        'success': False, 
                        'error': f'Video is {duration} seconds long (max 300 seconds for Shorts)'
                    }), 400
                
                # Start background download
                thread = threading.Thread(
                    target=download_video,
                    args=(url, temp_filename, file_id),
                    daemon=True
                )
                thread.start()
                
                return jsonify({
                    'success': True,
                    'message': f"Downloading '{info.get('title', 'video')}'...",
                    'title': info.get('title', 'YouTube Video'),
                    'duration': duration,
                    'thumbnail': info.get('thumbnail', ''),
                    'download_id': file_id,
                    'filename': f"{file_id}.mp4"
                })
                
        except yt_dlp.utils.DownloadError as e:
            return jsonify({'success': False, 'error': f'YouTube error: {str(e)}'}), 400
                
    except Exception as e:
        print(f"Server error in /download: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/status/<file_id>')
def check_status(file_id):
    """Check if download is complete"""
    try:
        # Basic validation
        if not re.match(r'^[a-f0-9-]{36}$', file_id):
            return jsonify({'success': False, 'error': 'Invalid file ID'}), 400
        
        filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            # Check if file is valid (at least 10KB)
            if size > 10240:
                return jsonify({
                    'success': True,
                    'status': 'ready',
                    'size': size,
                    'download_url': f"/downloads/{file_id}.mp4"
                })
            else:
                return jsonify({'success': True, 'status': 'processing'})
        else:
            return jsonify({'success': True, 'status': 'processing'})
            
    except Exception as e:
        print(f"Error in /status: {e}")
        return jsonify({'success': False, 'error': 'Status check failed'}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded file"""
    try:
        # Security check
        if not re.match(r'^[a-f0-9-]{36}\.mp4$', filename):
            return jsonify({'success': False, 'error': 'Invalid filename'}), 400

        filepath = os.path.join(DOWNLOAD_DIR, filename)

        if not os.path.exists(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
            
        # Check file size
        size = os.path.getsize(filepath)
        if size < 10240:  # Less than 10KB
            return jsonify({'success': False, 'error': 'File is incomplete'}), 404

        return send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=True,
            download_name='shorts_video.mp4'
        )
    except Exception as e:
        print(f"Error serving file: {e}")
        return jsonify({'success': False, 'error': 'File service error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'success': True, 'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/', methods=['GET'])
def index():
    """Root endpoint"""
    return jsonify({
        'success': True,
        'message': 'YouTube Shorts Downloader API',
        'endpoints': {
            'POST /download': 'Start download',
            'GET /status/<id>': 'Check download status',
            'GET /downloads/<filename>': 'Download video',
            'GET /health': 'Health check'
        }
    })

if __name__ == '__main__':
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    
    print("Starting YouTube Shorts Downloader API...")
    
    # Get port from environment or use default
    port = int(os.environ.get('PORT', 5000))
    
    # Run with production settings for Railway
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True
    )
