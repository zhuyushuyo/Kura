"""Application start-up."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import time
import traceback

from . import __version__, i18n, paths
from .config import Settings

log = logging.getLogger("kura")


def _setup_logging() -> None:
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(paths.LOG_DIR / "kura.log", maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _set_environment(settings: Settings) -> None:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    if not settings.get("webengine_gpu"):
        flags.append("--disable-gpu")
    if paths.IS_LINUX and hasattr(os, "geteuid") and os.geteuid() == 0:
        flags.append("--no-sandbox")  # Chromium refuses to sandbox as root (dev containers only)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(dict.fromkeys(flags))
    try:  # make HTTPS work with python-build-standalone on macOS
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass


def _excepthook(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("Unhandled exception:\n%s", text)
    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance():
            QMessageBox.critical(None, i18n.tr("app.crash_title"), i18n.tr("app.crash_body", msg=str(exc)[:500]))
    except Exception:  # noqa: BLE001
        pass


def _maybe_update_engine(settings: Settings, splash) -> None:
    """Update yt-dlp (+EJS, Deno) before it is first imported."""
    if not settings.get("auto_update", True):
        return
    from PyQt6.QtCore import QEventLoop, QThread

    from .core import updater

    channel = settings.get("ytdlp_channel", "stable")
    result: dict = {}

    class _Check(QThread):
        def run(self_inner) -> None:  # noqa: N805
            result["check"] = updater.check(channel, timeout=4)
            cur, latest, avail = result["check"]
            if avail:
                result["updating"] = latest
                result["ok"] = updater.update(channel, lambda m: log.info("update: %s", m))

    th = _Check()
    loop = QEventLoop()
    th.finished.connect(loop.quit)
    th.start()
    shown = False
    deadline = time.monotonic() + 180
    while th.isRunning() and time.monotonic() < deadline:
        loop.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
        if not shown and "updating" in result:
            splash.set_text(i18n.tr("app.updating_engine", ver=result["updating"]))
            shown = True
        th.wait(50)
    settings.set("last_update_check", int(time.time()))
    if "ok" in result:
        log.info("engine update %s -> %s: %s", result["check"][0], result.get("updating"), result["ok"])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    paths.ensure_dirs()
    _setup_logging()
    log.info("Kura %s starting (Python %s, %s)", __version__, sys.version.split()[0], sys.platform)
    settings = Settings()
    _set_environment(settings)

    # QtWebEngine must be imported before the QApplication exists.
    from PyQt6 import QtWebEngineWidgets  # noqa: F401  (must precede QApplication)
    from PyQt6.QtCore import QLocale, Qt
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    app = QApplication(argv)
    app.setApplicationName("Kura")
    app.setApplicationDisplayName("Kura")
    app.setOrganizationName("Kura")
    app.setApplicationVersion(__version__)
    sys.excepthook = _excepthook

    system_lang = "ja" if QLocale.system().name().lower().startswith("ja") else "en"
    i18n.set_language(i18n.resolve(settings.get("language", "auto"), system_lang))
    if paths.IS_WINDOWS:
        f = QFont("Yu Gothic UI" if i18n.language() == "ja" else "Segoe UI", 9)
        app.setFont(f)

    from .ui.theme import THEME, app_icon

    app.setWindowIcon(app_icon())
    THEME.apply(settings.get("theme", "system"))
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: THEME.mode == "system" and THEME.apply())
    except AttributeError:
        pass

    class Splash(QWidget):
        def __init__(self) -> None:
            super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
            self.setObjectName("Card")
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(28, 24, 28, 22)
            logo = QLabel()
            logo.setPixmap(app_icon().pixmap(64, 64))
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(logo)
            name = QLabel(f"Kura {__version__}")
            name.setObjectName("Title")
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(name)
            self.msg = QLabel(i18n.tr("app.starting"))
            self.msg.setObjectName("Muted")
            self.msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(self.msg)
            self.setFixedWidth(360)

        def set_text(self, text: str) -> None:
            self.msg.setText(text)
            app.processEvents()

    splash = Splash()
    splash.show()
    app.processEvents()
    _maybe_update_engine(settings, splash)

    from .core.library import Database
    from .ui.context import AppContext
    from .ui.main_window import MainWindow

    db = Database()
    ctx = AppContext(settings, db)
    win = MainWindow(ctx)
    win.show()
    splash.close()
    win.first_run()
    rc = app.exec()
    win.shutdown()
    db.close()
    log.info("Kura exited (%s)", rc)
    return rc
