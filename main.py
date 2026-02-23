from flask import Flask, request, jsonify
import yt_dlp
import os
import subprocess
import shutil
from pathlib import Path

app = Flask(__name__)

# Create downloads directory
DOWNLOAD_DIR = "downloads"
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

# Check if FFmpeg is available
FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None


def get_available_resolutions(url):
    """Extract available resolutions for a given video URL"""
    try:
        ydl_opts = {
            'quiet': False,
            'no_warnings': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Get available formats
            formats = info.get('formats', [])
            resolutions = set()
            
            for fmt in formats:
                if fmt.get('vcodec') != 'none':  # Only video formats
                    height = fmt.get('height')
                    if height:
                        resolutions.add(f"{height}p")
            
            # Sort resolutions
            resolution_list = sorted(list(resolutions), 
                                    key=lambda x: int(x[:-1]), 
                                    reverse=True)
            
            return {
                'success': True,
                'title': info.get('title'),
                'resolutions': resolution_list
            }
    
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def get_available_fps(url):
    """Extract available FPS values for a given video URL"""
    try:
        ydl_opts = {
            'quiet': False,
            'no_warnings': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Get available formats
            formats = info.get('formats', [])
            fps_values = set()
            
            for fmt in formats:
                if fmt.get('vcodec') != 'none':  # Only video formats
                    fps = fmt.get('fps')
                    if fps:
                        fps_values.add(int(fps))
            
            # Sort FPS values in descending order
            fps_list = sorted(list(fps_values), reverse=True)
            
            return {
                'success': True,
                'title': info.get('title'),
                'available_fps': fps_list,
                'original_fps': formats[0].get('fps') if formats else None
            }
    
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def download_video(url, resolution, fps=None, codec='h264'):
    """Download video at specified resolution and convert to MKV with optional fps and codec"""
    try:
        # Validate codec
        valid_codecs = ['h264', 'hevc']
        if codec not in valid_codecs:
            return {
                'success': False,
                'error': f'Invalid codec. Supported codecs: {", ".join(valid_codecs)}'
            }
        
        # Format selection string
        format_string = f"bestvideo[height<={resolution[:-1]}]+bestaudio/best[height<={resolution[:-1]}]"
        
        # Download with temporary name
        temp_filename = os.path.join(DOWNLOAD_DIR, '%(title)s_temp.%(ext)s')
        
        ydl_opts = {
            'format': format_string,
            'outtmpl': temp_filename,
            'quiet': False,
            'no_warnings': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            temp_file_path = ydl.prepare_filename(info)
            title = info.get('title', 'video')
            
            # Convert to MKV with optional fps
            if FFMPEG_AVAILABLE:
                mkv_filename = os.path.join(DOWNLOAD_DIR, f"{title}.mkv")
                
                # Map codec to ffmpeg encoder
                codec_map = {
                    'h264': 'libx264',
                    'hevc': 'libx265'
                }
                
                # Build FFmpeg command
                ffmpeg_cmd = [
                    'ffmpeg',
                    '-i', temp_file_path,
                    '-c:v', codec_map[codec],  # Select video codec
                    '-c:a', 'aac',      # Audio codec
                    '-preset', 'medium'  # Encoding speed/quality
                ]
                
                # Add fps parameter if specified
                if fps:
                    ffmpeg_cmd.extend(['-r', str(fps)])
                
                ffmpeg_cmd.append(mkv_filename)
                
                # Run FFmpeg
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    # Remove temporary file
                    try:
                        os.remove(temp_file_path)
                    except:
                        pass
                    
                    return {
                        'success': True,
                        'message': 'Download and conversion completed',
                        'filename': os.path.basename(mkv_filename),
                        'title': title,
                        'format': 'mkv',
                        'codec': codec,
                        'fps': fps if fps else 'original'
                    }
                else:
                    return {
                        'success': False,
                        'error': f'FFmpeg conversion failed: {result.stderr}'
                    }
            else:
                return {
                    'success': False,
                    'error': 'FFmpeg is not installed. Please install FFmpeg to enable MKV conversion.'
                }
    
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


@app.route('/resolutions', methods=['GET'])
def get_resolutions():
    """GET endpoint to fetch available resolutions for a video"""
    url = request.args.get('url')
    
    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400
    
    result = get_available_resolutions(url)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route('/fps', methods=['GET'])
def get_fps():
    """GET endpoint to fetch available FPS values for a video"""
    url = request.args.get('url')
    
    if not url:
        return jsonify({'error': 'URL parameter is required'}), 400
    
    result = get_available_fps(url)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route('/download', methods=['POST'])
def download():
    """POST endpoint to download video at specified resolution and optional fps/codec"""
    data = request.get_json()
    
    url = data.get('url') if data else None
    resolution = data.get('resolution') if data else None
    fps = data.get('fps') if data else None
    codec = data.get('codec', 'h264') if data else 'h264'
    
    if not url or not resolution:
        return jsonify({'error': 'URL and resolution parameters are required'}), 400
    
    # Validate resolution format
    if not resolution.endswith('p'):
        return jsonify({'error': 'Resolution must be in format like "720p"'}), 400
    
    # Validate fps if provided
    if fps:
        try:
            fps = int(fps)
            if fps <= 0:
                return jsonify({'error': 'FPS must be a positive number'}), 400
        except (ValueError, TypeError):
            return jsonify({'error': 'FPS must be a valid number'}), 400
    
    result = download_video(url, resolution, fps, codec)
    
    if result['success']:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'ffmpeg_available': FFMPEG_AVAILABLE
    }), 200


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
