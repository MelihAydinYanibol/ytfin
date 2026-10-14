"""Background download queue."""
import logging
import threading
import time
import uuid
from collections import deque

from .config import settings
from .downloader import DownloadCanceled, DownloadError, download_video
from .storage import download_state

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "downloading", "processing", "canceling")
FINISHED_STATUSES = ("completed", "failed", "canceled")
PUBLIC_FIELDS = (
    "id", "url", "video_id", "title", "resolution", "source", "status", "error",
    "progress", "speed", "eta", "filename", "created_at", "updated_at",
)


class JobError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class DownloadManager:
    def __init__(self):
        self.queue = deque()
        self.jobs = {}
        self.current_job_id = None
        self.lock = threading.Lock()
        self._paused = threading.Event()
        self._wakeup = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, name="download-worker", daemon=True)
        self._worker.start()

    # --- public API --------------------------------------------------------

    @property
    def paused(self):
        return self._paused.is_set()

    def add_job(self, url, resolution=None, source="manual", video_id=None, title=None):
        if video_id and not download_state.try_claim(video_id):
            raise JobError("This video is already queued or downloading", status=409)
        now = time.time()
        job = {
            "id": uuid.uuid4().hex,
            "url": url,
            "video_id": video_id,
            "title": title,
            "resolution": resolution or settings.get("default_resolution"),
            "source": source,
            "status": "queued",
            "error": None,
            "progress": None,
            "speed": None,
            "eta": None,
            "filename": None,
            "cancel_event": threading.Event(),
            "created_at": now,
            "updated_at": now,
        }
        with self.lock:
            self.jobs[job["id"]] = job
            self.queue.append(job["id"])
            self._prune_locked()
        self._wakeup.set()
        return job["id"]

    def list_jobs(self):
        with self.lock:
            return [self._public(job) for job in self.jobs.values()]

    def counts(self):
        counts = {status: 0 for status in ACTIVE_STATUSES + FINISHED_STATUSES}
        with self.lock:
            for job in self.jobs.values():
                counts[job["status"]] = counts.get(job["status"], 0) + 1
        return counts

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()
        self._wakeup.set()

    def cancel(self, job_id):
        """Cancel a queued or running job."""
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise JobError("Job not found", status=404)
            if job["status"] == "queued":
                try:
                    self.queue.remove(job_id)
                except ValueError:
                    pass
                self._set_locked(job, status="canceled", error="Removed from queue")
                download_state.release(job["video_id"])
                return
            if job["status"] in ("downloading", "processing"):
                job["cancel_event"].set()
                self._set_locked(job, status="canceling")
                return
        raise JobError(f"Job is {job['status']} and cannot be canceled", status=409)

    def cancel_current(self):
        with self.lock:
            job_id = self.current_job_id
        if not job_id:
            return False
        try:
            self.cancel(job_id)
            return True
        except JobError:
            return False

    def retry(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise JobError("Job not found", status=404)
            if job["status"] not in ("failed", "canceled"):
                raise JobError("Only failed or canceled jobs can be retried", status=409)
            self.jobs.pop(job_id)
        try:
            return self.add_job(job["url"], job["resolution"], job["source"], job["video_id"], job["title"])
        except JobError:
            with self.lock:
                self.jobs[job_id] = job
            raise

    def remove(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise JobError("Job not found", status=404)
            if job["status"] not in FINISHED_STATUSES:
                raise JobError("Cancel the job before removing it", status=409)
            del self.jobs[job_id]

    def clear_finished(self):
        with self.lock:
            finished = [jid for jid, job in self.jobs.items() if job["status"] in FINISHED_STATUSES]
            for job_id in finished:
                del self.jobs[job_id]
        return len(finished)

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _public(job):
        return {key: job.get(key) for key in PUBLIC_FIELDS}

    @staticmethod
    def _set_locked(job, **fields):
        job.update(fields)
        job["updated_at"] = time.time()

    def _set(self, job, **fields):
        with self.lock:
            self._set_locked(job, **fields)

    def _prune_locked(self):
        limit = settings.get("max_job_history")
        finished = [job for job in self.jobs.values() if job["status"] in FINISHED_STATUSES]
        excess = len(self.jobs) - limit
        if excess <= 0:
            return
        finished.sort(key=lambda job: job["updated_at"])
        for job in finished[:excess]:
            del self.jobs[job["id"]]

    def _next_job(self):
        with self.lock:
            if self._paused.is_set() or not self.queue:
                return None
            job_id = self.queue.popleft()
            self.current_job_id = job_id
            return self.jobs.get(job_id)

    def _worker_loop(self):
        while True:
            job = self._next_job()
            if job is None:
                self._wakeup.wait(timeout=1)
                self._wakeup.clear()
                continue
            try:
                self._run(job)
            except Exception:  # never let the worker thread die
                log.exception("Unexpected error while processing job %s", job["id"])
                self._set(job, status="failed", error="Internal error, see server log")
            finally:
                download_state.release(job["video_id"])
                with self.lock:
                    if self.current_job_id == job["id"]:
                        self.current_job_id = None
                    self._prune_locked()

    def _run(self, job):
        self._set(job, status="downloading", error=None, progress=0)
        log.info("Starting download %s (%s)", job["url"], job["resolution"])

        def on_progress(update):
            fields = {
                "progress": update.get("percent"),
                "speed": update.get("speed"),
                "eta": update.get("eta"),
            }
            if update.get("title") and not job.get("title"):
                fields["title"] = update["title"]
            if update.get("video_id") and not job.get("video_id"):
                fields["video_id"] = update["video_id"]
            if job["status"] != "canceling":
                fields["status"] = update.get("phase") or "downloading"
            self._set(job, **fields)

        try:
            result = download_video(job["url"], job["resolution"], job["cancel_event"], on_progress)
        except DownloadCanceled:
            self._set(job, status="canceled", error="Canceled by user", progress=None, speed=None, eta=None)
            log.info("Canceled %s", job["url"])
        except DownloadError as exc:
            self._set(job, status="failed", error=str(exc), progress=None, speed=None, eta=None)
            log.warning("Failed %s: %s", job["url"], exc)
        else:
            self._set(
                job, status="completed", progress=100, speed=None, eta=None,
                title=result.get("title") or job.get("title"),
                video_id=result.get("video_id") or job.get("video_id"),
                filename=result.get("filename"),
            )
            log.info("Completed %s -> %s", job["url"], result.get("filename"))


download_manager = DownloadManager()
