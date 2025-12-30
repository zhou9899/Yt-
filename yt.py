from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import re
import os
import uuid
import threading
import time
from datetime import datetime, timedelta
import shutil
import subprocess

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
CLEANUP_INTERVAL = 3600  # Cleanup files older than 1 hour
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def convert_shorts_url(url: str) -> str:
    """Convert YouTube Shorts URL to regular watch URL"""
    patterns = [
        r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)',
        r'(https?://)?youtu\.be/([a-zA-Z0-9_-]+)'
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

def merge_audio_video(video_path, audio_path, output_path):
    """Merge video and audio files using ffmpeg"""
    try:
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-i', audio_path,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-strict', 'experimental',
            '-y',  # Overwrite output file if exists
            output_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg merge error: {e}")
        return False
    except Exception as e:
        print(f"Merge error: {e}")
        return False

def download_highest_quality(url, output_filename):
    """Download video in highest available quality"""
    try:
        # First, get available formats
        ydl_info_opts = {
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])
            
            # Prefer formats with both video and audio first
            combined_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') != 'none']
            
            # Sort by quality: 1080p, 720p, 480p, etc.
            quality_order = ['1080', '720', '480', '360', '240', '144']
            quality_scores = {q: i for i, q in enumerate(quality_order)}
            
            def get_quality_score(format_dict):
                height = format_dict.get('height', 0)
                for q in quality_order:
                    if height and str(height).startswith(q):
                        return quality_scores[q]
                return len(quality_order)  # Lowest priority
            
            # Try to find best combined format
            if combined_formats:
                combined_formats.sort(key=lambda x: get_quality_score(x))
                best_combined = combined_formats[0]
                format_id = best_combined['format_id']
                print(f"Downloading combined format: {best_combined.get('format_note', 'Unknown')} ({format_id})")
            else:
                # If no combined format, try to get separate video and audio
                print("No combined format found, trying separate audio/video...")
                
                # Get best video-only format
                video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') == 'none']
                if not video_formats:
                    raise Exception("No suitable video formats found")
                
                video_formats.sort(key=lambda x: get_quality_score(x))
                video_format = video_formats[0]
                
                # Get best audio-only format
                audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
                if not audio_formats:
                    raise Exception("No suitable audio formats found")
                
                audio_formats.sort(key=lambda x: x.get('abr', 0), reverse=True)
                audio_format = audio_formats[0]
                
                # Download video and audio separately
                temp_dir = os.path.dirname(output_filename)
                video_temp = os.path.join(temp_dir, f"video_{uuid.uuid4()}.mp4")
                audio_temp = os.path.join(temp_dir, f"audio_{uuid.uuid4()}.mp4")
                
                # Download video
                print(f"Downloading video: {video_format.get('format_note', 'Unknown')}")
                ydl_video_opts = {
                    'format': video_format['format_id'],
                    'outtmpl': video_temp.replace('.mp4', '.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'postprocessors': [],
                }
                
                with yt_dlp.YoutubeDL(ydl_video_opts) as ydl:
                    ydl.download([url])
                
                # Download audio
                print(f"Downloading audio: {audio_format.get('format_note', 'Unknown')}")
                ydl_audio_opts = {
                    'format': audio_format['format_id'],
                    'outtmpl': audio_temp.replace('.mp4', '.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'postprocessors': [],
                }
                
                with yt_dlp.YoutubeDL(ydl_audio_opts) as ydl:
                    ydl.download([url])
                
                # Find actual downloaded files (extensions might vary)
                video_file = None
                audio_file = None
                for f in os.listdir(temp_dir):
                    if f.startswith(os.path.basename(video_temp).split('_')[0]):
                        video_file = os.path.join(temp_dir, f)
                    elif f.startswith(os.path.basename(audio_temp).split('_')[0]):
                        audio_file = os.path.join(temp_dir, f)
                
                if not video_file or not audio_file:
                    raise Exception("Could not find downloaded video/audio files")
                
                # Merge them
                print("Merging audio and video...")
                if merge_audio_video(video_file, audio_file, output_filename):
                    # Clean up temp files
                    try:
                        if os.path.exists(video_file):
                            os.remove(video_file)
                        if os.path.exists(audio_file):
                            os.remove(audio_file)
                    except:
                        pass
                    print(f"Download completed: {output_filename}")
                    return
                else:
                    raise Exception("Failed to merge audio and video")
        
        # If using combined format
        if 'format_id' in locals():
            ydl_opts = {
                'format': format_id,
                'outtmpl': output_filename,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            print(f"Download completed: {output_filename}")
            
    except Exception as e:
        print(f"Download failed: {e}")
        # Fallback to lower quality if high quality fails
        try:
            print("Trying fallback to lower quality...")
            ydl_fallback = {
                'format': 'best[height<=720]/best[height<=480]/best',
                'outtmpl': output_filename,
                'quiet': True,
                'no_warnings': True,
            }
            with yt_dlp.YoutubeDL(ydl_fallback) as ydl:
                ydl.download([url])
            print(f"Fallback download completed: {output_filename}")
        except Exception as e2:
            print(f"Fallback also failed: {e2}")
            raise

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
        download_highest_quality(url, filename)
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

            # Get available formats
            formats = info.get('formats', [])
            available_qualities = set()
            for f in formats:
                if f.get('height'):
                    available_qualities.add(f['height'])
            
            # Check if 1080p is available
            has_1080p = any(q >= 1080 for q in available_qualities)
            has_720p = any(q >= 720 for q in available_qualities)
            
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
                'message': f"Downloading '{info.get('title', 'video')}' in highest quality...",
                'title': info.get('title', 'Unknown Title'),
                'duration': info.get('duration', 0),
                'uploader': info.get('uploader', 'Unknown'),
                'thumbnail': info.get('thumbnail'),
                'download_id': file_id,
                'filename': f"{file_id}.mp4",
                'available_qualities': sorted(list(available_qualities)),
                'has_1080p': has_1080p,
                'has_720p': has_720p
            })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': 'Video not available or restricted', 'details': str(e)}), 400
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    """Serve downloaded file"""
    # Security check
    if not re.match(r'^[a-f0-9\-]{36}\.mp4$', filename):
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

if __name__ == '__main__':
    # Check if ffmpeg is available (needed for merging)
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        print("FFmpeg is available for merging audio/video streams")
    except:
        print("Warning: FFmpeg not found. Some high-quality videos may not download properly.")
    
    # Start cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()

    # Run Flask app
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    )

