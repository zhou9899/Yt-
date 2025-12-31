from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import time
import json
from threading import Thread
import subprocess
from pathlib import Path

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
COMPRESSED_DIR = './compressed'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(COMPRESSED_DIR, exist_ok=True)

BASE_URL = "https://web-production-73a3d.up.railway.app"

# Cleanup settings
CLEANUP_INTERVAL = 60 * 60  # Run cleanup every hour
FILE_LIFETIME = 60 * 60     # Delete files older than 1 hour
MAX_SIZE_MB = 45  # Keep under 50MB with some buffer

# Convert Shorts URLs to normal watch URLs
def convert_shorts_url(url: str) -> str:
    match = re.match(r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)', url)
    if match:
        video_id = match.group(3)
        return f"https://www.youtube.com/watch?v={video_id}"
    return url

def get_video_info(url):
    """Get video information without downloading"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def compress_video(input_path, output_path, target_size_mb):
    """Compress video to target size using ffmpeg"""
    try:
        # Get video duration
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
               '-of', 'default=noprint_wrappers=1:nokey=1', input_path]
        duration = float(subprocess.check_output(cmd).decode().strip())
        
        # Calculate target bitrate (in kbps)
        target_bitrate = int((target_size_mb * 8192) / duration)  # Convert MB to kilobits
        
        # Limit bitrate for reasonable quality
        target_bitrate = min(target_bitrate, 2500)  # Max 2500 kbps for good quality
        
        # Compress video
        cmd = [
            'ffmpeg', '-i', input_path,
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',  # Good quality
            '-maxrate', f'{target_bitrate}k',
            '-bufsize', f'{target_bitrate*2}k',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-movflags', '+faststart',
            '-y',  # Overwrite output file
            output_path
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"Compression failed: {e}")
        return False

def get_best_format(info, max_height=1080):
    """Select best format within constraints"""
    formats = info.get('formats', [])
    
    # Filter for video-only formats first
    video_formats = [f for f in formats if f.get('vcodec') != 'none']
    
    # Sort by resolution, preferring mp4
    video_formats.sort(key=lambda x: (
        -int(x.get('height', 0) or 0) if x.get('height') else 0,
        -int(x.get('width', 0) or 0) if x.get('width') else 0,
        'mp4' in x.get('ext', '')
    ))
    
    # Select best format under max_height
    for fmt in video_formats:
        height = fmt.get('height')
        if height and height <= max_height:
            return fmt['format_id']
    
    return 'best[height<=720]'

# Download endpoint
@app.route('/download', methods=['POST'])
def download_video():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL required'}), 400

    url = convert_shorts_url(url)
    unique_id = str(uuid.uuid4())
    temp_filename = os.path.join(DOWNLOAD_DIR, f"{unique_id}.mp4")
    compressed_filename = os.path.join(COMPRESSED_DIR, f"{unique_id}.mp4")

    try:
        # Get video info
        info = get_video_info(url)
        title = info.get('title', 'video')
        
        # Select best format
        format_id = get_best_format(info)
        
        # Download video
        ydl_opts = {
            'format': format_id,
            'outtmpl': temp_filename,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Check file size
        file_size_mb = os.path.getsize(temp_filename) / (1024 * 1024)
        
        if file_size_mb > MAX_SIZE_MB:
            # Compress if too large
            if compress_video(temp_filename, compressed_filename, MAX_SIZE_MB):
                final_path = compressed_filename
                compressed_size = os.path.getsize(compressed_filename) / (1024 * 1024)
                message = f"Video compressed from {file_size_mb:.1f}MB to {compressed_size:.1f}MB"
            else:
                final_path = temp_filename
                message = f"Video is {file_size_mb:.1f}MB (compression failed)"
        else:
            final_path = temp_filename
            message = f"Video is {file_size_mb:.1f}MB"
        
        # Get thumbnail
        thumbnail = info.get('thumbnail') or info.get('thumbnails', [{}])[0].get('url', '')
        
        # Get duration
        duration = info.get('duration', 0)
        
        return jsonify({
            'success': True,
            'title': title,
            'thumbnail': thumbnail,
            'duration': duration,
            'size_mb': round(os.path.getsize(final_path) / (1024 * 1024), 1),
            'message': message,
            'download_url': f"{BASE_URL}/downloads/{os.path.basename(final_path)}",
            'original_size': round(file_size_mb, 1)
        })
        
    except Exception as e:
        # Cleanup on error
        for path in [temp_filename, compressed_filename]:
            if os.path.exists(path):
                os.remove(path)
        return jsonify({'error': str(e)}), 500

# Serve downloads
@app.route('/downloads/<filename>')
def serve_download(filename):
    # Check if in compressed or downloads directory
    compressed_path = os.path.join(COMPRESSED_DIR, filename)
    download_path = os.path.join(DOWNLOAD_DIR, filename)
    
    if os.path.exists(compressed_path):
        return send_from_directory(COMPRESSED_DIR, filename, as_attachment=True)
    elif os.path.exists(download_path):
        return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)
    else:
        return jsonify({'error': 'File not found'}), 404

# Cleanup function
def cleanup_files():
    while True:
        now = time.time()
        for directory in [DOWNLOAD_DIR, COMPRESSED_DIR]:
            for f in os.listdir(directory):
                filepath = os.path.join(directory, f)
                if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > FILE_LIFETIME:
                    try:
                        os.remove(filepath)
                        print(f"Deleted old file: {filepath}")
                    except Exception as e:
                        print(f"Failed to delete {filepath}: {e}")
        time.sleep(CLEANUP_INTERVAL)

Thread(target=cleanup_files, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
