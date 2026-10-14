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

2. (Optional) copy `.env.example` to `.env` and adjust it.

3. Run the app:
```bash
python main.py
```

The UI and API start on `http://localhost:4999` (change with `YTFIN_PORT`). The UI works on phones too.

## Configuration

### Environment (`.env`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `YTFIN_USERNAME` / `YTFIN_PASSWORD` | `admin` / `admin` | Login credentials |
| `YTFIN_SECRET` | random per start | Session signing key |
| `YTFIN_AUTH_ENABLED` | *(unset)* | Force login on (`true`) or off (`false`), overriding the UI setting |
| `YTFIN_DOWNLOAD_DIR` | `downloads` | Output folder |
| `YTFIN_HOST` / `YTFIN_PORT` | `0.0.0.0` / `4999` | Listen address |
| `YTFIN_DEBUG` | `false` | Flask debug mode |

### Settings (in the UI)

Open the ⚙️ button in the top bar. Settings are saved to `settings.json`:

- **Require login**: turn authentication on or off. Only disable it on a trusted network. If you lock yourself out, set `YTFIN_AUTH_ENABLED=false` in `.env` and restart.
- **Check sources automatically**, **check interval** and **latest videos per channel**
- **Default max resolution** (`best`, `2160p` … `360p`)
- **Subtitle languages** (comma separated, empty for none)
- **Embed thumbnail** and **write `.nfo` metadata**
- **Finished jobs to keep** in the list

## Auto Sources

Add playlist and channel URLs in the **Sources** card, or edit these files directly (one URL per line, `#` for comments):

- [playlists.txt](playlists.txt)
- [channels.txt](channels.txt)

The monitor checks them on the configured interval and queues any videos that haven't been downloaded yet. Use **Check now** to run a check immediately. Errors for individual sources are shown in the UI and don't stop the other sources.

## What Gets Saved

For each video, the app writes:

- `Title [videoId].mkv` (video + audio streams, stream copy)
- `Title [videoId].jpg` (thumbnail)
- `Title [videoId].en.srt` (English subtitles, auto + manual)
- `Title [videoId].nfo` (Kodi-style metadata for Jellyfin)
- `Title [videoId].info.json` (raw yt-dlp metadata)

## API

All endpoints except `/health` need a logged-in session while login is enabled. Requests that change state must send JSON (`Content-Type: application/json`). Errors always come back as `{"success": false, "error": "..."}` with a matching HTTP status.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Health check, FFmpeg availability |
| `GET` | `/api/status` | Queue state, counts, monitor status |
| `GET` | `/api/jobs` | All jobs with progress |
| `POST` | `/api/jobs` | Queue a video: `{"url": "...", "resolution": "1080p"}` (resolution optional) |
| `POST` | `/api/jobs/<id>/cancel` | Cancel a queued or running job |
| `POST` | `/api/jobs/<id>/retry` | Retry a failed or canceled job |
| `DELETE` | `/api/jobs/<id>` | Remove a finished job from the list |
| `POST` | `/api/jobs/clear` | Remove all finished jobs |
| `POST` | `/api/pause`, `/api/resume`, `/api/stop-current` | Queue controls |
| `POST` | `/api/check/playlists`, `/api/check/channels` | Run a source check in the background |
| `GET` / `POST` | `/api/sources` | Read or save `playlists` and `channels` text |
| `GET` / `POST` | `/api/settings` | Read or update settings (partial updates allowed) |
| `GET` | `/api/probe?url=...` | Title, channel, resolutions and FPS for a video |

The original `POST /download`, `GET /resolutions` and `GET /fps` endpoints still work.

**Example** (with login disabled):
```bash
curl -X POST http://localhost:4999/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","resolution":"720p"}'
```

## Features

- **Playlist + Channel Monitor**: Checks configured sources on a schedule
- **Queue Worker**: Runs downloads in the background with live progress, pause/resume, cancel and retry
- **Stream Copy Remux**: Fast MKV output without re-encoding
- **Jellyfin-Friendly Sidecars**: Thumbnails, subtitles, and `.nfo` metadata
- **Responsive UI**: Works on desktop and mobile, with light and dark themes
- **Settings**: Toggle login, monitoring, resolution, subtitles and more from the UI

## Download Location

Downloaded videos are saved in `YTFIN_DOWNLOAD_DIR` (default `downloads/`) in MKV format. Partial files are cleaned up when a download fails or is canceled.

## Testing

Run the test script to verify the API:

`test.py` needs login turned off (`YTFIN_AUTH_ENABLED=false`).

```bash
# Terminal 1: Start the API
YTFIN_AUTH_ENABLED=false python main.py

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
- Jellyfin should be configured to “Use local metadata” and “Use external subtitles”

