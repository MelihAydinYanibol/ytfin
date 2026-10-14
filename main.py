"""YtFin entry point: `python main.py`."""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from ytfin import config  # noqa: E402  (logging must be configured first)
from ytfin.downloader import FFMPEG_AVAILABLE, ensure_download_dir  # noqa: E402
from ytfin.monitor import monitor  # noqa: E402
from ytfin.storage import download_state, ensure_source_files  # noqa: E402
from ytfin.web import create_app  # noqa: E402

log = logging.getLogger("ytfin")

app = create_app()


def startup():
    try:
        ensure_download_dir()
    except OSError:
        log.exception("Cannot create download directory %s", config.DOWNLOAD_DIR)
    ensure_source_files()
    download_state.bootstrap_from_downloads()
    if not FFMPEG_AVAILABLE:
        log.warning("FFmpeg not found on PATH: downloads will fail until it is installed")
    if config.settings.get("auth_enabled"):
        if config.ADMIN_USERNAME == "admin" and config.ADMIN_PASSWORD == "admin":
            log.warning("Using default credentials admin/admin. Set YTFIN_USERNAME and YTFIN_PASSWORD.")
    else:
        log.warning("Authentication is DISABLED: anyone who can reach this server has full access")
    monitor.start()


if __name__ == "__main__":
    startup()
    log.info("YtFin listening on http://%s:%s (downloads -> %s)", config.HOST, config.PORT, config.DOWNLOAD_DIR)
    # The reloader would start the background threads twice, so keep it off.
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG, use_reloader=False, threaded=True)
