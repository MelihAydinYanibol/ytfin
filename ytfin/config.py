"""Static configuration (environment) and runtime-editable settings."""
import json
import logging
import os
import secrets
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        log.warning("Invalid integer for %s, using %s", name, default)
        return default


def _resolve(path_value):
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


DOWNLOAD_DIR = _resolve(os.environ.get("YTFIN_DOWNLOAD_DIR", "downloads"))
PLAYLISTS_FILE = BASE_DIR / "playlists.txt"
CHANNELS_FILE = BASE_DIR / "channels.txt"
STATE_FILE = BASE_DIR / "state.json"
SETTINGS_FILE = _resolve(os.environ.get("YTFIN_SETTINGS_FILE", "settings.json"))

HOST = os.environ.get("YTFIN_HOST", "0.0.0.0")
PORT = _env_int("YTFIN_PORT", 4999)
DEBUG = _env_bool("YTFIN_DEBUG", False)

ADMIN_USERNAME = os.environ.get("YTFIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("YTFIN_PASSWORD", "admin")
SECRET_KEY = os.environ.get("YTFIN_SECRET") or secrets.token_hex(32)
# When set, this env var wins over the value stored in settings.json.
# Handy as a recovery switch if you lock yourself out.
AUTH_ENABLED_OVERRIDE = _env_bool("YTFIN_AUTH_ENABLED")

RESOLUTIONS = ("best", "2160p", "1440p", "1080p", "720p", "480p", "360p")

DEFAULT_SETTINGS = {
    "auth_enabled": True,
    "monitor_enabled": True,
    "check_interval_minutes": 15,
    "default_resolution": "1080p",
    "channel_video_limit": 10,
    "subtitle_languages": ["en"],
    "write_nfo": True,
    "embed_thumbnail": True,
    "max_job_history": 200,
}


class SettingsError(ValueError):
    pass


def _validate(key, value):
    """Return a normalized value for `key`, or raise SettingsError."""
    if key in ("auth_enabled", "monitor_enabled", "write_nfo", "embed_thumbnail"):
        if not isinstance(value, bool):
            raise SettingsError(f"'{key}' must be true or false")
        return value
    if key in ("check_interval_minutes", "channel_video_limit", "max_job_history"):
        if isinstance(value, bool):
            raise SettingsError(f"'{key}' must be a number")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise SettingsError(f"'{key}' must be a number") from None
        limits = {
            "check_interval_minutes": (1, 7 * 24 * 60),
            "channel_video_limit": (1, 500),
            "max_job_history": (10, 5000),
        }[key]
        if not limits[0] <= number <= limits[1]:
            raise SettingsError(f"'{key}' must be between {limits[0]} and {limits[1]}")
        return number
    if key == "default_resolution":
        if value not in RESOLUTIONS:
            raise SettingsError(f"'default_resolution' must be one of {', '.join(RESOLUTIONS)}")
        return value
    if key == "subtitle_languages":
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, list):
            raise SettingsError("'subtitle_languages' must be a list of language codes")
        langs = [str(item).strip() for item in value if str(item).strip()]
        for lang in langs:
            if len(lang) > 20 or not all(c.isalnum() or c in "-_.*" for c in lang):
                raise SettingsError(f"Invalid subtitle language code: {lang!r}")
        return langs
    raise SettingsError(f"Unknown setting '{key}'")


class Settings:
    """Thread-safe settings backed by a JSON file."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(DEFAULT_SETTINGS)
        self._listeners = []
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s, using defaults", self.path)
            return
        if not isinstance(raw, dict):
            log.warning("Ignoring malformed settings file %s", self.path)
            return
        for key, value in raw.items():
            try:
                self._data[key] = _validate(key, value)
            except SettingsError as exc:
                log.warning("Ignoring setting from file: %s", exc)

    def _save(self):
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, key):
        if key == "auth_enabled" and AUTH_ENABLED_OVERRIDE is not None:
            return AUTH_ENABLED_OVERRIDE
        with self._lock:
            return self._data[key]

    def as_dict(self):
        with self._lock:
            data = dict(self._data)
        if AUTH_ENABLED_OVERRIDE is not None:
            data["auth_enabled"] = AUTH_ENABLED_OVERRIDE
        return data

    def update(self, changes):
        """Validate and persist `changes`. All-or-nothing."""
        if not isinstance(changes, dict):
            raise SettingsError("Settings payload must be an object")
        if "auth_enabled" in changes and AUTH_ENABLED_OVERRIDE is not None:
            raise SettingsError("Authentication is controlled by YTFIN_AUTH_ENABLED and cannot be changed here")
        normalized = {key: _validate(key, value) for key, value in changes.items()}
        with self._lock:
            previous = dict(self._data)
            self._data.update(normalized)
            try:
                self._save()
            except OSError as exc:
                self._data = previous
                raise SettingsError(f"Could not save settings: {exc}") from exc
        for listener in list(self._listeners):
            try:
                listener(normalized)
            except Exception:
                log.exception("Settings listener failed")
        return self.as_dict()

    def on_change(self, callback):
        self._listeners.append(callback)


settings = Settings(SETTINGS_FILE)
