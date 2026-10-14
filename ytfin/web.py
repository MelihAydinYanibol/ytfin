"""Flask app: UI, JSON API and authentication."""
import hmac
import secrets
import logging
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from . import config, storage
from .config import SettingsError, settings
from .downloader import FFMPEG_AVAILABLE, DownloadError, extract_video_id, is_http_url, is_valid_resolution, probe
from .manager import JobError, download_manager
from .monitor import KINDS, monitor
from .storage import download_state

log = logging.getLogger(__name__)

PUBLIC_PATHS = {"/login", "/logout", "/health"}
API_PREFIXES = ("/api/", "/download", "/resolutions", "/fps")
MAX_SOURCE_BYTES = 256 * 1024


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _is_api_request():
    return request.path.startswith(API_PREFIXES)


def _auth_enabled():
    return settings.get("auth_enabled")


def _json_body():
    data = request.get_json(silent=True)
    if data is None:
        if request.content_length:
            raise ApiError("Request body must be valid JSON")
        return {}
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    return data


def create_app():
    app = Flask(
        __name__,
        template_folder=str(config.BASE_DIR / "templates"),
        static_folder=str(config.BASE_DIR / "static"),
    )
    app.secret_key = config.SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    # Rotates on every restart so old sessions are invalidated.
    session_token = secrets.token_hex(16)

    def is_authenticated():
        return (
            session.get("user") == config.ADMIN_USERNAME
            and hmac.compare_digest(str(session.get("token", "")), session_token)
        )

    @app.context_processor
    def inject_globals():
        return {"auth_enabled": _auth_enabled(), "username": session.get("user")}

    # --- request guards ----------------------------------------------------

    @app.before_request
    def guard():
        if request.path.startswith("/static/") or request.path in PUBLIC_PATHS:
            return None
        # Simple CSRF protection: browsers can't send JSON or custom headers
        # cross-site without a CORS preflight, which we never approve.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and _is_api_request():
            if not (request.is_json or request.headers.get("X-Requested-With")):
                return jsonify({"success": False, "error": "Send JSON (Content-Type: application/json)"}), 415
        if not _auth_enabled() or is_authenticated():
            return None
        if _is_api_request():
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))

    # --- error handling ----------------------------------------------------

    @app.errorhandler(ApiError)
    def handle_api_error(exc):
        return jsonify({"success": False, "error": str(exc)}), exc.status

    @app.errorhandler(JobError)
    def handle_job_error(exc):
        return jsonify({"success": False, "error": str(exc)}), exc.status

    @app.errorhandler(SettingsError)
    def handle_settings_error(exc):
        return jsonify({"success": False, "error": str(exc)}), 400

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        if _is_api_request():
            return jsonify({"success": False, "error": exc.description}), exc.code
        return render_template("error.html", code=exc.code, name=exc.name, description=exc.description), exc.code

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        log.exception("Unhandled error on %s %s", request.method, request.path)
        if _is_api_request():
            return jsonify({"success": False, "error": "Internal server error"}), 500
        return render_template(
            "error.html", code=500, name="Something went wrong",
            description="An unexpected error occurred. Check the server log for details.",
        ), 500

    # --- pages -------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("index.html", resolutions=config.RESOLUTIONS)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        target = request.args.get("next") or ""
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("index")
        if not _auth_enabled() or is_authenticated():
            return redirect(target)
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user_ok = hmac.compare_digest(username.encode(), config.ADMIN_USERNAME.encode())
            pass_ok = hmac.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode())
            if user_ok and pass_ok:
                session.clear()
                session["user"] = username
                session["token"] = session_token
                return redirect(target)
            log.warning("Failed login for %r from %s", username, request.remote_addr)
            time.sleep(0.5)  # slow down brute forcing a little
            return render_template("login.html", error="Invalid username or password", username=username), 401
        return render_template("login.html", error=None, username="")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "ffmpeg_available": FFMPEG_AVAILABLE}), 200

    # --- jobs --------------------------------------------------------------

    def _enqueue(data):
        url = str(data.get("url") or "").strip()
        resolution = str(data.get("resolution") or settings.get("default_resolution")).strip()
        if not url:
            raise ApiError("A video URL is required")
        if not is_http_url(url):
            raise ApiError("URL must start with http:// or https://")
        if not is_valid_resolution(resolution):
            raise ApiError('Resolution must look like "720p" or be "best"')
        if data.get("fps"):
            raise ApiError("FPS changes are not supported with stream copy. Leave FPS empty.")
        codec = data.get("codec") or "copy"
        if codec != "copy":
            raise ApiError('Only the "copy" codec (stream copy) is supported')
        job_id = download_manager.add_job(url, resolution, source="manual", video_id=extract_video_id(url))
        return jsonify({"success": True, "job_id": job_id}), 202

    @app.route("/download", methods=["POST"])
    def legacy_download():
        return _enqueue(_json_body())

    @app.route("/api/jobs", methods=["GET"])
    def api_jobs():
        return jsonify({
            "jobs": download_manager.list_jobs(),
            "paused": download_manager.paused,
        })

    @app.route("/api/jobs", methods=["POST"])
    def api_add_job():
        return _enqueue(_json_body())

    @app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    def api_cancel_job(job_id):
        download_manager.cancel(job_id)
        return jsonify({"success": True})

    @app.route("/api/jobs/<job_id>/retry", methods=["POST"])
    def api_retry_job(job_id):
        return jsonify({"success": True, "job_id": download_manager.retry(job_id)})

    @app.route("/api/jobs/<job_id>", methods=["DELETE"])
    def api_remove_job(job_id):
        download_manager.remove(job_id)
        return jsonify({"success": True})

    @app.route("/api/jobs/clear", methods=["POST"])
    def api_clear_jobs():
        return jsonify({"success": True, "removed": download_manager.clear_finished()})

    @app.route("/api/pause", methods=["POST"])
    def api_pause():
        download_manager.pause()
        return jsonify({"success": True, "paused": True})

    @app.route("/api/resume", methods=["POST"])
    def api_resume():
        download_manager.resume()
        return jsonify({"success": True, "paused": False})

    @app.route("/api/stop-current", methods=["POST"])
    def api_stop_current():
        if not download_manager.cancel_current():
            raise ApiError("Nothing is downloading right now", status=409)
        return jsonify({"success": True})

    # --- status, probing ---------------------------------------------------

    @app.route("/api/status")
    def api_status():
        return jsonify({
            "paused": download_manager.paused,
            "counts": download_manager.counts(),
            "downloaded_total": download_state.count(),
            "ffmpeg_available": FFMPEG_AVAILABLE,
            "monitor": monitor.status(),
            "auth_enabled": _auth_enabled(),
            "download_dir": str(config.DOWNLOAD_DIR),
        })

    def _probe_from_query():
        url = (request.args.get("url") or "").strip()
        if not url:
            raise ApiError("The 'url' query parameter is required")
        if not is_http_url(url):
            raise ApiError("URL must start with http:// or https://")
        try:
            return probe(url)
        except DownloadError as exc:
            raise ApiError(str(exc), status=422) from exc

    @app.route("/api/probe")
    def api_probe():
        return jsonify({"success": True, **_probe_from_query()})

    @app.route("/resolutions")
    def legacy_resolutions():
        info = _probe_from_query()
        return jsonify({"success": True, "title": info["title"], "resolutions": info["resolutions"]})

    @app.route("/fps")
    def legacy_fps():
        info = _probe_from_query()
        return jsonify({
            "success": True, "title": info["title"],
            "available_fps": info["available_fps"], "original_fps": info["original_fps"],
        })

    # --- sources -----------------------------------------------------------

    @app.route("/api/check/<kind>", methods=["POST"])
    def api_check(kind):
        if kind not in KINDS:
            raise ApiError(f"Unknown source type '{kind}'", status=404)
        if not monitor.trigger(kind):
            raise ApiError("A check is already running, try again shortly", status=409)
        return jsonify({"success": True, "started": True}), 202

    @app.route("/api/sources", methods=["GET"])
    def api_get_sources():
        try:
            return jsonify({kind: storage.read_source_text(kind) for kind in KINDS})
        except OSError as exc:
            raise ApiError(f"Could not read sources: {exc}", status=500) from exc

    @app.route("/api/sources", methods=["POST", "PUT"])
    def api_update_sources():
        data = _json_body()
        updates = {}
        for kind in KINDS:
            if kind not in data:
                continue
            text = data[kind]
            if not isinstance(text, str):
                raise ApiError(f"'{kind}' must be a string")
            if len(text.encode()) > MAX_SOURCE_BYTES:
                raise ApiError(f"'{kind}' is too large")
            invalid = [
                line.strip() for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#") and not is_http_url(line.strip())
            ]
            if invalid:
                raise ApiError(f"Invalid URL in {kind}: {invalid[0]}")
            updates[kind] = text
        if not updates:
            raise ApiError("Nothing to save")
        for kind, text in updates.items():
            try:
                storage.write_source_text(kind, text)
            except OSError as exc:
                raise ApiError(f"Could not save {kind}: {exc}", status=500) from exc
        return jsonify({"success": True, **{k: len(storage.read_sources(k)) for k in updates}})

    # --- settings ----------------------------------------------------------

    def _settings_payload():
        return {
            "settings": settings.as_dict(),
            "auth_locked": config.AUTH_ENABLED_OVERRIDE is not None,
            "default_credentials": config.ADMIN_USERNAME == "admin" and config.ADMIN_PASSWORD == "admin",
            "resolutions": list(config.RESOLUTIONS),
        }

    @app.route("/api/settings", methods=["GET"])
    def api_get_settings():
        return jsonify({"success": True, **_settings_payload()})

    @app.route("/api/settings", methods=["POST", "PUT", "PATCH"])
    def api_update_settings():
        data = _json_body()
        enabling_auth = data.get("auth_enabled") is True and not _auth_enabled()
        settings.update(data)
        if enabling_auth:
            # Keep the person who just turned auth on logged in.
            session["user"] = config.ADMIN_USERNAME
            session["token"] = session_token
        return jsonify({"success": True, **_settings_payload()})

    return app
