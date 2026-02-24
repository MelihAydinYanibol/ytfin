# YouTube Video Downloader API

A Flask API and UI for downloading YouTube videos, plus background monitoring of playlists and channels. Uses yt-dlp with FFmpeg support for format conversion and FPS adjustment.

## Prerequisites

- Python 3.7+
- FFmpeg (optional, for MKV conversion and FPS control)

### Installing FFmpeg

**Windows (using Chocolatey):**
```bash
choco install ffmpeg
```

**Windows (manual):**
1. Download from https://ffmpeg.org/download.html
2. Extract and add to your PATH

**macOS:**
```bash
brew install ffmpeg
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install ffmpeg
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the API:
```bash
python main.py
```

The API and UI will start on `http://localhost:5000`

## Auto Sources

Add your playlist and channel URLs (one per line):

- [playlists.txt](playlists.txt)
- [channels.txt](channels.txt)

The monitor checks every 15 minutes and queues any new videos it has not downloaded yet.

## Endpoints

### GET /resolutions
Get available resolutions for a video.

**Query Parameters:**
- `url` (required): YouTube video URL

**Example:**
```bash
curl "http://localhost:5000/resolutions?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

**Response:**
```json
{
  "success": true,
  "title": "Video Title",
  "resolutions": ["1080p", "720p", "480p", "360p", "240p"]
}
```

### GET /fps
Get available frame rates (FPS) for a video.

**Query Parameters:**
- `url` (required): YouTube video URL

**Example:**
```bash
curl "http://localhost:5000/fps?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

**Response:**
```json
{
  "success": true,
  "title": "Video Title",
  "available_fps": [60, 30],
  "original_fps": 30
}
```

### POST /download
Queue a video download at specified resolution with optional FPS control and codec selection.

**Request Body:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "resolution": "720p",
  "fps": 30,
  "codec": "hevc"
}
```

**Parameters:**
- `url` (required): YouTube video URL
- `resolution` (required): Target resolution (e.g., "720p", "1080p")
- `fps` (optional): Frame rate for output video. If not specified, original FPS is preserved
- `codec` (optional): Video codec to use. Options: "h264" (default), "hevc". HEVC provides better compression

**Example with H.264 (default):**
```bash
curl -X POST http://localhost:5000/download \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","resolution":"720p"}'
```

**Example with HEVC (better compression):**
```bash
curl -X POST http://localhost:5000/download \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","resolution":"720p","codec":"hevc"}'
```

**Example with HEVC and FPS:**
```bash
curl -X POST http://localhost:5000/download \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","resolution":"720p","fps":30,"codec":"hevc"}'
```

**Response:**
```json
{
  "success": true,
  "job_id": "..."
}
```

### GET /health
Health check endpoint. Shows FFmpeg availability.

**Example:**
```bash
curl http://localhost:5000/health
```

**Response:**
```json
{
  "status": "ok",
  "ffmpeg_available": true
}
```

## Features

- **Multiple Resolution Support**: Download videos in the best available quality up to your specified resolution
- **FPS Information**: Query available frame rates for any video
- **Dual Codec Support**: Choose between H.264 and HEVC (H.265) encoding
  - **H.264**: Universal compatibility, wider device support
  - **HEVC**: Better compression (~50% smaller files), recommended for storage
- **FPS Control**: Set custom frame rate for the output video (requires FFmpeg)
- **Format Conversion**: Automatically converts downloaded videos to MKV format
- **Audio/Video Merging**: Automatically combines the best video and audio streams
- **Background Monitor**: Checks playlists and channels on a schedule
- **Download Queue**: Pause/resume queue and stop current downloads from the UI
- **Error Handling**: Comprehensive error responses for troubleshooting

## Download Location

Downloaded videos are saved in the `downloads/` directory in MKV format.

## Testing

Run the test script to verify the API:

```bash
# Terminal 1: Start the API
python main.py

# Terminal 2: Run tests
python test.py
```

The test script will:
1. Check API health
2. Fetch available resolutions
3. Fetch available FPS values
4. Download a test video at highest resolution using H.264
5. Download the same video using HEVC codec (better compression)
6. Download with HEVC and FPS conversion

## Notes

- Videos are automatically converted to MKV format
- **H.264 codec**: Universal compatibility, use with older devices
- **HEVC codec**: Better compression (up to 50% smaller), requires devices/players with HEVC support
- If the exact resolution isn't available, the closest lower resolution will be used
- FFmpeg must be installed for HEVC codec. Linux systems may need to install `libx265`
- The API runs in debug mode by default; change for production use
- Higher FPS values may result in larger file sizes

