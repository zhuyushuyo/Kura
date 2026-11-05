"""The built-in browser (QtWebEngine): profile, page and view."""

from __future__ import annotations

import re

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .. import i18n, paths
from ..core.cookies import CookieRecord
from .context import AppContext
from .theme import THEME

tr = i18n.tr


def create_profile(ctx: AppContext, parent) -> QWebEngineProfile:
    profile = QWebEngineProfile("kura", parent)
    storage = paths.WEB_DIR
    storage.mkdir(parents=True, exist_ok=True)
    profile.setPersistentStoragePath(str(storage / "storage"))
    profile.setCachePath(str(storage / "cache"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    # Present as the real Chromium the engine is (drop the "QtWebEngine/x" token).
    ua = re.sub(r"\s*QtWebEngine/\S+", "", profile.httpUserAgent())
    profile.setHttpUserAgent(ua)
    s = profile.settings()
    s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
    s.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)

    store = profile.cookieStore()

    def to_record(c) -> CookieRecord:
        exp = None if c.isSessionCookie() else int(c.expirationDate().toSecsSinceEpoch())
        return CookieRecord(
            domain=c.domain() or "", name=bytes(c.name()).decode("utf-8", "replace"),
            value=bytes(c.value()).decode("utf-8", "replace"), path=c.path() or "/",
            secure=c.isSecure(), expires=exp, http_only=c.isHttpOnly(),
        )

    store.cookieAdded.connect(lambda c: ctx.update_cookie(to_record(c)))
    store.cookieRemoved.connect(lambda c: ctx.update_cookie(to_record(c), removed=True))
    store.loadAllCookies()

    def on_download(item) -> None:  # page-initiated downloads are not used
        item.cancel()
        ctx.notify(tr("browse.web_download_blocked"), "info")

    profile.downloadRequested.connect(on_download)
    return profile


class KuraWebPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, parent=None):
        super().__init__(profile, parent)
        self.setBackgroundColor(THEME.qcolor("base"))
        # Deny camera / microphone / location / notifications etc. by default.
        if hasattr(self, "permissionRequested"):
            self.permissionRequested.connect(lambda perm: perm.deny())
        elif hasattr(self, "featurePermissionRequested"):  # Qt < 6.8
            self.featurePermissionRequested.connect(
                lambda origin, feature: self.setFeaturePermission(
                    origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser))

    def createWindow(self, _type):  # noqa: N802 - open "new window" links in place
        return self

    def javaScriptConsoleMessage(self, *args) -> None:  # noqa: N802 - keep the console quiet
        pass


class KuraWebView(QWebEngineView):
    queue_link = pyqtSignal(str)
    preview_link = pyqtSignal(str)
    download_page = pyqtSignal()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = self.createStandardContextMenu()
        req = self.lastContextMenuRequest()
        link = req.linkUrl() if req is not None else QUrl()
        first = menu.actions()[0] if menu.actions() else None
        added = []
        if link.isValid() and link.scheme() in ("http", "https"):
            url = link.toString()
            a1 = QAction(THEME.icon("ph.download-simple", "accent"), tr("browse.ctx_queue_link"), menu)
            a1.triggered.connect(lambda: self.queue_link.emit(url))
            a2 = QAction(THEME.icon("ph.magnifying-glass"), tr("browse.ctx_preview_link"), menu)
            a2.triggered.connect(lambda: self.preview_link.emit(url))
            added += [a1, a2]
        else:
            a3 = QAction(THEME.icon("ph.download-simple", "accent"), tr("browse.ctx_download_page"), menu)
            a3.triggered.connect(self.download_page.emit)
            added.append(a3)
        for a in added:
            menu.insertAction(first, a) if first else menu.addAction(a)
        if first:
            menu.insertSeparator(first)
        menu.popup(event.globalPos())
