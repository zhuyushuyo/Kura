"""Colours, style sheet, icons and the app icon."""

from __future__ import annotations

import weakref

import qtawesome as qta
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QApplication

LIGHT = {
    "window": "#F4F3EF", "base": "#FFFFFF", "alt": "#FAF9F6", "panel": "#ECEBE6", "rail": "#E9E7E1",
    "text": "#1F2328", "muted": "#6A6E75", "border": "#DCDAD3", "accent": "#36568C",
    "accent_hover": "#2C4877", "accent_text": "#FFFFFF", "selection": "#D5DFEF",
    "success": "#2E7D4F", "warning": "#A86B12", "danger": "#BF3B30", "toast": "#24272C", "toast_text": "#F4F3EF",
}
DARK = {
    "window": "#1A1B1E", "base": "#222327", "alt": "#25262B", "panel": "#2C2D33", "rail": "#151619",
    "text": "#E6E6E8", "muted": "#9A9DA4", "border": "#35373D", "accent": "#7F9FD9",
    "accent_hover": "#97B3E6", "accent_text": "#10151F", "selection": "#34425E",
    "success": "#5CBF86", "warning": "#E2A93B", "danger": "#E7695C", "toast": "#E6E6E8", "toast_text": "#1A1B1E",
}


class _Theme:
    def __init__(self) -> None:
        self.c = dict(LIGHT)
        self.dark = False
        self.mode = "system"
        self._bindings: list[tuple[weakref.ref, str, str, str]] = []
        self._arrows: dict[str, str] = {}

    # -- colours -----------------------------------------------------------------
    def color(self, role: str) -> str:
        return self.c.get(role, role)

    def qcolor(self, role: str) -> QColor:
        return QColor(self.color(role))

    # -- icons ---------------------------------------------------------------------
    def icon(self, name: str, role: str = "text") -> QIcon:
        try:
            return qta.icon(name, color=self.color(role), color_disabled=self.color("border"))
        except Exception:  # noqa: BLE001 - unknown icon name
            return QIcon()

    def bind_icon(self, widget, name: str, role: str = "text", method: str = "setIcon") -> None:
        """Set an icon and keep it in sync with theme changes (re-binding replaces)."""
        getattr(widget, method)(self.icon(name, role))
        self._bindings = [b for b in self._bindings if not (b[0]() is widget and b[3] == method)]
        self._bindings.append((weakref.ref(widget), name, role, method))

    def _refresh_icons(self) -> None:
        alive = []
        for ref, name, role, method in self._bindings:
            w = ref()
            if w is None:
                continue
            try:
                getattr(w, method)(self.icon(name, role))
                alive.append((ref, name, role, method))
            except RuntimeError:
                continue
        self._bindings = alive

    # -- apply -----------------------------------------------------------------------
    def system_is_dark(self) -> bool:
        try:
            scheme = QGuiApplication.styleHints().colorScheme()
            return scheme == Qt.ColorScheme.Dark
        except AttributeError:
            pal = QGuiApplication.palette()
            return pal.color(QPalette.ColorRole.Window).lightness() < 128

    def apply(self, mode: str | None = None) -> None:
        app = QApplication.instance()
        if mode:
            self.mode = mode
        self.dark = self.system_is_dark() if self.mode == "system" else self.mode == "dark"
        self.c = dict(DARK if self.dark else LIGHT)
        app.setStyle("Fusion")
        app.setPalette(self._palette())
        self._arrows = self._arrow_images()
        app.setStyleSheet(self._qss())
        self._refresh_icons()

    def _arrow_images(self) -> dict[str, str]:
        """Small PNG arrows for spin boxes / combo boxes in the current colour."""
        from .. import paths

        out: dict[str, str] = {}
        folder = paths.CACHE_DIR / "ui"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for name, icon in (("up", "ph.caret-up"), ("down", "ph.caret-down")):
                p = folder / f"arrow-{name}-{self.color('muted').lstrip('#')}.png"
                if not p.exists():
                    self.icon(icon, "muted").pixmap(24, 24).save(str(p))
                out[name] = str(p).replace("\\", "/")
        except OSError:
            pass
        return out

    def _palette(self) -> QPalette:
        c = self.c
        pal = QPalette()
        R = QPalette.ColorRole

        def setc(role, value, group=None):
            if group is None:
                pal.setColor(role, QColor(value))
            else:
                pal.setColor(group, role, QColor(value))

        setc(R.Window, c["window"])
        setc(R.WindowText, c["text"])
        setc(R.Base, c["base"])
        setc(R.AlternateBase, c["alt"])
        setc(R.Text, c["text"])
        setc(R.Button, c["base"])
        setc(R.ButtonText, c["text"])
        setc(R.Highlight, c["accent"])
        setc(R.HighlightedText, c["accent_text"])
        setc(R.ToolTipBase, c["base"])
        setc(R.ToolTipText, c["text"])
        setc(R.PlaceholderText, c["muted"])
        setc(R.Link, c["accent"])
        setc(R.Mid, c["border"])
        setc(R.Midlight, c["panel"])
        setc(R.Dark, c["border"])
        setc(R.Light, c["base"])
        for role in (R.Text, R.WindowText, R.ButtonText):
            setc(role, c["muted"], QPalette.ColorGroup.Disabled)
        return pal

    def _qss(self) -> str:
        c = self.c
        return f"""
QWidget {{ font-size: 13px; }}
QMainWindow, #Page {{ background: {c['window']}; }}
#NavRail {{ background: {c['rail']}; border-right: 1px solid {c['border']}; }}
#NavRail QToolButton {{ border: none; border-radius: 10px; padding: 7px 2px 5px 2px; color: {c['muted']};
    font-size: 11px; }}
#NavRail QToolButton:hover {{ background: {c['panel']}; color: {c['text']}; }}
#NavRail QToolButton:checked {{ background: {c['selection']}; color: {c['text']}; font-weight: 600; }}
#Badge {{ background: {c['accent']}; color: {c['accent_text']}; border-radius: 8px; font-size: 10px;
    font-weight: 700; padding: 0px 4px; }}
#Toolbar {{ background: {c['window']}; border-bottom: 1px solid {c['border']}; }}
#Panel {{ background: {c['alt']}; border-left: 1px solid {c['border']}; }}
#Card {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 12px; }}
#Section {{ background: transparent; }}
#Title {{ font-size: 20px; font-weight: 700; }}
#H2 {{ font-size: 15px; font-weight: 700; }}
#H3 {{ font-size: 13px; font-weight: 700; }}
#VideoTitle {{ font-size: 14px; font-weight: 700; }}
#Muted {{ color: {c['muted']}; }}
#Small {{ color: {c['muted']}; font-size: 11px; }}
#Warn {{ color: {c['warning']}; }}
#Error {{ color: {c['danger']}; }}
#Success {{ color: {c['success']}; }}
#Chip {{ background: {c['panel']}; color: {c['muted']}; border-radius: 9px; padding: 1px 8px; font-size: 11px; }}
#ChipAccent {{ background: {c['selection']}; color: {c['text']}; border-radius: 9px; padding: 1px 8px; font-size: 11px; }}
QPushButton {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 12px;
    color: {c['text']}; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['panel']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['panel']}; border-color: {c['panel']}; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 6px; }}
QPushButton#Primary {{ background: {c['accent']}; color: {c['accent_text']}; border: none; font-weight: 700;
    padding: 10px 14px; font-size: 14px; border-radius: 10px; }}
QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#Primary:disabled {{ background: {c['border']}; color: {c['muted']}; }}
QPushButton#Danger {{ color: {c['danger']}; }}
QPushButton#Link {{ border: none; background: transparent; color: {c['accent']}; padding: 2px 4px; text-align: left; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QToolButton {{ color: {c['text']}; }}
QToolButton#Flat {{ border: none; border-radius: 7px; padding: 5px; background: transparent; }}
QToolButton#Flat:hover {{ background: {c['panel']}; }}
QToolButton#Flat:checked {{ background: {c['selection']}; }}
QToolButton#Flat:disabled {{ background: transparent; }}
QToolButton#SectionHeader {{ border: none; background: transparent; font-weight: 700; padding: 4px 0px;
    text-align: left; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{ background: {c['base']}; border: 1px solid {c['border']};
    border-radius: 7px; padding: 5px 8px; color: {c['text']}; selection-background-color: {c['selection']};
    selection-color: {c['text']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit#Address {{ border-radius: 15px; padding: 5px 14px; background: {c['base']}; }}
QSpinBox {{ padding-right: 24px; }}
QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none;
    background: transparent; }}
QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: none;
    background: transparent; }}
QSpinBox::up-arrow {{ image: url("{self._arrows.get('up', '')}"); width: 11px; height: 11px; }}
QSpinBox::down-arrow {{ image: url("{self._arrows.get('down', '')}"); width: 11px; height: 11px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("{self._arrows.get('down', '')}"); width: 11px; height: 11px; }}
QComboBox QAbstractItemView {{ background: {c['base']}; border: 1px solid {c['border']};
    selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QProgressBar {{ background: {c['panel']}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QProgressBar#Thin {{ max-height: 2px; min-height: 2px; border-radius: 0px; background: transparent; }}
QProgressBar#Thin::chunk {{ border-radius: 0px; }}
QStatusBar {{ background: {c['rail']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QToolButton {{ border: none; padding: 2px 8px; color: {c['muted']}; border-radius: 5px; }}
QStatusBar QToolButton:hover {{ color: {c['text']}; background: {c['panel']}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QTableWidget, QListWidget, QTreeWidget {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 10px;
    alternate-background-color: {c['alt']}; gridline-color: {c['border']}; selection-background-color: {c['selection']};
    selection-color: {c['text']}; outline: 0; }}
QHeaderView::section {{ background: {c['panel']}; border: none; border-bottom: 1px solid {c['border']};
    padding: 6px 8px; color: {c['muted']}; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['panel']}; border: none; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 12px; margin-top: 22px; padding: 14px 12px 12px 12px;
    background: {c['base']}; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 2px; color: {c['text']}; font-size: 14px; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QToolTip {{ background: {c['base']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 5px; }}
QMenu {{ background: {c['base']}; border: 1px solid {c['border']}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
QSplitter::handle {{ background: {c['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
#Toast {{ background: {c['toast']}; border-radius: 10px; }}
#Toast QLabel {{ color: {c['toast_text']}; }}
#Toast QPushButton {{ background: transparent; border: 1px solid {c['toast_text']}; color: {c['toast_text']};
    padding: 3px 10px; border-radius: 7px; }}
#Toast QToolButton {{ border: none; background: transparent; }}
#TaskRow {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 12px; }}
#Thumb {{ background: {c['panel']}; border-radius: 8px; }}
#EmptyState QLabel {{ color: {c['muted']}; }}
"""


THEME = _Theme()


def human_size(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def app_icon() -> QIcon:
    """Kura's icon: a white-walled storehouse with a namako-kabe lattice."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 100.0
        indigo, ink, plaster = QColor("#36568C"), QColor("#1C2B47"), QColor("#F6F2E9")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(indigo)
        p.drawRoundedRect(QRectF(3 * s, 3 * s, 94 * s, 94 * s), 22 * s, 22 * s)
        # roof (gable with eaves)
        p.setBrush(ink)
        p.drawPolygon(QPolygonF([QPointF(14 * s, 42 * s), QPointF(50 * s, 18 * s), QPointF(86 * s, 42 * s),
                                 QPointF(80 * s, 45 * s), QPointF(20 * s, 45 * s)]))
        # plaster wall
        p.setBrush(plaster)
        p.drawRect(QRectF(24 * s, 44 * s, 52 * s, 38 * s))
        # namako-kabe (lower wall): dark tiles with a white diagonal lattice
        p.setBrush(ink)
        tiles = QRectF(24 * s, 64 * s, 52 * s, 18 * s)
        p.drawRect(tiles)
        if size >= 32:
            pen = QPen(plaster)
            pen.setWidthF(max(1.0, 1.5 * s))
            p.setPen(pen)
            p.setClipRect(tiles)
            step = 9 * s
            x = 24 * s - 18 * s
            while x < 76 * s + 18 * s:
                p.drawLine(QPointF(x, 64 * s), QPointF(x + 18 * s, 82 * s))
                p.drawLine(QPointF(x + 18 * s, 64 * s), QPointF(x, 82 * s))
                x += step
            p.setClipping(False)
            p.setPen(Qt.PenStyle.NoPen)
        # small window
        p.setBrush(ink)
        p.drawRect(QRectF(43 * s, 49 * s, 14 * s, 9 * s))
        p.end()
        icon.addPixmap(pm)
    return icon
