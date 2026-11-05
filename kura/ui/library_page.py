"""Library page: everything Kura has downloaded."""

from __future__ import annotations

import datetime as _dt
import json
import os

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, paths
from ..core import citation, metadata
from .context import AppContext
from .dialogs import confirm, copy_text
from .theme import THEME, human_duration, human_size
from .widgets import ElidedLabel, EmptyState, ThumbLabel, label

tr = i18n.tr


def _fmt_date(yyyymmdd: str | None) -> str:
    s = str(yyyymmdd or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else ""


def _fmt_saved(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return _dt.datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16]


class LibraryPage(QWidget):
    redownload = pyqtSignal(str, str, str)   # url, preset id, title

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.rows: list[dict] = []
        self._icon_cache: dict[str, QIcon] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 12)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label(tr("lib.title"), "Title"))
        self.count = label("", "Muted")
        head.addWidget(self.count)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("lib.search_ph"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(260)
        self.search.addAction(THEME.icon("ph.magnifying-glass", "muted"), QLineEdit.ActionPosition.LeadingPosition)
        head.addWidget(self.search)
        self.kind = QComboBox()
        for k in ("all", "video", "audio"):
            self.kind.addItem(tr(f"lib.kind_{k}"), k)
        head.addWidget(self.kind)
        self.sort = QComboBox()
        for k in ("new", "old", "published", "title", "size"):
            self.sort.addItem(tr(f"lib.sort_{k}"), k)
        self.sort.setCurrentIndex(max(self.sort.findData(ctx.settings.get("library_sort", "new")), 0))
        head.addWidget(self.sort)
        exp = QPushButton(tr("lib.export"))
        THEME.bind_icon(exp, "ph.export")
        menu = QMenu(exp)
        menu.addAction(tr("lib.export_csv")).triggered.connect(self._export_csv)
        menu.addAction(tr("lib.export_json")).triggered.connect(self._export_json)
        exp.setMenu(menu)
        head.addWidget(exp)
        lay.addLayout(head)

        self.stack = QStackedWidget()
        self.empty = EmptyState("ph.books", tr("lib.title"), tr("lib.empty"))
        self.stack.addWidget(self.empty)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(10)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([tr("lib.col_title"), tr("lib.col_channel"), tr("lib.col_preset"),
                                              tr("lib.col_published"), tr("lib.col_saved"), tr("lib.col_size")])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.setIconSize(QSize(72, 40))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self._open())
        split.addWidget(self.table)
        self.detail = self._build_detail()
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([800, 340])
        self.stack.addWidget(split)
        lay.addWidget(self.stack, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda *_: self._timer.start())
        self.kind.currentIndexChanged.connect(lambda *_: self.reload())
        self.sort.currentIndexChanged.connect(self._sort_changed)
        ctx.library_changed.connect(self.reload)
        self.reload()

    # -- detail pane ---------------------------------------------------------------------------
    def _build_detail(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(330)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        w = QFrame()
        w.setObjectName("Card")
        scroll.setWidget(w)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)
        self.d_thumb = ThumbLabel(288, 162)
        lay.addWidget(self.d_thumb)
        self.d_title = label(tr("lib.select_hint"), "VideoTitle", wrap=True, selectable=True)
        lay.addWidget(self.d_title)
        self.d_missing = label(tr("lib.missing"), "Warn", wrap=True)
        self.d_missing.setVisible(False)
        lay.addWidget(self.d_missing)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.d_fields: dict[str, QLabel] = {}
        for i, key in enumerate(("channel", "published", "saved", "duration", "quality", "preset", "views",
                                 "file", "extra")):
            k = label(tr(f"lib.field_{key}"), "Small")
            grid.addWidget(k, i, 0, Qt.AlignmentFlag.AlignTop)
            v = ElidedLabel("") if key == "file" else label("", wrap=True, selectable=True)
            grid.addWidget(v, i, 1)
            self.d_fields[key] = v
        lay.addLayout(grid)
        btns = QGridLayout()
        self.b_open = QPushButton(tr("lib.open"))
        THEME.bind_icon(self.b_open, "ph.play")
        self.b_open.clicked.connect(self._open)
        self.b_reveal = QPushButton(tr("lib.reveal"))
        THEME.bind_icon(self.b_reveal, "ph.folder-open")
        self.b_reveal.clicked.connect(self._reveal)
        self.b_redl = QPushButton(tr("lib.redownload"))
        THEME.bind_icon(self.b_redl, "ph.arrow-counter-clockwise")
        self.redl_menu = QMenu(self.b_redl)
        self.b_redl.setMenu(self.redl_menu)
        self.redl_menu.aboutToShow.connect(self._fill_redl_menu)
        self.b_url = QPushButton(tr("lib.copy_url"))
        THEME.bind_icon(self.b_url, "ph.link")
        self.b_url.clicked.connect(self._copy_url)
        self.b_cite = QPushButton(tr("lib.copy_citation"))
        THEME.bind_icon(self.b_cite, "ph.quotes")
        cm = QMenu(self.b_cite)
        for style in citation.STYLES:
            cm.addAction(tr(f"set.cite_{style}")).triggered.connect(lambda _=False, s=style: self._copy_cite(s))
        self.b_cite.setMenu(cm)
        self.b_del = QPushButton(tr("common.delete"))
        self.b_del.setObjectName("Danger")
        THEME.bind_icon(self.b_del, "ph.trash", "danger")
        dm = QMenu(self.b_del)
        dm.addAction(tr("lib.remove")).triggered.connect(self._remove)
        dm.addAction(tr("lib.remove_file")).triggered.connect(self._remove_with_files)
        self.b_del.setMenu(dm)
        for i, b in enumerate((self.b_open, self.b_reveal, self.b_redl, self.b_cite, self.b_url, self.b_del)):
            b.setStyleSheet("text-align: left; padding-left: 10px;")
            btns.addWidget(b, i, 0)
        lay.addLayout(btns)
        lay.addStretch(1)
        self._set_detail_enabled(False)
        return scroll

    def _set_detail_enabled(self, on: bool) -> None:
        for b in (self.b_open, self.b_reveal, self.b_redl, self.b_url, self.b_cite, self.b_del):
            b.setEnabled(on)

    # -- data -------------------------------------------------------------------------------------
    def _sort_changed(self) -> None:
        self.ctx.set("library_sort", self.sort.currentData())
        self.reload()

    def reload(self) -> None:
        current = self.selected()
        cur_id = current["id"] if current else None
        self.rows = self.ctx.db.search(self.search.text().strip(), self.kind.currentData(), self.sort.currentData())
        total = self.ctx.db.count()
        self.count.setText(tr("lib.count", n=total))
        self.stack.setCurrentIndex(1 if total else 0)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            title = QTableWidgetItem(row.get("title") or row.get("url") or "")
            title.setIcon(self._thumb_icon(row))
            title.setToolTip(row.get("title") or "")
            if row.get("file_path") and not os.path.exists(row["file_path"]):
                title.setForeground(THEME.qcolor("muted"))
            self.table.setItem(r, 0, title)
            self.table.setItem(r, 1, QTableWidgetItem(row.get("channel") or ""))
            self.table.setItem(r, 2, QTableWidgetItem(row.get("preset_name") or row.get("preset_id") or ""))
            self.table.setItem(r, 3, QTableWidgetItem(_fmt_date(row.get("upload_date"))))
            self.table.setItem(r, 4, QTableWidgetItem(_fmt_saved(row.get("downloaded_at"))))
            size = QTableWidgetItem(human_size(row.get("file_size")) if row.get("file_size") else "")
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 5, size)
        if cur_id is not None:
            for r, row in enumerate(self.rows):
                if row["id"] == cur_id:
                    self.table.selectRow(r)
                    break
        if not self.selected():
            self._show_detail()

    def _thumb_icon(self, row: dict) -> QIcon:
        path = row.get("thumb_path")
        if path and path in self._icon_cache:
            return self._icon_cache[path]
        if path and os.path.isfile(path):
            pm = QPixmap(path)
            if not pm.isNull():
                pm = pm.scaled(144, 80, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
                x, y = (pm.width() - 144) // 2, (pm.height() - 80) // 2
                ico = QIcon(pm.copy(max(x, 0), max(y, 0), 144, 80))
                self._icon_cache[path] = ico
                return ico
        return THEME.icon("ph.music-notes" if row.get("kind") == "audio" else "ph.film-strip", "muted")

    def selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        r = rows[0].row()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def _show_detail(self) -> None:
        row = self.selected()
        self._set_detail_enabled(row is not None)
        if not row:
            self.d_thumb.clear_thumb("ph.books")
            self.d_title.setText(tr("lib.select_hint"))
            self.d_missing.setVisible(False)
            for v in self.d_fields.values():
                v.setText("")
            return
        if not self.d_thumb.set_image_file(row.get("thumb_path")):
            self.d_thumb.clear_thumb("ph.music-notes" if row.get("kind") == "audio" else "ph.film-strip")
        self.d_title.setText(row.get("title") or "")
        exists = bool(row.get("file_path")) and os.path.exists(row["file_path"])
        self.d_missing.setVisible(not exists)
        self.b_open.setEnabled(exists)
        q = []
        if row.get("height"):
            q.append(f"{min(row.get('height') or 0, row.get('width') or 99999)}p")
        q += [x for x in (row.get("vcodec"), row.get("acodec")) if x]
        if row.get("fps"):
            q.append(f"{row['fps']:.0f}fps")
        if row.get("encoder") and row["encoder"] != "remux":
            q.append(f"({row['encoder']})")
        extra = []
        try:
            extra = json.loads(row.get("extra_files") or "[]")
        except ValueError:
            pass
        values = {
            "channel": row.get("channel") or "",
            "published": _fmt_date(row.get("upload_date")),
            "saved": _fmt_saved(row.get("downloaded_at")),
            "duration": human_duration(row.get("duration")) if row.get("duration") else "",
            "quality": " ".join(q) + (f" · {human_size(row['file_size'])}" if row.get("file_size") else ""),
            "preset": row.get("preset_name") or "",
            "views": f"{row['view_count']:,}" if row.get("view_count") else "",
            "file": row.get("file_path") or "",
            "extra": "\n".join(os.path.basename(x) for x in extra),
        }
        for k, v in values.items():
            self.d_fields[k].setText(v)

    # -- actions --------------------------------------------------------------------------------
    def _open(self) -> None:
        row = self.selected()
        if row and row.get("file_path") and os.path.exists(row["file_path"]):
            paths.open_path(row["file_path"])

    def _reveal(self) -> None:
        row = self.selected()
        if row and row.get("file_path"):
            paths.reveal_in_file_manager(row["file_path"])

    def _fill_redl_menu(self) -> None:
        self.redl_menu.clear()
        row = self.selected()
        if not row:
            return
        for p in self.ctx.presets():
            act = self.redl_menu.addAction(self.ctx.preset_name(p))
            act.triggered.connect(lambda _=False, pid=p.id: self.redownload.emit(row.get("url") or "", pid,
                                                                               row.get("title") or ""))

    def _copy_url(self) -> None:
        row = self.selected()
        if row and row.get("url"):
            copy_text(row["url"])
            self.ctx.notify(tr("common.copied"), "success", timeout=2000)

    def _meta(self, row: dict) -> dict:
        try:
            meta = json.loads(row.get("meta_json") or "{}")
        except ValueError:
            meta = {}
        for k in ("title", "channel", "upload_date", "duration"):
            meta.setdefault(k, row.get(k))
        meta.setdefault("webpage_url", row.get("url"))
        meta.setdefault("extractor_key", row.get("extractor"))
        return meta

    def _copy_cite(self, style: str) -> None:
        row = self.selected()
        if row:
            copy_text(citation.cite(self._meta(row), style))
            self.ctx.notify(tr("common.copied"), "success", timeout=2000)

    def _remove(self) -> None:
        row = self.selected()
        if row:
            self.ctx.db.delete_item(row["id"])
            self.reload()

    def _remove_with_files(self) -> None:
        row = self.selected()
        if not row:
            return
        files = [row.get("file_path")]
        try:
            files += json.loads(row.get("extra_files") or "[]")
        except ValueError:
            pass
        files = [f for f in files if f and os.path.exists(f)]
        if files and not confirm(self, tr("lib.remove_file"), tr("lib.remove_file_confirm", files="\n".join(files))):
            return
        for f in files:
            try:
                os.remove(f)
            except OSError as e:
                QMessageBox.warning(self, tr("lib.remove_file"), str(e))
        self._remove()

    def _export_csv(self) -> None:
        default = os.path.join(self.ctx.settings.save_dir(), "kura-library.csv")
        path, _ = QFileDialog.getSaveFileName(self, tr("lib.export_csv"), default, "CSV (*.csv)")
        if path:
            n = metadata.write_library_csv(self.rows or self.ctx.db.all_items(), path)
            self.ctx.notify(tr("lib.exported", n=n), "success")

    def _export_json(self) -> None:
        row = self.selected()
        if not row:
            return
        base = os.path.splitext(row.get("file_path") or "metadata")[0]
        path, _ = QFileDialog.getSaveFileName(self, tr("lib.export_json"), base + ".info.json", "JSON (*.json)")
        if path:
            metadata.write_json(self._meta(row), path)
            self.ctx.notify(tr("lib.exported", n=1), "success")
