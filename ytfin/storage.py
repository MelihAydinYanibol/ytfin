"""Persistent bookkeeping: downloaded video ids and source lists."""
import json
import logging
import os
import threading
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

MEDIA_EXTENSIONS = (".mkv", ".mp4")


def _atomic_write(path, text):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def extract_video_id_from_name(file_name):
    if "[" not in file_name or "]" not in file_name:
        return None
    return file_name.rsplit("[", 1)[-1].split("]", 1)[0] or None


class DownloadState:
    """Tracks which video ids are downloaded or currently in the queue."""

    def __init__(self, state_file, download_dir):
        self.state_file = Path(state_file)
        self.download_dir = Path(download_dir)
        self._lock = threading.Lock()
        self._downloaded = set(self._load())
        self._inflight = set()

    def _load(self):
        if not self.state_file.exists():
            return []
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s; starting with empty state", self.state_file)
            return []
        if not isinstance(data, dict):
            return []
        return [str(v) for v in data.get("downloaded_ids", []) if v]

    def _save_locked(self):
        try:
            _atomic_write(self.state_file, json.dumps({"downloaded_ids": sorted(self._downloaded)}, indent=2))
        except OSError:
            log.exception("Failed to write state file %s", self.state_file)

    def bootstrap_from_downloads(self):
        """Pick up videos that exist on disk but are missing from state."""
        found = set()
        try:
            for path in self.download_dir.iterdir():
                if not path.is_file():
                    continue
                name = path.name
                if name.endswith(".info.json"):
                    stem = name[: -len(".info.json")]
                elif path.suffix.lower() in MEDIA_EXTENSIONS + (".nfo",):
                    stem = path.stem
                else:
                    continue
                if stem.endswith("_temp"):
                    continue
                video_id = extract_video_id_from_name(stem)
                if video_id:
                    found.add(video_id)
        except OSError:
            log.exception("Could not scan %s", self.download_dir)
            return
        with self._lock:
            new = found - self._downloaded
            if new:
                self._downloaded |= new
                self._save_locked()
                log.info("Recovered %d downloaded ids from disk", len(new))

    def has_file(self, video_id):
        marker = f"[{video_id}]"
        try:
            return any(
                path.is_file() and marker in path.name and path.suffix.lower() in MEDIA_EXTENSIONS
                for path in self.download_dir.iterdir()
            )
        except OSError:
            return False

    def remember(self, video_id):
        if not video_id:
            return
        with self._lock:
            if video_id not in self._downloaded:
                self._downloaded.add(video_id)
                self._save_locked()

    def is_downloaded(self, video_id):
        with self._lock:
            if video_id in self._downloaded:
                return True
        if self.has_file(video_id):
            self.remember(video_id)
            return True
        return False

    def try_claim(self, video_id):
        """Mark a video as in flight. Returns False if it already was."""
        if not video_id:
            return True
        with self._lock:
            if video_id in self._inflight:
                return False
            self._inflight.add(video_id)
            return True

    def release(self, video_id):
        if not video_id:
            return
        with self._lock:
            self._inflight.discard(video_id)

    def count(self):
        with self._lock:
            return len(self._downloaded)


SOURCE_FILES = {
    "playlists": config.PLAYLISTS_FILE,
    "channels": config.CHANNELS_FILE,
}


def ensure_source_files():
    for path in SOURCE_FILES.values():
        if not path.exists():
            try:
                path.write_text("", encoding="utf-8")
            except OSError:
                log.exception("Could not create %s", path)


def read_source_text(kind):
    try:
        return SOURCE_FILES[kind].read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def read_sources(kind):
    """Return the non-empty, non-comment lines of a source file."""
    try:
        text = read_source_text(kind)
    except OSError:
        log.exception("Failed reading %s sources", kind)
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def write_source_text(kind, content):
    if not content.endswith("\n") and content:
        content += "\n"
    _atomic_write(SOURCE_FILES[kind], content)


download_state = DownloadState(config.STATE_FILE, config.DOWNLOAD_DIR)
