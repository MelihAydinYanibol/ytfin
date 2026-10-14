"""yt-dlp download + FFmpeg remux into Jellyfin-friendly MKV files."""
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import yt_dlp
from yt_dlp.utils import sanitize_filename

from . import config
from .config import settings
from .storage import download_state

log = logging.getLogger(__name__)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
FFMPEG_TIMEOUT_SECONDS = 60 * 60
RESOLUTION_RE = re.compile(r"^(\d{3,4})p$")


class DownloadError(Exception):
    """A download failed with a message suitable for showing to the user."""


class DownloadCanceled(DownloadError):
    pass


def ensure_download_dir():
    Path(config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)


def is_valid_resolution(resolution):
    return resolution == "best" or bool(RESOLUTION_RE.match(resolution or ""))


def build_format_string(resolution):
    if resolution == "best":
        return "bestvideo+bestaudio/best"
    height = RESOLUTION_RE.match(resolution).group(1)
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"


def is_http_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def extract_video_id(url):
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        return parsed.path.strip("/").split("/")[0] or None
    if "youtube" in host:
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("v"):
            return query["v"][0]
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live"):
            return parts[1]
    return None


def entry_url(entry):
    """Build a watch URL from a flat playlist entry."""
    if not entry:
        return None
    url = entry.get("url")
    if url and url.startswith("http"):
        return url
    video_id = entry.get("id") or url
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


def friendly_error(exc):
    """Turn a yt-dlp/OS exception into a short, readable message."""
    message = str(exc).strip()
    message = re.sub(r"^ERROR:\s*", "", message)
    message = re.sub(r"\x1b\[[0-9;]*m", "", message)  # strip ANSI colors
    # Drop yt-dlp's "please report this issue" boilerplate.
    message = re.split(r";\s*please report this issue", message, maxsplit=1, flags=re.I)[0]
    return message.splitlines()[0][:500] if message else exc.__class__.__name__


def _remove_path(path, attempts=5, delay=0.5):
    """Delete a file, retrying to ride out Windows file locks."""
    for _ in range(attempts):
        try:
            if path and os.path.exists(path):
                os.remove(path)
            return True
        except OSError:
            time.sleep(delay)
    log.warning("Could not remove %s", path)
    return False


def probe(url):
    """Return title, resolutions and frame rates for a single video."""
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise DownloadError(friendly_error(exc)) from exc
    if not info:
        raise DownloadError("No information returned for this URL")

    heights, fps_values = set(), set()
    for fmt in info.get("formats") or []:
        if fmt.get("vcodec") in (None, "none"):
            continue
        if fmt.get("height"):
            heights.add(int(fmt["height"]))
        if fmt.get("fps"):
            fps_values.add(int(fmt["fps"]))
    return {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "resolutions": [f"{h}p" for h in sorted(heights, reverse=True)],
        "available_fps": sorted(fps_values, reverse=True),
        "original_fps": info.get("fps"),
    }


def list_entries(url):
    """Flat-extract a playlist/channel and return its entries."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise DownloadError(friendly_error(exc)) from exc
    entries = list((info or {}).get("entries") or [])
    # Channel URLs can return nested tabs (Videos, Shorts, ...); flatten one level.
    flat = []
    for entry in entries:
        if entry and entry.get("_type") == "playlist" and entry.get("entries"):
            flat.extend(e for e in entry["entries"] if e)
        elif entry:
            flat.append(entry)
    return flat


# --- sidecar helpers -------------------------------------------------------

def _temp_marker(video_id):
    return f"[{video_id}]_temp"


def _rename_temp_sidecars(video_id, base_name):
    directory = Path(config.DOWNLOAD_DIR)
    marker = _temp_marker(video_id)
    allowed = (".info.json", ".srt", ".vtt", ".jpg", ".jpeg", ".png", ".webp")
    for path in directory.iterdir():
        name = path.name
        if marker not in name or not name.endswith(allowed):
            continue
        target = directory / f"{base_name}{name.split(marker, 1)[1]}"
        try:
            if target.exists():
                target.unlink()
            path.rename(target)
        except OSError:
            log.exception("Failed to rename sidecar %s", path)


def cleanup_temp_files(video_id):
    """Remove every leftover `_temp` file for a video (partial downloads included)."""
    if not video_id:
        return
    marker = _temp_marker(video_id)
    try:
        paths = [p for p in Path(config.DOWNLOAD_DIR).iterdir() if marker in p.name]
    except OSError:
        return
    for path in paths:
        _remove_path(str(path))


def _collect_sidecars(base_name):
    directory = Path(config.DOWNLOAD_DIR)
    pattern_base = glob_escape(base_name)
    subtitles = sorted(directory.glob(f"{pattern_base}.*.srt")) or sorted(directory.glob(f"{pattern_base}.*.vtt"))
    thumbnail = None
    for ext in ("jpg", "jpeg", "png", "webp"):
        candidate = directory / f"{base_name}.{ext}"
        if candidate.exists():
            thumbnail = candidate
            break
    return thumbnail, subtitles


def glob_escape(text):
    # Titles contain "[id]", which glob would treat as a character class.
    return re.sub(r"([\[\]*?])", r"[\1]", text)


def _convert_thumbnail_to_jpg(thumbnail, base_name):
    if not thumbnail or thumbnail.suffix.lower() == ".jpg":
        return thumbnail
    jpg_path = Path(config.DOWNLOAD_DIR) / f"{base_name}.jpg"
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", str(thumbnail), "-frames:v", "1", str(jpg_path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.exception("Thumbnail conversion failed for %s", thumbnail)
        return thumbnail
    if result.returncode == 0 and jpg_path.exists():
        _remove_path(str(thumbnail))
        return jpg_path
    return thumbnail


def _subtitle_lang(base_name, file_path):
    stem = Path(file_path).stem
    prefix = f"{base_name}."
    if not stem.startswith(prefix):
        return None
    return stem[len(prefix):].split("-")[0] or None


def write_nfo(base_name, info):
    upload_date = info.get("upload_date") or ""
    root = ET.Element("movie")
    ET.SubElement(root, "title").text = info.get("title") or ""
    if len(upload_date) == 8 and upload_date.isdigit():
        ET.SubElement(root, "premiered").text = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    channel = info.get("channel") or info.get("uploader")
    if channel:
        ET.SubElement(root, "studio").text = channel
    if info.get("description"):
        ET.SubElement(root, "plot").text = info["description"]
    if info.get("id"):
        ET.SubElement(root, "uniqueid", type="youtube", default="true").text = info["id"]
    try:
        ET.ElementTree(root).write(Path(config.DOWNLOAD_DIR) / f"{base_name}.nfo", encoding="utf-8", xml_declaration=True)
    except OSError:
        log.exception("Failed to write NFO for %s", base_name)


def _run_ffmpeg(cmd, cancel_event):
    """Run FFmpeg, honoring cancellation and a hard timeout."""
    with tempfile.TemporaryFile(mode="w+") as stderr:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr, text=True)
        except OSError as exc:
            raise DownloadError(f"Could not start FFmpeg: {exc}") from exc
        deadline = time.monotonic() + FFMPEG_TIMEOUT_SECONDS
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise DownloadCanceled("Download canceled")
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                raise DownloadError("FFmpeg timed out")
            time.sleep(0.2)
        if proc.returncode != 0:
            stderr.seek(0)
            tail = "\n".join(stderr.read().strip().splitlines()[-8:])
            raise DownloadError(f"FFmpeg remux failed: {tail or 'exit code ' + str(proc.returncode)}")


def _remux(temp_file, base_name, info, cancel_event):
    mkv_path = os.path.join(config.DOWNLOAD_DIR, f"{base_name}.mkv")
    _rename_temp_sidecars(info.get("id"), base_name)
    thumbnail, subtitles = _collect_sidecars(base_name)
    if not settings.get("embed_thumbnail"):
        thumbnail = None
    thumbnail = _convert_thumbnail_to_jpg(thumbnail, base_name)

    title = info.get("title") or ""
    channel = info.get("channel") or info.get("uploader") or ""
    description = " ".join((info.get("description") or "").split())

    cmd = ["ffmpeg", "-nostdin", "-y", "-i", temp_file]
    next_input = 1
    thumb_index = None
    if thumbnail:
        cmd += ["-i", str(thumbnail)]
        thumb_index, next_input = next_input, next_input + 1
    sub_indices = []
    for subtitle in subtitles:
        cmd += ["-i", str(subtitle)]
        sub_indices.append(next_input)
        next_input += 1

    cmd += ["-map", "0:v:0", "-map", "0:a?"]
    if thumb_index is not None:
        cmd += ["-map", str(thumb_index)]
    for index in sub_indices:
        cmd += ["-map", str(index)]
    cmd += [
        "-c:v:0", "copy", "-c:a", "copy", "-c:s", "srt",
        "-metadata", f"title={title}",
        "-metadata", f"artist={channel}",
        "-metadata", f"comment={description}",
        "-metadata", f"description={description}",
    ]
    if thumb_index is not None:
        cmd += ["-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
    for index, subtitle in enumerate(subtitles):
        lang = _subtitle_lang(base_name, subtitle)
        if lang:
            cmd += [f"-metadata:s:s:{index}", f"language={lang}"]
    cmd.append(mkv_path)

    if os.path.exists(mkv_path):
        _remove_path(mkv_path)
    try:
        _run_ffmpeg(cmd, cancel_event)
    except DownloadError:
        _remove_path(mkv_path)
        raise
    return mkv_path


def download_video(url, resolution, cancel_event=None, on_progress=None):
    """Download `url` and remux it into an MKV next to its sidecar files.

    Raises DownloadError (or DownloadCanceled) on failure.
    """
    if not FFMPEG_AVAILABLE:
        raise DownloadError("FFmpeg is not installed. Install FFmpeg to enable MKV remuxing.")
    if not is_valid_resolution(resolution):
        raise DownloadError(f"Invalid resolution: {resolution}")
    try:
        ensure_download_dir()
    except OSError as exc:
        raise DownloadError(f"Download folder is not writable: {exc}") from exc

    def progress_hook(progress):
        if cancel_event is not None and cancel_event.is_set():
            raise yt_dlp.utils.DownloadCancelled()
        if on_progress is None:
            return
        info = progress.get("info_dict") or {}
        total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
        done = progress.get("downloaded_bytes")
        on_progress({
            "title": info.get("title"),
            "video_id": info.get("id"),
            "percent": round(done * 100 / total, 1) if total and done else None,
            "speed": progress.get("speed"),
            "eta": progress.get("eta"),
            "phase": "downloading" if progress.get("status") == "downloading" else "processing",
        })

    langs = settings.get("subtitle_languages")
    opts = {
        "format": build_format_string(resolution),
        "outtmpl": os.path.join(str(config.DOWNLOAD_DIR), "%(title)s [%(id)s]_temp.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "progress_hooks": [progress_hook],
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
        "writesubtitles": bool(langs),
        "writeautomaticsub": bool(langs),
        "subtitleslangs": langs,
        "subtitlesformat": "srt/best",
        "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}] if langs else [],
    }

    video_id = extract_video_id(url)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise DownloadError("yt-dlp returned no video information")
            video_id = info.get("id") or video_id
            temp_file = ydl.prepare_filename(info)

        if on_progress:
            on_progress({"phase": "processing", "percent": None, "speed": None, "eta": None})

        base_name = f"{sanitize_filename(info.get('title') or 'video', restricted=True)} [{video_id}]"
        mkv_path = _remux(temp_file, base_name, info, cancel_event)

        _remove_path(temp_file)
        download_state.remember(video_id)
        _rename_temp_sidecars(video_id, base_name)
        if settings.get("write_nfo"):
            write_nfo(base_name, info)
        return {
            "filename": os.path.basename(mkv_path),
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "video_id": video_id,
        }
    except yt_dlp.utils.DownloadCancelled as exc:
        raise DownloadCanceled("Download canceled") from exc
    except DownloadError:
        raise
    except Exception as exc:
        log.exception("Download failed for %s", url)
        raise DownloadError(friendly_error(exc)) from exc
    finally:
        cleanup_temp_files(video_id)
