"""Periodic playlist/channel checker that queues new videos."""
import logging
import threading
import time

from . import storage
from .config import settings
from .downloader import DownloadError, entry_url, list_entries
from .manager import JobError, download_manager
from .storage import download_state

log = logging.getLogger(__name__)

KINDS = ("playlists", "channels")


class Monitor:
    def __init__(self):
        self._lock = threading.Lock()
        self._check_lock = threading.Lock()
        self._wakeup = threading.Event()
        self._thread = None
        self.running_kind = None
        self.last_check = {kind: None for kind in KINDS}
        self.next_check_at = None
        settings.on_change(self._on_settings_change)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        storage.ensure_source_files()
        self._thread = threading.Thread(target=self._loop, name="source-monitor", daemon=True)
        self._thread.start()

    def status(self):
        with self._lock:
            return {
                "enabled": settings.get("monitor_enabled"),
                "running": self.running_kind,
                "next_check_at": self.next_check_at if settings.get("monitor_enabled") else None,
                "last_check": {kind: dict(value) if value else None for kind, value in self.last_check.items()},
            }

    def trigger(self, kind):
        """Start a check in the background. Returns False if one is already running."""
        if kind not in KINDS:
            raise ValueError(f"Unknown source type: {kind}")
        if self._check_lock.locked():
            return False
        threading.Thread(target=self.run_check, args=(kind,), name=f"check-{kind}", daemon=True).start()
        return True

    def run_check(self, kind):
        if not self._check_lock.acquire(blocking=False):
            log.info("Skipping %s check; another check is running", kind)
            return None
        try:
            with self._lock:
                self.running_kind = kind
            return self._check(kind)
        finally:
            with self._lock:
                self.running_kind = None
            self._check_lock.release()

    def _check(self, kind):
        started = time.time()
        queued, errors = 0, []
        limit = settings.get("channel_video_limit") if kind == "channels" else None
        sources = storage.read_sources(kind)
        for url in sources:
            try:
                queued += self._check_source(url, kind[:-1], limit)
            except DownloadError as exc:
                log.warning("Could not check %s: %s", url, exc)
                errors.append({"url": url, "error": str(exc)})
            except Exception as exc:
                log.exception("Unexpected error checking %s", url)
                errors.append({"url": url, "error": f"Unexpected error: {exc}"})
        result = {
            "finished_at": time.time(),
            "duration": round(time.time() - started, 1),
            "sources": len(sources),
            "queued": queued,
            "errors": errors,
        }
        with self._lock:
            self.last_check[kind] = result
        log.info("%s check done: %d sources, %d queued, %d errors", kind, len(sources), queued, len(errors))
        return result

    @staticmethod
    def _check_source(url, source, limit):
        entries = list_entries(url)
        if limit:
            entries = entries[:limit]
        queued = 0
        for entry in entries:
            video_id = entry.get("id")
            video_url = entry_url(entry)
            if not video_id or not video_url or download_state.is_downloaded(video_id):
                continue
            try:
                download_manager.add_job(video_url, source=source, video_id=video_id, title=entry.get("title"))
                queued += 1
            except JobError:
                continue  # already queued
        return queued

    def _on_settings_change(self, changes):
        if "monitor_enabled" in changes or "check_interval_minutes" in changes:
            self._wakeup.set()

    def _loop(self):
        last_run_at = None
        while True:
            enabled = settings.get("monitor_enabled")
            interval = settings.get("check_interval_minutes") * 60
            due_at = (last_run_at + interval) if last_run_at else time.time()
            if enabled and time.time() >= due_at:
                last_run_at = time.time()
                for kind in KINDS:
                    try:
                        self.run_check(kind)
                    except Exception:
                        log.exception("Monitor loop error")
                continue
            with self._lock:
                self.next_check_at = due_at if enabled else None
            # Sleep until the next check is due, or until settings change.
            self._wakeup.wait(timeout=max(due_at - time.time(), 1) if enabled else None)
            self._wakeup.clear()


monitor = Monitor()
