"""Browse page: toolbar + built-in browser + download panel."""

from __future__ import annotations

from PyQt6.QtCore import QSize, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QProgressBar, QVBoxLayout, QWidget

from .. import i18n
from ..core import urls
from .context import AppContext
from .download_panel import DownloadPanel
from .web import KuraWebPage, KuraWebView
from .widgets import flat_button

tr = i18n.tr


class BrowserPage(QWidget):
    fullscreen_requested = pyqtSignal(bool)
    queue_url = pyqtSignal(str)

    def __init__(self, ctx: AppContext, queue, profile):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QFrame()
        bar.setObjectName("Toolbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 6, 10, 6)
        bl.setSpacing(4)
        self.b_back = flat_button("ph.arrow-left", tr("browse.back"))
        self.b_fwd = flat_button("ph.arrow-right", tr("browse.forward"))
        self.b_reload = flat_button("ph.arrow-clockwise", tr("browse.reload"))
        self.b_home = flat_button("ph.house", tr("browse.home"))
        for b in (self.b_back, self.b_fwd, self.b_reload, self.b_home):
            bl.addWidget(b)
        self.address = QLineEdit()
        self.address.setObjectName("Address")
        self.address.setPlaceholderText(tr("browse.address_ph"))
        self.address.setClearButtonEnabled(True)
        self.address.returnPressed.connect(self._go)
        bl.addWidget(self.address, 1)
        self.b_ext = flat_button("ph.arrow-square-out", tr("browse.open_external"))
        self.b_panel = flat_button("ph.columns", tr("browse.toggle_panel"), checkable=True)
        self.b_panel.setChecked(bool(ctx.settings.get("panel_visible", True)))
        bl.addWidget(self.b_ext)
        bl.addWidget(self.b_panel)
        lay.addWidget(bar)
        self.toolbar = bar

        self.load_bar = QProgressBar()
        self.load_bar.setObjectName("Thin")
        self.load_bar.setRange(0, 100)
        self.load_bar.setTextVisible(False)
        self.load_bar.setVisible(False)
        lay.addWidget(self.load_bar)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.view = KuraWebView()
        self.page = KuraWebPage(profile, self.view)
        self.view.setPage(self.page)
        row.addWidget(self.view, 1)
        self.panel = DownloadPanel(ctx, queue)
        self.panel.setVisible(self.b_panel.isChecked())
        row.addWidget(self.panel)
        lay.addLayout(row, 1)

        self.b_back.clicked.connect(self.view.back)
        self.b_fwd.clicked.connect(self.view.forward)
        self.b_reload.clicked.connect(self._reload_or_stop)
        self.b_home.clicked.connect(self.go_home)
        self.b_ext.clicked.connect(lambda: QDesktopServices.openUrl(self.view.url()))
        self.b_panel.toggled.connect(self._toggle_panel)
        self.view.urlChanged.connect(self._url_changed)
        self.view.loadStarted.connect(self._load_started)
        self.view.loadProgress.connect(self.load_bar.setValue)
        self.view.loadFinished.connect(self._load_finished)
        self.view.queue_link.connect(self.queue_url.emit)
        self.view.preview_link.connect(lambda u: self.load(u))
        self.view.download_page.connect(self.panel.trigger_download)
        self.page.fullScreenRequested.connect(self._fullscreen)
        self._loading = False

    # -- navigation -------------------------------------------------------------------------
    def go_home(self) -> None:
        self.load(self.ctx.settings.get("home_url") or "https://www.youtube.com")

    def load(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if urls.looks_like_url(text):
            url = urls.normalize(text)
        else:
            url = urls.search_url(text)
        self.view.load(QUrl(url))

    def _go(self) -> None:
        self.load(self.address.text())
        self.view.setFocus()

    def focus_address(self) -> None:
        self.address.setFocus()
        self.address.selectAll()

    def _reload_or_stop(self) -> None:
        if self._loading:
            self.view.stop()
        else:
            self.view.reload()

    def _load_started(self) -> None:
        self._loading = True
        self.load_bar.setVisible(True)
        from .theme import THEME

        THEME.bind_icon(self.b_reload, "ph.x")

    def _load_finished(self, _ok: bool) -> None:
        self._loading = False
        self.load_bar.setVisible(False)
        from .theme import THEME

        THEME.bind_icon(self.b_reload, "ph.arrow-clockwise")

    def _url_changed(self, qurl: QUrl) -> None:
        url = qurl.toString()
        if not self.address.hasFocus():
            self.address.setText(url)
        self.b_back.setEnabled(self.view.history().canGoBack())
        self.b_fwd.setEnabled(self.view.history().canGoForward())
        self.panel.set_url(url)

    def _toggle_panel(self, on: bool) -> None:
        self.panel.setVisible(on)
        self.ctx.set("panel_visible", on)

    # -- full screen ----------------------------------------------------------------------------
    def _fullscreen(self, request) -> None:
        request.accept()
        on = request.toggleOn()
        self.toolbar.setVisible(not on)
        self.panel.setVisible((not on) and self.b_panel.isChecked())
        self.fullscreen_requested.emit(on)

    def exit_fullscreen(self) -> None:
        from PyQt6.QtWebEngineCore import QWebEnginePage

        self.page.triggerAction(QWebEnginePage.WebAction.ExitFullScreen)

    def icon_size(self) -> QSize:
        return QSize(18, 18)

    def current_url(self) -> str:
        return self.view.url().toString()
