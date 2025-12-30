from flask import Flask, request, Response, jsonify
import yt_dlp
import re

app = Flask(__name__)

def convert_shorts_url(url: str) -> str:
    """Convert Shorts URL to standard YouTube URL"""
    patterns = [
        r'(https?://)?(www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]+)',
        r'(https?://)?youtu\.be/([a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(3) if 'youtube.com/shorts' in pattern else match.group(2)
            return f"https://www.youtube.com/watch?v={video_id}"
    return url

@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'error': 'URL required'}), 400

    url = convert_shorts_url(data['url'])

    # Prepare yt-dlp options
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best[height<=480]/best[height<=360]',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        def generate():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # This will stream the video content
                for chunk in ydl.urlopen(url):
                    yield chunk

        return Response(generate(), mimetype='video/mp4')
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': 'Video not available or restricted', 'details': str(e)}), 400
    except Exception as e:
        return jsonify({'error': 'Internal server error', 'details': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
