from flask import Flask, request, jsonify, send_from_directory
import yt_dlp
import os
import uuid
import re

app = Flask(__name__)

DOWNLOAD_DIR = './downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def convert_shorts_url(url: str) -> str:
    match = re.match(r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)', url)
    if match:
        video_id = match.group(3)
        return f"https://www.youtube.com/watch?v={video_id}"
    return url

@app.route('/download', methods=['POST'])
def download_short():
    data = request.json
    url = data.get('url')

    if not url:
        return jsonify({'error': 'URL required'}), 400

    url = convert_shorts_url(url)

    try:
        ydl_opts = {
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.%(ext)s"),
            'format': 'best[height<=720]',
            'noplaylist': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

        return jsonify({
            'message': 'Short downloaded successfully',
            'filename': os.path.basename(filename),
            'download_url': f"/downloads/{os.path.basename(filename)}"
        })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f'Failed to download short: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': f'Unexpected error: {str(e)}'}), 500

@app.route('/downloads/<filename>')
def serve_download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

# Remove the if __name__ == '__main__' block for production
