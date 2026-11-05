"""Main window: navigation rail, pages, status bar, toasts, clipboard, tray."""

from __future__ import annotations

import time

from PyQt6.QtCore import QByteArray, QSize, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, i18n, paths
from ..core import urls
from .browser_page import BrowserPage
from .context import AppContext
from .dialogs import AddUrlsDialog, FfmpegSetupDialog, FirstRunDialog, PlaylistDialog, confirm
from .library_page import LibraryPage
from .queue_manager import QueueManager
from .queue_page import QueuePage
from .settings_page import SettingsPage
from .theme import THEME, app_icon, human_size
from .web import create_profile
from .widgets import NavButton, ToastHost

tr = i18n.tr

_QUICK_HOSTS = ("vimeo.com", "nicovideo.jp", "nico.ms", "twitch.tv", "x.com", "twitter.com", "tiktok.com",
                "bilibili.com", "dailymotion.com", "ted.com", "soundcloud.com", "instagram.com", "facebook.com")


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        ctx.main_window = self
        self.setWindowTitle(f"Kura {__version__}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1080, 700)
        self.setAcceptDrops(True)
        self.queue = QueueManager(ctx)
        # No parent: the profile must outlive the web page (see shutdown()).
        self.profile = create_profile(ctx, None)

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- navigation rail ------------------------------------------------------------------
        self.rail = QFrame()
        self.rail.setObjectName("NavRail")
        self.rail.setFixedWidth(80)
        rl = QVBoxLayout(self.rail)
        rl.setContentsMargins(7, 12, 7, 12)
        rl.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(36, 36)))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setToolTip(f"Kura {__version__}")
        rl.addWidget(logo)
        rl.addSpacing(10)
        self.nav_browse = NavButton("ph.compass", tr("nav.browse"))
        self.nav_queue = NavButton("ph.download-simple", tr("nav.queue"))
        self.nav_library = NavButton("ph.books", tr("nav.library"))
        self.nav_settings = NavButton("ph.gear-six", tr("nav.settings"))
        for b in (self.nav_browse, self.nav_queue, self.nav_library):
            rl.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        rl.addStretch(1)
        rl.addWidget(self.nav_settings, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(self.rail)

        # -- pages ------------------------------------------------------------------------------
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.browse = BrowserPage(ctx, self.queue, self.profile)
        self.queue_page = QueuePage(ctx, self.queue)
        self.library = LibraryPage(ctx)
        self.settings_page = SettingsPage(ctx, self)
        for p in (self.browse, self.queue_page, self.library, self.settings_page):
            self.stack.addWidget(p)
        for i, b in enumerate((self.nav_browse, self.nav_queue, self.nav_library, self.nav_settings)):
            b.clicked.connect(lambda _=False, i=i: self.show_page(i))
        self.nav_browse.setChecked(True)

        self.toasts = ToastHost(central)
        ctx.toast = self.toasts.show
        self._build_status_bar()
        self._build_shortcuts()
        self._build_tray()

        # -- wiring --------------------------------------------------------------------------------
        self.browse.queue_url.connect(self.quick_add)
        self.browse.fullscreen_requested.connect(self._fullscreen)
        self.browse.panel.open_queue.connect(lambda: self.show_page(1))
        self.queue_page.add_urls_requested.connect(self.add_urls)
        self.library.redownload.connect(self._redownload)
        self.queue.counts_changed.connect(self._counts_changed)
        self.queue.finished.connect(self._task_finished)
        ctx.tools_changed.connect(self._update_status)
        ctx.auth_changed.connect(self._update_status)
        ctx.settings_changed.connect(self._setting_changed)
        ctx.update_state.connect(lambda *_: self._update_status())

        self._last_clip = ""
        self._ignore_clip_until = 0.0
        QGuiApplication.clipboard().dataChanged.connect(self._clipboard_changed)
        self._clip_timer = QTimer(self)
        self._clip_timer.setInterval(1500)
        self._clip_timer.timeout.connect(self._clipboard_changed)
        if paths.IS_MAC and ctx.settings.get("clipboard_monitor"):
            self._clip_timer.start()
        self._speed_timer = QTimer(self)
        self._speed_timer.setInterval(1000)
        self._speed_timer.timeout.connect(self._update_activity)
        self._speed_timer.start()

        geo = ctx.settings.get("window_geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        else:
            self.resize(1360, 860)

        restored = self.queue.restore()
        if restored:
            QTimer.singleShot(800, lambda: ctx.notify(tr("queue.restored", n=restored), "info", tr("queue.resume_all"),
                                                      self.queue.resume_all, 8000))
        ctx.refresh_tools()
        if ctx.settings.get("auto_update"):
            QTimer.singleShot(4000, ctx.check_updates)
        self.browse.go_home()
        self._update_status()

    # ------------------------------------------------------------------------------------------
    # Layout pieces
    # ------------------------------------------------------------------------------------------
    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.sb_msg = QLabel(tr("sb.ready"))
        self.sb_msg.setContentsMargins(10, 0, 0, 0)
        sb.addWidget(self.sb_msg, 1)

        def chip(tip_key: str, handler) -> QToolButton:
            b = QToolButton()
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setIconSize(QSize(14, 14))
            b.setToolTip(tr(tip_key))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(handler)
            sb.addPermanentWidget(b)
            return b

        self.sb_ytdlp = chip("sb.tip_ytdlp", self._ytdlp_menu)
        self.sb_js = chip("sb.tip_js", lambda: self.open_settings("engine"))
        self.sb_ff = chip("sb.tip_ffmpeg", lambda: FfmpegSetupDialog(self.ctx, self).exec())
        self.sb_auth = chip("sb.tip_auth", lambda: self.open_settings("account"))
        self.sb_free = chip("sb.tip_free", lambda: paths.open_path(self.ctx.settings.save_dir()))

    def _build_shortcuts(self) -> None:
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.ShortcutContext.ApplicationShortcut)
            s.activated.connect(fn)

        sc("Ctrl+1", lambda: self.show_page(0))
        sc("Ctrl+2", lambda: self.show_page(1))
        sc("Ctrl+3", lambda: self.show_page(2))
        sc("Ctrl+,", lambda: self.show_page(3))
        sc("Ctrl+D", self._download_current)
        sc("Ctrl+L", self._focus_address)
        sc("F5", lambda: self.browse.view.reload())
        sc("Ctrl+R", lambda: self.browse.view.reload())
        sc("Ctrl+Shift+V", lambda: self.add_urls(QGuiApplication.clipboard().text()))
        esc = QShortcut(QKeySequence("Escape"), self)
        esc.activated.connect(self._escape)

    def _build_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Kura")
        menu = QMenu()
        menu.addAction(tr("nav.browse")).triggered.connect(lambda: (self.showNormal(), self.activateWindow()))
        menu.addAction(tr("queue.pause_all")).triggered.connect(self.queue.pause_all)
        menu.addAction(tr("queue.resume_all")).triggered.connect(self.queue.resume_all)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(lambda *_: (self.showNormal(), self.activateWindow()))
        self.tray.show()

    # ------------------------------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------------------------------
    def show_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        (self.nav_browse, self.nav_queue, self.nav_library, self.nav_settings)[index].setChecked(True)

    def open_settings(self, section: str) -> None:
        self.show_page(3)
        QTimer.singleShot(50, lambda: self.settings_page.scroll_to(section))

    def _focus_address(self) -> None:
        self.show_page(0)
        self.browse.focus_address()

    def _download_current(self) -> None:
        if self.stack.currentIndex() == 0:
            self.browse.panel.trigger_download()

    def _escape(self) -> None:
        if self.isFullScreen():
            self.browse.exit_fullscreen()

    def _fullscreen(self, on: bool) -> None:
        self.rail.setVisible(not on)
        self.statusBar().setVisible(not on)
        if on:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
            self.ctx.notify(tr("toast.fullscreen_exit"), "info", timeout=2500)
        else:
            self.showMaximized() if getattr(self, "_was_maximized", False) else self.showNormal()

    # ------------------------------------------------------------------------------------------
    # Adding downloads
    # ------------------------------------------------------------------------------------------
    def quick_add(self, url: str) -> None:
        """Add one URL with the panel's current preset (lists open the picker)."""
        c = urls.classify(url)
        preset = self.browse.panel.current_preset()
        subs = self.browse.panel._subtitle_options()
        if c.is_collection:
            PlaylistDialog(self.ctx, self.queue, url, preset.id, subs, self).exec()
            return
        if c.video_id:
            url = urls.canonical_video_url(c.video_id)
        self.queue.add(url, preset, video_id=c.video_id or "", subtitles=subs)
        self.ctx.notify(tr("panel.added") + "\n" + url, "success")

    def add_urls(self, text: str = "") -> None:
        dlg = AddUrlsDialog(self.ctx, text, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        preset = self.ctx.preset(data["preset_id"])
        skipped = 0
        added = 0
        for u in data["singles"]:
            c = urls.classify(u)
            if c.video_id:
                u = urls.canonical_video_url(c.video_id)
                if self.ctx.settings.get("skip_downloaded") and self.ctx.db.find_downloaded(c.video_id, preset.id):
                    skipped += 1
                    continue
            self.queue.add(u, preset, video_id=c.video_id or "", subtitles=data["subtitles"])
            added += 1
        if added:
            self.ctx.notify(tr("queue.added_n", n=added), "success")
        if skipped:
            self.ctx.notify(tr("queue.skipped_n", n=skipped), "info")
        for u in data["collections"]:
            PlaylistDialog(self.ctx, self.queue, u, preset.id, data["subtitles"], self).exec()
        if not (data["singles"] or data["collections"]):
            self.ctx.notify(tr("queue.no_urls"), "warning")

    def _redownload(self, url: str, preset_id: str, title: str) -> None:
        if not url:
            return
        preset = self.ctx.preset(preset_id)
        c = urls.classify(url)
        self.queue.add(url, preset, title=title, video_id=c.video_id or "")
        self.ctx.notify(tr("panel.added") + "\n" + (title or url), "success")

    # drag & drop anywhere
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        md = e.mimeData()
        text = "\n".join(u.toString() for u in md.urls()) if md.hasUrls() else md.text()
        self.add_urls(text)

    # ------------------------------------------------------------------------------------------
    # Clipboard
    # ------------------------------------------------------------------------------------------
    def _clipboard_changed(self) -> None:
        if not self.ctx.settings.get("clipboard_monitor"):
            return
        text = QGuiApplication.clipboard().text()
        if time.monotonic() < self._ignore_clip_until or (self.isActiveWindow() and self.stack.currentIndex() != 1):
            # Copies made inside Kura (address bar, "copy URL" …) are not offered, except on the queue page.
            self._last_clip = text
            return
        if not text or text == self._last_clip:
            return
        self._last_clip = text
        found = []
        for u in urls.extract_urls(text)[:20]:
            c = urls.classify(u)
            if (c.is_youtube and c.kind in ("video", "playlist", "channel")) or \
                    any(c.site == h or c.site.endswith("." + h) for h in _QUICK_HOSTS):
                found.append(u)
        if not found:
            return
        if self.ctx.settings.get("clipboard_auto_add") and len(found) == 1:
            self.quick_add(found[0])
            return
        self._pending_clip = (found, text)
        if self.isActiveWindow():
            self._offer_clipboard()

    def _offer_clipboard(self) -> None:
        pending = getattr(self, "_pending_clip", None)
        self._pending_clip = None
        if not pending:
            return
        found, text = pending
        show = found[0] if len(found) == 1 else f"{found[0]} (+{len(found) - 1})"
        self.ctx.notify(tr("clip.found", url=show), "info", tr("clip.add"),
                        (lambda: self.quick_add(found[0])) if len(found) == 1 else (lambda: self.add_urls(text)),
                        timeout=10000)

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        from PyQt6.QtCore import QEvent

        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(150, self._offer_clipboard)

    def suppress_clipboard(self, seconds: float = 1.5) -> None:
        self._ignore_clip_until = time.monotonic() + seconds

    # ------------------------------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------------------------------
    def _update_status(self) -> None:
        c = self.ctx
        if c.update_available:
            self.sb_ytdlp.setText(tr("sb.ytdlp_update", ver=c.ytdlp_version))
            THEME.bind_icon(self.sb_ytdlp, "ph.arrow-circle-up", "warning")
        else:
            self.sb_ytdlp.setText(tr("sb.ytdlp", ver=c.ytdlp_version or "?"))
            THEME.bind_icon(self.sb_ytdlp, "ph.check-circle", "success")
        ready = c.tools_ready
        self.sb_js.setText(tr("sb.js_ok") if c.deno.ok or not ready else tr("sb.js_missing"))
        THEME.bind_icon(self.sb_js, "ph.check-circle" if c.deno.ok or not ready else "ph.warning",
                        "success" if c.deno.ok else ("muted" if not ready else "warning"))
        self.sb_ff.setText(tr("sb.ffmpeg_ok") if c.ffmpeg.ok or not ready else tr("sb.ffmpeg_missing"))
        THEME.bind_icon(self.sb_ff, "ph.check-circle" if c.ffmpeg.ok or not ready else "ph.warning",
                        "success" if c.ffmpeg.ok else ("muted" if not ready else "warning"))
        self.sb_auth.setText(c.auth_label())
        THEME.bind_icon(self.sb_auth, "ph.user-circle", "muted")
        free = paths.free_space(c.settings.save_dir())
        self.sb_free.setText(tr("sb.free", size=human_size(free)) if free is not None else "")
        THEME.bind_icon(self.sb_free, "ph.hard-drives", "muted")
        if ready and not c.ffmpeg.ok and not getattr(self, "_ff_warned", False):
            self._ff_warned = True
            c.notify(tr("set.ffmpeg_missing"), "warning", tr("set.ffmpeg_setup"),
                     lambda: FfmpegSetupDialog(c, self).exec(), 12000)

    def _update_activity(self) -> None:
        n = self.queue.running_count()
        if n:
            self.sb_msg.setText(tr("sb.running", n=n, speed=human_size(self.queue.total_speed()) + "/s"))
        else:
            self.sb_msg.setText(tr("sb.ready"))

    def _counts_changed(self, active: int, _total: int) -> None:
        self.nav_queue.set_badge(active)
        self._update_activity()

    def _setting_changed(self, key: str) -> None:
        if key in ("save_dir", "auth_mode", "cookie_file"):
            self._update_status()
        elif key == "clipboard_monitor" and paths.IS_MAC:
            (self._clip_timer.start if self.ctx.settings.get(key) else self._clip_timer.stop)()

    def _ytdlp_menu(self) -> None:
        m = QMenu(self)
        m.addAction(tr("set.check_update")).triggered.connect(self.update_engine)
        m.addAction(tr("nav.settings")).triggered.connect(lambda: self.open_settings("engine"))
        m.exec(self.sb_ytdlp.mapToGlobal(self.sb_ytdlp.rect().topLeft()))

    def _task_finished(self, task_id: str, ok: bool) -> None:
        t = self.queue.tasks.get(task_id)
        if not t:
            return
        title = t.title or t.url
        if ok:
            files = (t.result or {}).get("files") or []
            self.ctx.notify(tr("queue.finished_toast", title=title), "success", tr("queue.open_folder"),
                            (lambda f=files[0]: paths.reveal_in_file_manager(f)) if files else None)
            if self.ctx.settings.get("notifications"):
                if self.tray and not self.isActiveWindow():
                    self.tray.showMessage(tr("queue.notify_done_title"), title, app_icon(), 5000)
                QApplication.alert(self, 0)
            self._update_status()
        else:
            self.ctx.notify(tr("queue.failed_toast", title=title) + "\n" + tr(f"err.{t.error_kind or 'unknown'}.title"),
                            "error", tr("queue.details"), lambda: self.queue_page.show_details(task_id), 9000)

    # ------------------------------------------------------------------------------------------
    # Account / engine actions
    # ------------------------------------------------------------------------------------------
    def open_login(self) -> None:
        self.show_page(0)
        self.browse.load("https://accounts.google.com/ServiceLogin?service=youtube&continue=https://www.youtube.com/")
        self.ctx.notify(tr("toast.login_page"), "info", timeout=8000)

    def logout_internal(self) -> None:
        self.profile.cookieStore().deleteAllCookies()
        self.profile.clearHttpCache()
        self.profile.clearAllVisitedLinks()
        self.ctx.clear_cookies()
        self.browse.go_home()
        self.ctx.notify(tr("set.logged_out"), "success")

    def clear_web_cache(self) -> None:
        self.profile.clearHttpCache()

    def update_engine(self) -> None:
        if self.queue.running_count():
            self.ctx.notify(tr("set.update_busy"), "warning")
            return
        from ..core import updater

        channel = self.ctx.settings.get("ytdlp_channel", "stable")
        self.ctx.notify(tr("set.update_running"), "info", timeout=3000)
        lines: list[str] = []

        def work(log, *_):
            cur, latest, avail = updater.check(channel)
            if latest is None:
                return "offline"
            if not avail:
                return "latest:" + latest
            return "updated" if updater.update(channel, lambda m: (lines.append(m), log(m))) else "failed"

        def done(res: str) -> None:
            if res == "offline":
                self.ctx.notify(tr("set.update_offline"), "warning")
            elif res.startswith("latest:"):
                self.ctx.update_available = False
                self.ctx.notify(tr("set.update_latest", ver=res.split(":", 1)[1]), "success")
            elif res == "updated":
                self.ctx.update_available = False
                self.ctx.notify(tr("set.update_done"), "success", tr("common.restart_now"), self.restart, 15000)
            else:
                self.ctx.notify(tr("set.update_failed") + "\n" + "\n".join(lines[-3:]), "error", timeout=12000)
            self.ctx.refresh_tools()

        self.ctx.run_bg(work, done, lambda m: self.ctx.notify(m, "error"))

    def restart(self) -> None:
        import sys

        from PyQt6.QtCore import QProcess

        if self.close_for_restart():
            QProcess.startDetached(sys.executable, ["-m", "kura"], str(paths.APP_DIR))
            QApplication.quit()

    def close_for_restart(self) -> bool:
        self._restarting = True
        return self.close()

    # ------------------------------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------------------------------
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.toasts.relayout()

    def closeEvent(self, e) -> None:  # noqa: N802
        if self.queue.running_count() and not getattr(self, "_restarting", False):
            if not confirm(self, tr("queue.quit_title"), tr("queue.quit_body")):
                e.ignore()
                return
        self.queue.stop_all_for_exit()
        self.ctx.settings.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        if self.tray:
            self.tray.hide()
        e.accept()

    def shutdown(self) -> None:
        """Delete the window (and its web page) before the browser profile."""
        from PyQt6 import sip

        profile = self.profile
        try:
            self.browse.view.stop()
            sip.delete(self)
            sip.delete(profile)
        except (RuntimeError, TypeError):
            pass

    def first_run(self) -> None:
        if not self.ctx.settings.get("first_run_done"):
            FirstRunDialog(self.ctx, self).exec()
            self.ctx.set("first_run_done", True)
            self._update_status()
