from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from dotenv import load_dotenv
import yt_dlp
from yt_dlp.utils import sanitize_filename
import os
import urllib.parse
import subprocess
import shutil
import threading
import time
import json
import uuid
import logging
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("YTFIN_SECRET", "change-this-secret")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Create downloads directory
DOWNLOAD_DIR = "downloads"
Path(DOWNLOAD_DIR).mkdir(exist_ok=True)

PLAYLISTS_FILE = "playlists.txt"
CHANNELS_FILE = "channels.txt"
STATE_FILE = "state.json"

CHECK_INTERVAL_SECONDS = 15 * 60
DEFAULT_MAX_RESOLUTION = "1080p"
DEFAULT_CODEC = "copy"
DEFAULT_FPS = None

ADMIN_USERNAME = os.environ.get("YTFIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("YTFIN_PASSWORD", "admin")
SESSION_TOKEN = uuid.uuid4().hex

# Check if FFmpeg is available
FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None


def _is_authenticated():
    return session.get("user") == ADMIN_USERNAME and session.get("token") == SESSION_TOKEN


@app.before_request
def _require_auth():
    public_paths = ("/login", "/logout", "/health")
    if request.path.startswith("/static/"):
        return None
    if request.path in public_paths:
        return None
    if _is_authenticated():
        return None
    if request.path.startswith("/api/") or request.path == "/download":
        return jsonify({"error": "Unauthorized"}), 401
    return redirect(url_for("login"))


def _ensure_source_files():
    for path in (PLAYLISTS_FILE, CHANNELS_FILE):
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("")


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {"downloaded_ids": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            if not isinstance(data, dict):
                return {"downloaded_ids": []}
            return {"downloaded_ids": list(set(data.get("downloaded_ids", [])))}
    except (json.JSONDecodeError, OSError):
        return {"downloaded_ids": []}


def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
    except OSError:
        logging.exception("Failed to write state file")


STATE_LOCK = threading.Lock()
STATE = _load_state()
INFLIGHT_LOCK = threading.Lock()
INFLIGHT_IDS = set()


def _extract_video_id_from_name(file_name):
    if "[" not in file_name or "]" not in file_name:
        return None
    return file_name.split("[")[-1].split("]")[0]


def _has_downloaded_file(video_id):
    if not video_id:
        return False
    marker = f"[{video_id}]"
    for path in Path(DOWNLOAD_DIR).iterdir():
        if not path.is_file():
            continue
        if marker in path.name and path.suffix.lower() in (".mkv", ".mp4"):
            return True
    return False


def _bootstrap_state_from_downloads():
    updated = False
    for path in Path(DOWNLOAD_DIR).iterdir():
        if not path.is_file() or path.suffix.lower() not in (".mkv", ".mp4", ".nfo", ".json"):
            continue
        name = path.stem
        if path.suffix.lower() == ".json" and not path.name.endswith(".info.json"):
            continue
        video_id = _extract_video_id_from_name(name)
        if not video_id:
            continue
        with STATE_LOCK:
            if video_id not in STATE["downloaded_ids"]:
                STATE["downloaded_ids"].append(video_id)
                updated = True
    if updated:
        _save_state(STATE)


def _remember_downloaded(video_id):
    if not video_id:
        return
    with STATE_LOCK:
        if video_id not in STATE["downloaded_ids"]:
            STATE["downloaded_ids"].append(video_id)
            _save_state(STATE)


def _is_inflight(video_id):
    if not video_id:
        return False
    with INFLIGHT_LOCK:
        return video_id in INFLIGHT_IDS


def _mark_inflight(video_id):
    if not video_id:
        return
    with INFLIGHT_LOCK:
        INFLIGHT_IDS.add(video_id)


def _clear_inflight(video_id):
    if not video_id:
        return
    with INFLIGHT_LOCK:
        INFLIGHT_IDS.discard(video_id)


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


def _build_format_string(resolution):
    return f"bestvideo[height<={resolution[:-1]}]+bestaudio/best[height<={resolution[:-1]}]"


def _ensure_url(entry):
    if not entry:
        return None
    url = entry.get("url")
    if url and url.startswith("http"):
        return url
    video_id = entry.get("id") or url
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return None


def _safe_title(title):
    if not title:
        return "video"
    return sanitize_filename(title, restricted=True)


def _collect_sidecar_files(base_name):
    directory = Path(DOWNLOAD_DIR)
    subtitles = sorted(directory.glob(f"{base_name}.*.srt"))
    if not subtitles:
        subtitles = sorted(directory.glob(f"{base_name}.*.vtt"))

    thumbnail = None
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = directory / f"{base_name}.{ext}"
        if candidate.exists():
            thumbnail = candidate
            break
    return thumbnail, subtitles


def _rename_sidecars(temp_base_name, base_name):
    directory = Path(DOWNLOAD_DIR)
    patterns = [
        f"{temp_base_name}.info.json",
        f"{temp_base_name}.*.srt",
        f"{temp_base_name}.*.vtt",
        f"{temp_base_name}.jpg",
        f"{temp_base_name}.jpeg",
        f"{temp_base_name}.png",
        f"{temp_base_name}.webp",
    ]
    for pattern in patterns:
        for path in directory.glob(pattern):
            suffix = path.name[len(temp_base_name):]
            target = directory / f"{base_name}{suffix}"
            try:
                if target.exists():
                    target.unlink()
                path.rename(target)
            except OSError:
                logging.exception("Failed to rename sidecar %s", path)


def _rename_sidecars_by_id(video_id, base_name):
    if not video_id:
        return
    directory = Path(DOWNLOAD_DIR)
    marker = f"[{video_id}]_temp"
    allowed_ext = (".info.json", ".srt", ".vtt", ".jpg", ".jpeg", ".png", ".webp")
    for path in directory.iterdir():
        name = path.name
        if marker not in name:
            continue
        if not name.endswith(allowed_ext):
            continue
        suffix = name.split(marker, 1)[1]
        target = directory / f"{base_name}{suffix}"
        try:
            if target.exists():
                target.unlink()
            path.rename(target)
        except OSError:
            logging.exception("Failed to rename sidecar %s", path)


def _cleanup_temp_sidecars(video_id):
    if not video_id:
        return
    directory = Path(DOWNLOAD_DIR)
    marker = f"[{video_id}]_temp"
    for path in directory.iterdir():
        if marker not in path.name:
            continue
        if path.suffix == ".webm":
            continue
        _remove_path(str(path))


def _convert_thumbnail_to_jpg(thumbnail_path, base_name):
    if not thumbnail_path:
        return None
    if thumbnail_path.suffix.lower() == ".jpg":
        return thumbnail_path
    jpg_path = Path(DOWNLOAD_DIR) / f"{base_name}.jpg"
    result = subprocess.run(
        ["ffmpeg", "-i", str(thumbnail_path), "-frames:v", "1", str(jpg_path)],
        capture_output=True,
        text=True
    )
    if result.returncode == 0 and jpg_path.exists():
        _remove_path(str(thumbnail_path))
        return jpg_path
    return thumbnail_path


def _write_nfo(base_name, info):
    title = info.get("title") or ""
    description = info.get("description") or ""
    channel_name = info.get("channel") or info.get("uploader") or ""
    upload_date = info.get("upload_date") or ""
    premiered = ""
    if len(upload_date) == 8 and upload_date.isdigit():
        premiered = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    root = ET.Element("movie")
    ET.SubElement(root, "title").text = title
    if premiered:
        ET.SubElement(root, "premiered").text = premiered
    if channel_name:
        ET.SubElement(root, "studio").text = channel_name
    if description:
        ET.SubElement(root, "plot").text = description
    video_id = info.get("id") or ""
    if video_id:
        ET.SubElement(root, "uniqueid", type="youtube", default="true").text = video_id

    tree = ET.ElementTree(root)
    nfo_path = Path(DOWNLOAD_DIR) / f"{base_name}.nfo"
    try:
        tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
    except OSError:
        logging.exception("Failed to write NFO for %s", base_name)


def _subtitle_lang_from_filename(base_name, file_path):
    stem = Path(file_path).stem
    prefix = f"{base_name}."
    if not stem.startswith(prefix):
        return None
    suffix = stem[len(prefix):]
    if not suffix:
        return None
    return suffix.split("-")[0]


def _remove_path(path, attempts=5, delay=0.5):
    for _ in range(attempts):
        try:
            if path and os.path.exists(path):
                os.remove(path)
            return True
        except OSError:
            time.sleep(delay)
    return False


def _extract_video_id(url):
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        return parsed.path.lstrip("/") or None
    if "youtube" in host:
        query = urllib.parse.parse_qs(parsed.query)
        if "v" in query and query["v"]:
            return query["v"][0]
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "embed"):
            return parts[1]
    return None


def download_video(url, resolution, fps=None, codec="copy", cancel_event=None):
    """Download video at specified resolution and remux to MKV with stream copy."""
    try:
        if fps:
            return {
                "success": False,
                "error": "FPS changes are not supported with stream copy. Leave FPS empty."
            }
        
        # Format selection string
        format_string = _build_format_string(resolution)
        
        # Download with temporary name
        temp_filename = os.path.join(DOWNLOAD_DIR, "%(title)s [%(id)s]_temp.%(ext)s")

        def _progress_hook(progress):
            if cancel_event and cancel_event.is_set():
                raise yt_dlp.utils.DownloadCancelled()
        
        ydl_opts = {
            "format": format_string,
            "outtmpl": temp_filename,
            "quiet": False,
            "no_warnings": False,
            "progress_hooks": [_progress_hook],
            "windowsfilenames": True,
            "restrictfilenames": True,
            "nopart": True,
            "concurrent_fragment_downloads": 1,
            "retries": 5,
            "fragment_retries": 5,
            "sleep_interval": 1,
            "max_sleep_interval": 5,
            "sleep_interval_requests": 2,
            "writeinfojson": True,
            "writethumbnail": True,
            "convertthumbnails": "jpg",
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "srt",
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            temp_file_path = ydl.prepare_filename(info)
            title = info.get("title", "video")
            video_id = info.get("id")
            safe_title = _safe_title(title)
            base_name = f"{safe_title} [{video_id}]"
            channel_name = info.get("channel") or info.get("uploader") or ""
            description = info.get("description") or ""
            description = description.replace("\r", " ").replace("\n", " ").strip()
            
            # Remux to MKV with stream copy
            if FFMPEG_AVAILABLE:
                mkv_filename = os.path.join(DOWNLOAD_DIR, f"{base_name}.mkv")
                _rename_sidecars_by_id(video_id, base_name)
                thumbnail, subtitles = _collect_sidecar_files(base_name)
                thumbnail = _convert_thumbnail_to_jpg(thumbnail, base_name)
                
                # Build FFmpeg command
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-i", temp_file_path,
                ]

                input_index = 1
                thumbnail_index = None
                if thumbnail:
                    ffmpeg_cmd.extend(["-i", str(thumbnail)])
                    thumbnail_index = input_index
                    input_index += 1

                subtitle_indices = []
                for subtitle in subtitles:
                    ffmpeg_cmd.extend(["-i", str(subtitle)])
                    subtitle_indices.append(input_index)
                    input_index += 1

                ffmpeg_cmd.extend(["-map", "0:v:0", "-map", "0:a?"])
                if thumbnail_index is not None:
                    ffmpeg_cmd.extend(["-map", str(thumbnail_index)])
                for sub_index in subtitle_indices:
                    ffmpeg_cmd.extend(["-map", str(sub_index)])

                ffmpeg_cmd.extend([
                    "-c:v:0", "copy",
                    "-c:a", "copy",
                    "-c:s", "srt",
                    "-metadata", f"title={title}",
                    "-metadata", f"artist={channel_name}",
                    "-metadata", f"comment={description}",
                    "-metadata", f"description={description}",
                ])

                if thumbnail_index is not None:
                    ffmpeg_cmd.extend(["-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"])

                for index, subtitle in enumerate(subtitles):
                    lang = _subtitle_lang_from_filename(base_name, subtitle)
                    if lang:
                        ffmpeg_cmd.extend(["-metadata:s:s:{}".format(index), f"language={lang}"])

                ffmpeg_cmd.append(mkv_filename)
                
                # Run FFmpeg
                if os.path.exists(mkv_filename):
                    _remove_path(mkv_filename)

                result = subprocess.run(
                    ffmpeg_cmd,
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL
                )
                
                if result.returncode == 0:
                    # Remove temporary file (retry to avoid Windows file locks)
                    _remove_path(temp_file_path)

                    _remember_downloaded(video_id)
                    _rename_sidecars_by_id(video_id, base_name)
                    _cleanup_temp_sidecars(video_id)
                    _write_nfo(base_name, info)
                    
                    return {
                        'success': True,
                        'message': 'Download and remux completed',
                        'filename': os.path.basename(mkv_filename),
                        'title': title,
                        'format': 'mkv',
                        "codec": "copy",
                        "fps": "original",
                        "channel": channel_name
                    }
                else:
                    return {
                        'success': False,
                        'error': f'FFmpeg conversion failed: {result.stderr}'
                    }
            else:
                return {
                    "success": False,
                    "error": "FFmpeg is not installed. Please install FFmpeg to enable MKV remux."
                }
    
    except yt_dlp.utils.DownloadCancelled:
        return {
            "success": False,
            "error": "Download canceled"
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


class DownloadManager:
    def __init__(self):
        self.queue = deque()
        self.jobs = {}
        self.current_job_id = None
        self.lock = threading.Lock()
        self.paused = False
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def add_job(self, url, resolution=DEFAULT_MAX_RESOLUTION, fps=DEFAULT_FPS, codec=DEFAULT_CODEC, source="manual", video_id=None):
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "url": url,
            "video_id": video_id,
            "resolution": resolution,
            "fps": fps,
            "codec": codec,
            "source": source,
            "status": "queued",
            "error": None,
            "cancel_event": threading.Event(),
            "created_at": time.time(),
            "updated_at": time.time()
        }
        with self.lock:
            self.jobs[job_id] = job
            self.queue.append(job_id)
        return job_id

    def list_jobs(self):
        with self.lock:
            return [self._public_job(self.jobs[job_id]) for job_id in self.jobs]

    def get_queue(self):
        with self.lock:
            return [self._public_job(self.jobs[job_id]) for job_id in self.queue]

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def cancel_current(self):
        with self.lock:
            if not self.current_job_id:
                return False
            job = self.jobs.get(self.current_job_id)
            if job:
                job["cancel_event"].set()
                job["status"] = "canceling"
                job["updated_at"] = time.time()
                return True
        return False

    def _public_job(self, job):
        return {
            "id": job["id"],
            "url": job["url"],
            "video_id": job.get("video_id"),
            "resolution": job["resolution"],
            "fps": job["fps"],
            "codec": job["codec"],
            "source": job["source"],
            "status": job["status"],
            "error": job["error"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"]
        }

    def _worker_loop(self):
        while True:
            if self.paused:
                time.sleep(1)
                continue
            job_id = None
            with self.lock:
                if self.queue:
                    job_id = self.queue.popleft()
                    self.current_job_id = job_id
            if not job_id:
                time.sleep(1)
                continue
            job = self.jobs.get(job_id)
            if not job:
                continue
            job["status"] = "downloading"
            job["updated_at"] = time.time()
            result = download_video(
                job["url"],
                job["resolution"],
                job["fps"],
                job["codec"],
                cancel_event=job["cancel_event"]
            )
            if result["success"]:
                job["status"] = "completed"
                job["updated_at"] = time.time()
            else:
                job["status"] = "canceled" if result["error"] == "Download canceled" else "failed"
                job["error"] = result["error"]
                job["updated_at"] = time.time()
            _clear_inflight(job.get("video_id"))
            with self.lock:
                if self.current_job_id == job_id:
                    self.current_job_id = None


download_manager = DownloadManager()


def _read_sources(file_path):
    sources = []
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                sources.append(line)
    except OSError:
        logging.exception("Failed reading %s", file_path)
    return sources


def _read_source_text(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        logging.exception("Failed reading %s", file_path)
        return ""


def _write_source_text(file_path, content):
    try:
        with open(file_path, "w", encoding="utf-8") as handle:
            handle.write(content)
            return True
    except OSError:
        logging.exception("Failed writing %s", file_path)
        return False


def _should_download(video_id):
    if not video_id:
        return False
    with STATE_LOCK:
        if video_id in STATE["downloaded_ids"]:
            return False
    if _has_downloaded_file(video_id):
        _remember_downloaded(video_id)
        return False
    return True


def _check_playlist(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = info.get("entries", []) if info else []
        for entry in entries:
            video_id = entry.get("id")
            if not _should_download(video_id):
                continue
            if _is_inflight(video_id):
                continue
            video_url = _ensure_url(entry)
            if video_url:
                _mark_inflight(video_id)
                download_manager.add_job(video_url, source="playlist", video_id=video_id)


def _check_channel(url, limit=10):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = info.get("entries", []) if info else []
        for entry in entries[:limit]:
            video_id = entry.get("id")
            if not _should_download(video_id):
                continue
            if _is_inflight(video_id):
                continue
            video_url = _ensure_url(entry)
            if video_url:
                _mark_inflight(video_id)
                download_manager.add_job(video_url, source="channel", video_id=video_id)


def background_monitor():
    _ensure_source_files()
    while True:
        try:
            run_playlist_check()
            run_channel_check()
        except Exception:
            logging.exception("Monitor loop error")

        time.sleep(CHECK_INTERVAL_SECONDS)


def run_playlist_check():
    playlists = _read_sources(PLAYLISTS_FILE)
    for playlist_url in playlists:
        _check_playlist(playlist_url)


def run_channel_check():
    channels = _read_sources(CHANNELS_FILE)
    for channel_url in channels:
        _check_channel(channel_url)


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


@app.route('/')
def index():
    return render_template("index.html")


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["user"] = username
            session["token"] = SESSION_TOKEN
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials"), 401
    return render_template("login.html", error=None)


@app.route('/logout', methods=['POST'])
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route('/download', methods=['POST'])
def download():
    """POST endpoint to enqueue a download job."""
    data = request.get_json()

    url = data.get("url") if data else None
    resolution = data.get("resolution") if data else DEFAULT_MAX_RESOLUTION
    fps = data.get("fps") if data else DEFAULT_FPS
    codec = data.get("codec", DEFAULT_CODEC) if data else DEFAULT_CODEC

    if not url or not resolution:
        return jsonify({"error": "URL and resolution parameters are required"}), 400

    if not resolution.endswith("p"):
        return jsonify({"error": "Resolution must be in format like \"720p\""}), 400

    if fps:
        try:
            fps = int(fps)
            if fps <= 0:
                return jsonify({"error": "FPS must be a positive number"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "FPS must be a valid number"}), 400

    job_id = download_manager.add_job(
        url,
        resolution,
        fps,
        codec,
        source="manual",
        video_id=_extract_video_id(url)
    )
    return jsonify({"success": True, "job_id": job_id}), 202


@app.route('/api/jobs', methods=['GET'])
def api_jobs():
    return jsonify({
        "jobs": download_manager.list_jobs(),
        "queue": download_manager.get_queue(),
        "paused": download_manager.paused
    }), 200


@app.route('/api/pause', methods=['POST'])
def api_pause():
    download_manager.pause()
    return jsonify({"success": True, "paused": True}), 200


@app.route('/api/resume', methods=['POST'])
def api_resume():
    download_manager.resume()
    return jsonify({"success": True, "paused": False}), 200


@app.route('/api/stop-current', methods=['POST'])
def api_stop_current():
    canceled = download_manager.cancel_current()
    return jsonify({"success": canceled}), 200


@app.route('/api/check/playlists', methods=['POST'])
def api_check_playlists():
    try:
        run_playlist_check()
        return jsonify({"success": True}), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/check/channels', methods=['POST'])
def api_check_channels():
    try:
        run_channel_check()
        return jsonify({"success": True}), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/sources', methods=['GET'])
def api_get_sources():
    return jsonify({
        "playlists": _read_source_text(PLAYLISTS_FILE),
        "channels": _read_source_text(CHANNELS_FILE)
    }), 200


@app.route('/api/sources', methods=['POST'])
def api_update_sources():
    data = request.get_json() or {}
    playlists = data.get("playlists", "")
    channels = data.get("channels", "")

    ok_playlists = _write_source_text(PLAYLISTS_FILE, playlists)
    ok_channels = _write_source_text(CHANNELS_FILE, channels)

    if ok_playlists and ok_channels:
        return jsonify({"success": True}), 200
    return jsonify({"success": False}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'ffmpeg_available': FFMPEG_AVAILABLE
    }), 200


if __name__ == '__main__':
    _ensure_source_files()
    _bootstrap_state_from_downloads()
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        monitor_thread = threading.Thread(target=background_monitor, daemon=True)
        monitor_thread.start()
    app.run(debug=True, host='0.0.0.0', port=5000)
