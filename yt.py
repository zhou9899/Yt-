from flask import Flask, request, jsonify
import yt_dlp
import re

app = Flask(__name__)

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
            'format': 'best[height<=720]',
            'noplaylist': True,
        }

        # Only extract URL, no download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_url = info.get('url')

        return jsonify({
            'message': 'Short URL extracted successfully',
            'title': info.get('title'),
            'thumbnail': info.get('thumbnail'),
            'video_url': video_url
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
