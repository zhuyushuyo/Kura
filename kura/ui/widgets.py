"""Small reusable widgets."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import THEME


def label(text: str = "", obj: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    lb.setTextFormat(Qt.TextFormat.PlainText)  # titles from the web must never be read as HTML
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def flat_button(icon_name: str, tooltip: str = "", role: str = "text", size: int = 18,
                checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setObjectName("Flat")
    THEME.bind_icon(b, icon_name, role)
    b.setIconSize(QSize(size, size))
    b.setToolTip(tooltip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"color: {THEME.color('border')};")
    return f


class NavButton(QToolButton):
    def __init__(self, icon_name: str, text: str):
        super().__init__()
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setText(text)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        THEME.bind_icon(self, icon_name, "text")
        self.setIconSize(QSize(22, 22))
        self.setFixedSize(66, 58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.badge = QLabel(self)
        self.badge.setObjectName("Badge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.hide()

    def set_badge(self, n: int) -> None:
        if n <= 0:
            self.badge.hide()
            return
        self.badge.setText(str(n) if n < 100 else "99+")
        self.badge.adjustSize()
        w = max(16, self.badge.sizeHint().width())
        self.badge.setFixedSize(w, 16)
        self.badge.move(self.width() - w - 8, 4)
        self.badge.show()
        self.badge.raise_()


class ElidedLabel(QLabel):
    """Single-line label that elides its text to the available width."""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle):
        super().__init__()
        self._full = text
        self._mode = mode
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        self.setToolTip(self._full)
        self._update()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._update()

    def _update(self) -> None:
        fm = QFontMetrics(self.font())
        super().setText(fm.elidedText(self._full, self._mode, max(self.width(), 20)))


class ThumbLabel(QLabel):
    """Rounded thumbnail with a placeholder icon."""

    def __init__(self, w: int, h: int, radius: int = 8):
        super().__init__()
        self.setObjectName("Thumb")
        self._w, self._h, self._r = w, h, radius
        self.setFixedSize(w, h)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clear_thumb()

    def set_size(self, w: int, h: int) -> None:
        self._w, self._h = w, h
        self.setFixedSize(w, h)

    def clear_thumb(self, icon_name: str = "ph.film-strip") -> None:
        ico = THEME.icon(icon_name, "muted")
        self.setPixmap(ico.pixmap(QSize(min(32, self._h // 2 or 16), min(32, self._h // 2 or 16))))

    def set_image_bytes(self, data: bytes | None) -> bool:
        if not data:
            return False
        pm = QPixmap()
        if not pm.loadFromData(data):
            return False
        self.set_pixmap_rounded(pm)
        return True

    def set_image_file(self, path: str | None) -> bool:
        if not path:
            return False
        pm = QPixmap(path)
        if pm.isNull():
            return False
        self.set_pixmap_rounded(pm)
        return True

    def set_pixmap_rounded(self, pm: QPixmap) -> None:
        dpr = self.devicePixelRatioF()
        W, H = int(self._w * dpr), int(self._h * dpr)
        scaled = pm.scaled(W, H, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
        x = (scaled.width() - W) // 2
        y = (scaled.height() - H) // 2
        scaled = scaled.copy(x, y, W, H)
        out = QPixmap(W, H)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, W, H), self._r * dpr, self._r * dpr)
        p.setClipPath(path)
        p.drawPixmap(0, 0, scaled)
        p.end()
        out.setDevicePixelRatio(dpr)
        super().setPixmap(out)


class CollapsibleSection(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, title: str, expanded: bool = False):
        super().__init__()
        self.setObjectName("Section")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.header = QToolButton()
        self.header.setObjectName("SectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4, 0, 0, 6)
        self.body_layout.setSpacing(6)
        lay.addWidget(self.header)
        lay.addWidget(self.body)
        self.header.toggled.connect(self._on_toggle)
        self._on_toggle(expanded)

    def _on_toggle(self, on: bool) -> None:
        THEME.bind_icon(self.header, "ph.caret-down" if on else "ph.caret-right", "muted")
        self.body.setVisible(on)
        self.toggled.emit(on)

    def set_title(self, text: str) -> None:
        self.header.setText(text)

    def expand(self, on: bool = True) -> None:
        self.header.setChecked(on)


TOAST_WIDTH = 400


def shorten(text: str, limit: int = 90) -> str:
    """Cut overly long one-line text (titles, paths) for compact messages."""
    text = text or ""
    if len(text) <= limit:
        return text
    head = int(limit * 0.45)
    return text[:head] + "…" + text[-(limit - head - 1):]


class Toast(QFrame):
    """Fixed-width notification: text wraps, the action button sits below it
    (so long titles can never run under the button)."""

    closed = pyqtSignal()

    def __init__(self, parent: QWidget, text: str, kind: str = "info", action: str | None = None,
                 callback: Callable[[], None] | None = None, timeout: int = 4500):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 8, 10)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        icon_name = {"info": "ph.info", "success": "ph.check-circle", "warning": "ph.warning",
                     "error": "ph.x-circle"}.get(kind, "ph.info")
        ico = QLabel()
        ico.setFixedSize(18, 18)
        role = {"success": "success", "warning": "warning", "error": "danger"}.get(kind, "toast_text")
        ico.setPixmap(THEME.icon(icon_name, role).pixmap(QSize(18, 18)))
        top.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
        lines = [shorten(line, 160) for line in (text or "").split("\n")]
        self.label = QLabel("\n".join(lines))
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(self.label, 1)
        x = QToolButton()
        x.setIcon(THEME.icon("ph.x", "toast_text"))
        x.setIconSize(QSize(12, 12))
        x.clicked.connect(self.dismiss)
        top.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(top)
        self.button: QPushButton | None = None
        if action and callback:
            row = QHBoxLayout()
            row.setContentsMargins(28, 0, 6, 0)
            row.addStretch(1)
            b = QPushButton(action.replace("&", "&&"))
            b.setCursor(Qt.CursorShape.PointingHandCursor)

            def run() -> None:
                callback()
                self.dismiss()

            b.clicked.connect(run)
            row.addWidget(b)
            outer.addLayout(row)
            self.button = b
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        if timeout > 0:
            QTimer.singleShot(timeout, self.dismiss)
        self._closing = False

    def dismiss(self) -> None:
        if self._closing:
            return
        self._closing = True
        anim = QPropertyAnimation(self._effect, b"opacity", self)
        anim.setDuration(180)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._done)
        anim.start()

    def _done(self) -> None:
        self.hide()
        self.closed.emit()
        self.deleteLater()


class ToastHost:
    """Stacks toasts in the bottom-right corner of a widget."""

    def __init__(self, host: QWidget):
        self.host = host
        self.toasts: list[Toast] = []

    def show(self, text: str, kind: str = "info", action: str | None = None,
             callback: Callable[[], None] | None = None, timeout: int = 4500) -> None:
        t = Toast(self.host, text, kind, action, callback, timeout)
        t.closed.connect(lambda t=t: self._remove(t))
        self.toasts.append(t)
        if len(self.toasts) > 4:
            self.toasts[0].dismiss()
        t.show()
        t.raise_()
        self.relayout()

    def _remove(self, t: Toast) -> None:
        if t in self.toasts:
            self.toasts.remove(t)
        self.relayout()

    def relayout(self) -> None:
        y = self.host.height() - 16
        w = max(220, min(TOAST_WIDTH, self.host.width() - 32))
        for t in reversed(self.toasts):
            t.setFixedWidth(w)
            t.layout().activate()
            h = t.heightForWidth(w) if t.hasHeightForWidth() else t.sizeHint().height()
            h = max(h, t.minimumSizeHint().height())
            t.setFixedHeight(h)
            y -= h
            t.move(self.host.width() - w - 16, y)
            y -= 8


class IconLabel(QLabel):
    """A QLabel showing an icon (works with ``THEME.bind_icon``)."""

    def __init__(self, size: int = 18):
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)

    def setIcon(self, icon) -> None:  # noqa: N802
        self.setPixmap(icon.pixmap(QSize(self._size, self._size)))


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, body: str):
        super().__init__()
        self.setObjectName("EmptyState")
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        ico = IconLabel(56)
        THEME.bind_icon(ico, icon_name, "muted")
        lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = QLabel(title)
        self.title.setObjectName("H2")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body = QLabel(body)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.body)
        lay.addStretch(2)
