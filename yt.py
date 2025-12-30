from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import threading
import time
from datetime import datetime, timedelta
import shutil

app = Flask(name)

DOWNLOAD_DIR = './downloads'
CLEANUP_INTERVAL = 3600  # Cleanup files older than 1 hour
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def convert_shorts_url(url: str) -> str:
"""Convert YouTube Shorts URL to regular watch URL"""
patterns = [
r'(https?://)?(www.)?youtube.com/shorts/([a-zA-Z0-9_-]+)',
r'(https?://)?youtu.be/([a-zA-Z0-9_-]+)'
]

for pattern in patterns:
match = re.match(pattern, url)
if match:
if 'youtu.be' in pattern:
video_id = match.group(2)
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

def download_video(url, filename):
"""Download video in background thread"""
try:
ydl_opts = {
'format': 'best[height<=720]/best[height<=480]/best',
'outtmpl': filename,
'quiet': True,
'no_warnings': True,
'postprocessors': [],
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
data = request.get_json()
if not data:
return jsonify({'error': 'Invalid JSON'}), 400

url = data.get('url')
if not url:
return jsonify({'error': 'URL required'}), 400

try:
url = convert_shorts_url(url)

# Extract video info    
ydl_opts_info = {    
    'quiet': True,    
    'no_warnings': True,    
    'extract_flat': False    
}    
    
with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:    
    info = ydl.extract_info(url, download=False)    
        
    if not info:    
        return jsonify({'error': 'Could not extract video information'}), 400    
        
    # Generate unique filename    
    file_id = str(uuid.uuid4())    
    temp_filename = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")    
        
    # Start background download    
    thread = threading.Thread(    
        target=download_video,     
        args=(url, temp_filename),    
        daemon=True    
    )    
    thread.start()    
        
    # Return immediate response    
    return jsonify({    
        'success': True,    
        'message': f"Downloading '{info.get('title', 'video')}'...",    
        'title': info.get('title', 'Unknown Title'),    
        'duration': info.get('duration', 0),    
        'uploader': info.get('uploader', 'Unknown'),    
        'thumbnail': info.get('thumbnail'),    
        'download_id': file_id,    
        'filename': f"{file_id}.mp4"    
    })

except yt_dlp.utils.DownloadError as e:
return jsonify({'error': 'Video not available or restricted', 'details': str(e)}), 400
except Exception as e:
return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
"""Serve downloaded file"""

Security check

if not re.match(r'^[a-f0-9-]{36}.mp4$', filename):
return jsonify({'error': 'Invalid filename'}), 400

filepath = os.path.join(DOWNLOAD_DIR, filename)

if not os.path.exists(filepath):
return jsonify({'error': 'File not found or expired'}), 404

return send_from_directory(
DOWNLOAD_DIR,
filename,
as_attachment=True,
download_name='video.mp4'
)

@app.route('/status/<file_id>')
def check_status(file_id):
"""Check if download is complete"""
filepath = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

if os.path.exists(filepath):
size = os.path.getsize(filepath)
return jsonify({
'status': 'ready',
'size': size,
'download_url': f"/downloads/{file_id}.mp4"
})
else:
return jsonify({'status': 'processing'})

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
return jsonify({'message': f'Cleaned up {deleted} files'})
except Exception as e:
return jsonify({'error': str(e)}), 500

if name == 'main':

Start cleanup thread

cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

Run Flask app

app.run(
host='0.0.0.0',
port=int(os.environ.get('PORT', 5000)),
debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
)

