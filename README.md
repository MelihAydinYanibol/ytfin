# YouTube to Jellyfin Downloader

A Flask app with a small UI that keeps Jellyfin libraries fed with YouTube content. It monitors playlist and channel URLs, queues new videos, and downloads them into the `downloads/` folder. Each download is remuxed into MKV using stream copy (fast, no re-encode), and sidecar metadata is written so Jellyfin can read thumbnails, subtitles, descriptions, and channel names.

## Prerequisites

- Python 3.7+
- FFmpeg (required for MKV remuxing and thumbnail embedding)

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

## What Gets Saved

For each video, the app writes:

- `Title [videoId].mkv` (video + audio streams, stream copy)
- `Title [videoId].jpg` (thumbnail)
- `Title [videoId].en.srt` (English subtitles, auto + manual)
- `Title [videoId].nfo` (Kodi-style metadata for Jellyfin)
- `Title [videoId].info.json` (raw yt-dlp metadata)

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
Queue a video download at specified resolution. This is enqueued and processed by the download worker.

**Request Body:**
```json
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "resolution": "720p",
  "fps": 30,
  "codec": "copy"
}
```

**Parameters:**
- `url` (required): YouTube video URL
- `resolution` (required): Target resolution (e.g., "720p", "1080p")
- `fps` (optional): Not supported in stream copy mode (leave empty)
- `codec` (optional): Only "copy" is supported (stream copy)

**Example (stream copy):**
```bash
curl -X POST http://localhost:5000/download \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","resolution":"720p"}'
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

- **Playlist + Channel Monitor**: Checks configured sources on a schedule
- **Queue Worker**: Runs downloads in the background, with pause/resume/stop
- **Stream Copy Remux**: Fast MKV output without re-encoding
- **Jellyfin-Friendly Sidecars**: Thumbnails, subtitles, and `.nfo` metadata
- **UI Controls**: Manual add, manual checks, and source editor

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
4. Queue a test download

## Notes

- Stream copy is fast and preserves the source quality (no re-encode)
- If the exact resolution isn't available, the closest lower resolution will be used
- The API runs in debug mode by default; change for production use
- Jellyfin should be configured to “Use local metadata” and “Use external subtitles”

