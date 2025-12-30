from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS  # Add CORS support
import yt_dlp
import re
import os
import uuid
import threading
import time
from datetime import datetime, timedelta
import shutil

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

DOWNLOAD_DIR = './downloads'
CLEANUP_INTERVAL = 3600  # Cleanup files older than 1 hour
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Store download status
download_status = {}

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
                        # Remove from status tracking
                        file_id = filename.replace('.mp4', '')
                        if file_id in download_status:
                            del download_status[file_id]
                        print(f"Cleaned up: {filename}")
                    except Exception as e:
                        print(f"Error cleaning {filename}: {e}")

def download_video(url, filename, file_id):
    """Download video in background thread"""
    try:
        download_status[file_id] = {'status': 'downloading', 'progress': 0}
        
        ydl_opts = {
            'format': 'best[height<=720]',
            'outtmpl': filename,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [lambda d: progress_hook(d, file_id)],
            'postprocessors': [],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            },
            'noplaylist': True,
            'socket_timeout': 30,
            'retries': 3
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        download_status[file_id] = {'status': 'completed', 'progress': 100}
        print(f"Download completed: {filename}")

    except Exception as e:
        print(f"Download failed for {url}: {e}")
        download_status[file_id] = {'status': 'failed', 'error': str(e)}
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except:
            pass

def progress_hook(d, file_id):
    """Progress hook for downloads"""
    if d['status'] == 'downloading':
        progress = 0
        if d.get('_percent_str'):
            try:
                progress = float(d['_percent_str'].replace('%', '').strip())
            except:
                pass
        download_status[file_id] = {'status': 'downloading', 'progress': progress}
    elif d['status'] == 'finished':
        download_status[file_id] = {'status': 'processing', 'progress': 100}

@app.route('/download', methods=['POST', 'OPTIONS'])
def download_short():
    """Handle download requests"""
    if request.method == 'OPTIONS':
        return '', 200
        
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Invalid JSON'}), 400

    url = data.get('url')
    if not url:
        return jsonify({'success': False, 'error': 'URL required'}), 400

    try:
        url = convert_shorts_url(url)

        # Extract video info    
        ydl_opts_info = {    
            'quiet': True,    
            'no_warnings': True,
            'extract_flat': False,
            'socket_timeout': 15,
            'retries': 2
        }    
        
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:    
            info = ydl.extract_info(url, download=False)    
            
            if not info:    
                return jsonify({'success': False, 'error': 'Could not extract video information'}), 400    
            
            # Check video duration (reject long videos)
            duration = info.get('duration', 0)
            if duration > 180:  # Reject videos longer than 3 minutes
                return jsonify({'success': False, 'error': 'Video is too long (max 3 minutes)'}), 400
            
            # Generate unique filename    
            file_id = str(uuid.uuid4())    
            temp_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")    
            
            # Start background download    
            thread = threading.Thread(    
                target=download_video,     
                args=(url, temp_filename, file_id),    
                daemon=True    
            )    
            thread.start()    
            
            # Return immediate response    
            return jsonify({    
                'success': True,    
                'message': f"Downloading '{info.get('title', 'video')}'...",    
                'title': info.get('title', 'Unknown Title'),    
                'duration': duration,    
                'uploader': info.get('uploader', 'Unknown'),    
                'thumbnail': info.get('thumbnail', ''),    
                'download_id': file_id,    
                'filename': f"{file_id}.mp4"    
            })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'success': False, 'error': 'Video not available or restricted', 'details': str(e)}), 400
    except Exception as e:
        print(f"Server error: {e}")
        return jsonify({'success': False, 'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded file"""
    
    # Security check
    if not re.match(r'^[a-f0-9-]{36}\.mp4$', filename):
        return jsonify({'success': False, 'error': 'Invalid filename'}), 400

    filepath = os.path.join(DOWNLOAD_DIR, filename)

    if not os.path.exists(filepath):
        return jsonify({'success': False, 'error': 'File not found or expired'}), 404

    try:
        return send_from_directory(
            DOWNLOAD_DIR,
            filename,
            as_attachment=True,
            download_name='shorts_video.mp4',
            mimetype='video/mp4'
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/status/<file_id>')
def check_status(file_id):
    """Check if download is complete"""
    # Security check
    if not re.match(r'^[a-f0-9-]{36}$', file_id):
        return jsonify({'success': False, 'error': 'Invalid file ID'}), 400
    
    filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

    if os.path.exists(filepath):
        try:
            size = os.path.getsize(filepath)
            # Check if file is fully downloaded (not empty)
            if size > 1024:  # At least 1KB
                return jsonify({
                    'success': True,
                    'status': 'ready',
                    'size': size,
                    'download_url': f"/downloads/{file_id}.mp4"
                })
            else:
                return jsonify({'success': True, 'status': 'processing'})
        except:
            return jsonify({'success': True, 'status': 'processing'})
    else:
        # Check status from memory
        if file_id in download_status:
            status_info = download_status[file_id]
            if status_info.get('status') == 'failed':
                return jsonify({'success': False, 'status': 'failed', 'error': status_info.get('error', 'Unknown error')})
        
        return jsonify({'success': True, 'status': 'processing'})

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Manually trigger cleanup"""
    try:
        deleted = 0
        for filename in os.listdir(DOWNLOAD_DIR):
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                    deleted += 1
                except:
                    pass
        download_status.clear()
        return jsonify({'success': True, 'message': f'Cleaned up {deleted} files'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'success': True, 'status': 'healthy', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    
    # Run Flask app
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true',
        threaded=True
    )
