"""Dialogs: add URLs, playlist picker, format picker, preset editor, ffmpeg
setup, task details/errors and the first-run welcome."""

from __future__ import annotations

import os
import re

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, i18n, paths
from ..config import import_v2_settings
from ..core import errors, tools, urls
from ..core.presets import Preset, SubtitleOptions, new_custom_preset
from .context import AppContext
from .theme import THEME, human_duration, human_size
from .widgets import ElidedLabel, IconLabel, label
from .workers import FuncThread, InfoThread

tr = i18n.tr


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def parse_time(text: str) -> float | None:
    """'83' / '1:23' / '01:02:03' / '1:02:03.5' -> seconds."""
    text = (text or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", text):
        raise ValueError(text)
    parts = text.split(":")
    secs = float(parts[-1])
    mult = 60
    for p in reversed(parts[:-1]):
        secs += int(p) * mult
        mult *= 60
    return secs


def fill_preset_combo(combo: QComboBox, ctx: AppContext, selected: str | None = None) -> None:
    combo.blockSignals(True)
    combo.clear()
    presets = ctx.presets()
    groups = [("preset.group_video", [p for p in presets if p.builtin and not p.is_audio]),
              ("preset.group_audio", [p for p in presets if p.builtin and p.is_audio]),
              ("preset.group_custom", [p for p in presets if not p.builtin])]
    first = True
    for key, items in groups:
        if not items:
            continue
        if not first:
            combo.insertSeparator(combo.count())
        first = False
        for p in items:
            icon = THEME.icon("ph.music-notes" if p.is_audio else "ph.film-strip", "muted")
            combo.addItem(icon, ctx.preset_name(p), p.id)
    idx = combo.findData(selected or ctx.settings.get("default_preset"))
    combo.setCurrentIndex(max(idx, 0))
    combo.blockSignals(False)


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def _dialog_buttons(ok_text: str | None = None) -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text or tr("common.ok"))
    bb.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
    bb.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("common.cancel"))
    return bb


# --------------------------------------------------------------------------
# Add URLs
# --------------------------------------------------------------------------

class AddUrlsDialog(QDialog):
    def __init__(self, ctx: AppContext, initial_text: str = "", parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(tr("add.title"))
        self.resize(620, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(label(tr("add.hint"), "Muted", wrap=True))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("https://www.youtube.com/watch?v=…")
        self.edit.setPlainText(initial_text)
        lay.addWidget(self.edit, 1)
        self.count = label("", "Small")
        lay.addWidget(self.count)
        form = QFormLayout()
        self.preset = QComboBox()
        fill_preset_combo(self.preset, ctx)
        form.addRow(tr("panel.preset"), self.preset)
        sub = ctx.settings.get("subtitles")
        self.subs = QCheckBox(tr("panel.sub_enable") + f"（{sub.get('langs', 'ja,en')}）")
        self.subs.setChecked(bool(sub.get("enabled")))
        form.addRow("", self.subs)
        self.expand = QCheckBox(tr("add.expand_lists"))
        self.expand.setChecked(True)
        form.addRow("", self.expand)
        lay.addLayout(form)
        bb = _dialog_buttons(tr("add.add"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.ok_button = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.edit.textChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        n = len(urls.extract_urls(self.edit.toPlainText()))
        self.count.setText(tr("add.found", n=n))
        self.ok_button.setEnabled(n > 0)

    def result_data(self) -> dict:
        found = urls.extract_urls(self.edit.toPlainText())
        singles, collections = [], []
        for u in found:
            c = urls.classify(u)
            if self.expand.isChecked() and c.kind in ("playlist", "channel"):
                collections.append(u)
            else:
                singles.append(u)
        sub_opts = SubtitleOptions.from_settings(self.ctx.settings.get("subtitles")) if self.subs.isChecked() else None
        return {"singles": singles, "collections": collections, "preset_id": self.preset.currentData(),
                "subtitles": sub_opts}


# --------------------------------------------------------------------------
# Playlist / channel picker
# --------------------------------------------------------------------------

class PlaylistDialog(QDialog):
    def __init__(self, ctx: AppContext, queue, url: str, preset_id: str | None = None,
                 subtitles: SubtitleOptions | None = None, parent=None):
        super().__init__(parent)
        self.ctx, self.queue = ctx, queue
        self.subtitles = subtitles
        c = urls.classify(url)
        if c.kind == "channel":
            url = urls.channel_videos_url(url)
        elif c.kind == "video" and c.has_list:
            url = urls.playlist_url(c.list_id)
        self.url = url
        self.entries: list[dict] = []
        self.list_title = ""
        self.downloaded: set[str] = set()
        self.setWindowTitle(tr("pl.title"))
        self.resize(820, 620)
        lay = QVBoxLayout(self)
        self.head = label(tr("pl.loading"), "H2", wrap=True)
        lay.addWidget(self.head)
        self.sub = label(url, "Small", selectable=True)
        lay.addWidget(self.sub)

        tools_row = QHBoxLayout()
        b_all = QPushButton(tr("common.select_all"))
        b_none = QPushButton(tr("common.select_none"))
        b_all.clicked.connect(lambda: self._select(lambda r, e: True))
        b_none.clicked.connect(lambda: self._select(lambda r, e: False))
        tools_row.addWidget(b_all)
        tools_row.addWidget(b_none)
        tools_row.addSpacing(16)
        tools_row.addWidget(QLabel(tr("pl.latest")))
        self.latest = QSpinBox()
        self.latest.setRange(1, 5000)
        self.latest.setValue(20)
        tools_row.addWidget(self.latest)
        b_latest = QPushButton(tr("pl.latest_n"))
        b_latest.clicked.connect(lambda: self._select(lambda r, e: r < self.latest.value()))
        tools_row.addWidget(b_latest)
        tools_row.addStretch(1)
        lay.addLayout(tools_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", tr("pl.col_title"), tr("pl.col_duration"), tr("pl.col_status")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(lambda *_: self._update_count())
        lay.addWidget(self.table, 1)

        opts = QFormLayout()
        self.skip = QCheckBox(tr("pl.skip_downloaded", n=0))
        self.skip.setChecked(bool(ctx.settings.get("skip_downloaded", True)))
        self.skip.toggled.connect(self._apply_skip)
        opts.addRow("", self.skip)
        self.subfolder = QCheckBox(tr("pl.subfolder", name="…"))
        self.subfolder.setChecked(bool(ctx.settings.get("playlist_subfolder", True)))
        opts.addRow("", self.subfolder)
        self.preset = QComboBox()
        fill_preset_combo(self.preset, ctx, preset_id)
        self.preset.currentIndexChanged.connect(lambda *_: self._recheck_downloaded())
        opts.addRow(tr("panel.preset"), self.preset)
        lay.addLayout(opts)
        self.count = label("", "Muted")
        lay.addWidget(self.count)
        bb = _dialog_buttons(tr("pl.add"))
        bb.accepted.connect(self._add)
        bb.rejected.connect(self.reject)
        self.ok_button = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setEnabled(False)
        lay.addWidget(bb)

        self.thread = InfoThread(1, url, ctx.tool_config(), ctx.auth_config(), flat=True, noplaylist=False,
                                 playlist_end=2000, want_thumb=False, parent=self)
        self.thread.done.connect(self._loaded)
        self.thread.failed.connect(self._failed)
        self.thread.start()

    def _failed(self, _rid: int, msg: str, kind: str) -> None:
        self.head.setText(tr("pl.failed"))
        self.head.setObjectName("Error")
        self.sub.setText(f"{tr(f'err.{kind}.title')}\n{msg}")

    def _loaded(self, _rid: int, info: dict, _thumb: bytes) -> None:
        self.list_title = info.get("title") or info.get("playlist_title") or ""
        entries = []

        def walk(i: dict) -> None:
            for e in i.get("entries") or []:
                if not e:
                    continue
                if e.get("_type") == "playlist" or (e.get("entries") is not None):
                    walk(e)
                    continue
                url = e.get("url") or e.get("webpage_url")
                vid = e.get("id")
                if url and not url.startswith("http") and vid:
                    url = urls.canonical_video_url(vid)
                if url:
                    entries.append({"url": url, "id": vid, "title": e.get("title") or url,
                                    "duration": e.get("duration"), "thumb": _entry_thumb(e)})

        walk(info)
        self.entries = entries
        self.head.setText(self.list_title or tr("pl.title"))
        self.subfolder.setText(tr("pl.subfolder", name=self.list_title or "playlist"))
        self.table.blockSignals(True)
        self.table.setRowCount(len(entries))
        for r, e in enumerate(entries):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(f"{r + 1:>3}. {e['title']}"))
            self.table.setItem(r, 2, QTableWidgetItem(human_duration(e["duration"]) if e["duration"] else ""))
            self.table.setItem(r, 3, QTableWidgetItem(""))
        self.table.blockSignals(False)
        self._recheck_downloaded()
        self.ok_button.setEnabled(bool(entries))

    def _recheck_downloaded(self) -> None:
        pid = self.preset.currentData()
        ids = [e["id"] for e in self.entries if e.get("id")]
        self.downloaded = self.ctx.db.downloaded_ids(ids, pid)
        self.table.blockSignals(True)
        for r, e in enumerate(self.entries):
            it = self.table.item(r, 3)
            if it:
                it.setText(tr("pl.downloaded") if e.get("id") in self.downloaded else "")
        self.table.blockSignals(False)
        self.skip.setText(tr("pl.skip_downloaded", n=len(self.downloaded)))
        self._apply_skip()

    def _apply_skip(self) -> None:
        if self.skip.isChecked():
            self._select(lambda r, e: e.get("id") not in self.downloaded, only_downloaded=True)
        self._update_count()

    def _select(self, pred, only_downloaded: bool = False) -> None:
        self.table.blockSignals(True)
        for r, e in enumerate(self.entries):
            if only_downloaded and e.get("id") not in self.downloaded:
                continue
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Checked if pred(r, e) else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_count()

    def _selected(self) -> list[tuple[int, dict]]:
        out = []
        for r, e in enumerate(self.entries):
            it = self.table.item(r, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                out.append((r, e))
        return out

    def _update_count(self) -> None:
        sel = len(self._selected())
        self.count.setText(tr("pl.count", n=len(self.entries), sel=sel))
        self.ok_button.setEnabled(sel > 0)

    def _add(self) -> None:
        preset = self.ctx.preset(self.preset.currentData())
        sub = self.list_title if self.subfolder.isChecked() else ""
        n = 0
        for r, e in self._selected():
            self.queue.add(e["url"], preset, title=e["title"], video_id=e.get("id") or "", thumb_url=e.get("thumb"),
                           subtitles=self.subtitles, subfolder=sub,
                           literals={"playlist": self.list_title, "index": f"{r + 1:03d}"})
            n += 1
        self.ctx.notify(tr("queue.added_n", n=n), "success")
        self.accept()


def _entry_thumb(e: dict) -> str | None:
    thumbs = e.get("thumbnails") or []
    for t in reversed(thumbs):
        u = t.get("url") or ""
        if u.split("?")[0].endswith(".jpg"):
            return u
    if e.get("id") and re.fullmatch(r"[0-9A-Za-z_-]{11}", str(e.get("id"))):
        return f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg"
    return thumbs[-1]["url"] if thumbs else None


# --------------------------------------------------------------------------
# Format picker
# --------------------------------------------------------------------------

class FormatPickerDialog(QDialog):
    def __init__(self, ctx: AppContext, info: dict, parent=None):
        super().__init__(parent)
        from ..core.engine import format_rows

        self.ctx = ctx
        self.rows = format_rows(info)
        self.setWindowTitle(tr("fmt.title"))
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(label(tr("fmt.hint"), "Muted", wrap=True))
        cols = ["fmt.col_id", "fmt.col_kind", "fmt.col_res", "fmt.col_fps", "fmt.col_hdr", "fmt.col_vcodec",
                "fmt.col_acodec", "fmt.col_bitrate", "fmt.col_size", "fmt.col_note"]
        self.table = QTableWidget(len(self.rows), len(cols))
        self.table.setHorizontalHeaderLabels([tr(c) for c in cols])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        kinds = {"av": tr("fmt.kind_av"), "v": tr("fmt.kind_v"), "a": tr("fmt.kind_a"), "?": "?"}
        for r, f in enumerate(self.rows):
            res = f"{f['width']}×{f['height']}" if f.get("height") else ""
            br = f"{f['tbr']:.0f}k" if f.get("tbr") else ""
            vals = [f["id"], kinds[f["kind"]], res, f"{f['fps']:.0f}" if f.get("fps") else "", f.get("hdr") or "",
                    f.get("vcodec") or "", f.get("acodec") or "", br, human_size(f["size"]) if f.get("size") else "",
                    " ".join(x for x in (f.get("note"), f.get("lang"), f.get("ext")) if x)]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._update)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("fmt.container")))
        self.container = QComboBox()
        self.container.addItem("MKV", "mkv")
        self.container.addItem("MP4", "mp4")
        row.addWidget(self.container)
        row.addStretch(1)
        self.sel_label = label("", "Muted")
        row.addWidget(self.sel_label)
        lay.addLayout(row)
        bb = _dialog_buttons()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.ok_button = bb.button(QDialogButtonBox.StandardButton.Ok)
        lay.addWidget(bb)
        self._update()

    def selection(self) -> tuple[str | None, str]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        chosen = [self.rows[r] for r in rows]
        kinds = sorted(f["kind"] for f in chosen)
        fmt = None
        if len(chosen) == 1:
            fmt = chosen[0]["id"]
        elif len(chosen) == 2 and kinds == ["a", "v"]:
            v = next(f for f in chosen if f["kind"] == "v")
            a = next(f for f in chosen if f["kind"] == "a")
            fmt = f"{v['id']}+{a['id']}"
        return fmt, self.container.currentData()

    def _update(self) -> None:
        fmt, _ = self.selection()
        self.sel_label.setText(tr("fmt.selection", sel=fmt) if fmt else tr("fmt.invalid"))
        self.ok_button.setEnabled(bool(fmt))


# --------------------------------------------------------------------------
# Preset editor
# --------------------------------------------------------------------------

class PresetEditorDialog(QDialog):
    def __init__(self, ctx: AppContext, preset: Preset, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.preset = Preset.from_dict(preset.to_dict())
        self.setWindowTitle(tr("preset.editor_title"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self.preset.name or tr("preset.new_name"))
        form.addRow(tr("preset.name"), self.name)
        self.kind = QComboBox()
        self.kind.addItem(tr("preset.kind_video"), "video")
        self.kind.addItem(tr("preset.kind_audio"), "audio")
        self.kind.setCurrentIndex(1 if self.preset.is_audio else 0)
        form.addRow(tr("preset.kind"), self.kind)
        # video
        self.res = QComboBox()
        self.res.addItem(tr("preset.res_any"), 0)
        for r in (2160, 1440, 1080, 720, 480, 360):
            self.res.addItem(f"{r}p", r)
        self.res.setCurrentIndex(max(self.res.findData(self.preset.max_res or 0), 0))
        form.addRow(tr("preset.max_res"), self.res)
        self.fps = QComboBox()
        self.fps.addItem(tr("preset.fps_any"), 0)
        self.fps.addItem("60", 60)
        self.fps.addItem("30", 30)
        self.fps.setCurrentIndex(max(self.fps.findData(self.preset.fps_max or 0), 0))
        form.addRow(tr("preset.fps_max"), self.fps)
        self.vcodec = QComboBox()
        self.vcodec.addItem(tr("preset.vcodec_copy"), "copy")
        self.vcodec.addItem("H.264", "h264")
        self.vcodec.addItem("HEVC (H.265)", "hevc")
        self.vcodec.setCurrentIndex(max(self.vcodec.findData(self.preset.vcodec), 0))
        form.addRow(tr("preset.vcodec"), self.vcodec)
        self.vacodec = QComboBox()
        self.vacodec.addItem(tr("preset.acodec_copy"), "copy")
        self.vacodec.addItem("AAC", "aac")
        self.vacodec.setCurrentIndex(max(self.vacodec.findData(self.preset.acodec if not self.preset.is_audio else "copy"), 0))
        form.addRow(tr("preset.acodec"), self.vacodec)
        self.container = QComboBox()
        self.container.addItem("MP4", "mp4")
        self.container.addItem("MKV", "mkv")
        self.container.setCurrentIndex(max(self.container.findData(self.preset.container), 0))
        form.addRow(tr("preset.container"), self.container)
        self.sdr = QCheckBox(tr("preset.prefer_sdr"))
        self.sdr.setChecked(self.preset.prefer_sdr)
        form.addRow("", self.sdr)
        # audio
        self.afmt = QComboBox()
        for a in ("m4a", "mp3", "wav", "opus", "flac"):
            self.afmt.addItem(a.upper(), a)
        self.afmt.setCurrentIndex(max(self.afmt.findData(self.preset.acodec if self.preset.is_audio else "m4a"), 0))
        form.addRow(tr("preset.audio_format"), self.afmt)
        self.aq = QComboBox()
        for q in ("320", "256", "192", "160", "128"):
            self.aq.addItem(q, q)
        self.aq.setCurrentIndex(max(self.aq.findData(self.preset.audio_quality), 0))
        form.addRow(tr("preset.audio_quality"), self.aq)
        self.thumb = QCheckBox(tr("preset.embed_thumb"))
        self.thumb.setChecked(self.preset.embed_thumbnail)
        self.meta = QCheckBox(tr("preset.embed_meta"))
        self.meta.setChecked(self.preset.embed_metadata)
        self.chap = QCheckBox(tr("preset.embed_chapters"))
        self.chap.setChecked(self.preset.embed_chapters)
        for w in (self.thumb, self.meta, self.chap):
            form.addRow("", w)
        self.tag = QLineEdit(self.preset.tag or "")
        form.addRow(tr("preset.tag"), self.tag)
        lay.addLayout(form)
        self._form = form
        bb = _dialog_buttons(tr("common.save"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self) -> None:
        audio = self.kind.currentData() == "audio"
        for w in (self.res, self.fps, self.vcodec, self.vacodec, self.container, self.sdr):
            w.setVisible(not audio)
            lb = self._form.labelForField(w)
            if lb:
                lb.setVisible(not audio)
        for w in (self.afmt, self.aq):
            w.setVisible(audio)
            lb = self._form.labelForField(w)
            if lb:
                lb.setVisible(audio)

    def result_preset(self) -> Preset:
        p = self.preset
        p.name = self.name.text().strip() or tr("preset.new_name")
        p.kind = self.kind.currentData()
        if p.kind == "audio":
            p.acodec = self.afmt.currentData()
            p.container = p.acodec
            p.audio_quality = self.aq.currentData()
            p.max_res = None
            p.vcodec = "copy"
        else:
            p.max_res = self.res.currentData() or None
            p.fps_max = self.fps.currentData() or None
            p.vcodec = self.vcodec.currentData()
            p.acodec = self.vacodec.currentData()
            p.container = self.container.currentData()
            p.prefer_sdr = self.sdr.isChecked()
            p.prefer_h264_source = p.vcodec == "h264"
        p.embed_thumbnail = self.thumb.isChecked()
        p.embed_metadata = self.meta.isChecked()
        p.embed_chapters = self.chap.isChecked()
        p.tag = re.sub(r"[^\w\-.]", "", self.tag.text().strip()) or "custom"
        p.builtin = False
        return p


def edit_custom_preset(ctx: AppContext, preset: Preset | None, parent=None) -> Preset | None:
    base = preset if preset is not None else new_custom_preset(ctx.preset("edit1080"), tr("preset.new_name"))
    if base.builtin:
        base = new_custom_preset(base, ctx.preset_name(base) + " (custom)")
    dlg = PresetEditorDialog(ctx, base, parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    p = dlg.result_preset()
    customs = [c for c in ctx.settings.get("custom_presets", []) if c.get("id") != p.id]
    customs.append(p.to_dict())
    ctx.set("custom_presets", customs)
    ctx.presets_changed.emit()
    return p


# --------------------------------------------------------------------------
# ffmpeg setup
# --------------------------------------------------------------------------

class FfmpegSetupDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.thread: FuncThread | None = None
        self.setWindowTitle(tr("ff.title"))
        self.resize(620, 440)
        lay = QVBoxLayout(self)
        lay.addWidget(label(tr("ff.why"), "Muted", wrap=True))
        self.status = label("", "H3", wrap=True)
        lay.addWidget(self.status)
        btns = QHBoxLayout()
        if paths.IS_WINDOWS:
            b = QPushButton(tr("ff.download_win"))
            b.setObjectName("Primary")
            b.clicked.connect(self._download_win)
            btns.addWidget(b)
        elif paths.IS_MAC:
            b = QPushButton(tr("ff.brew"))
            b.setObjectName("Primary")
            b.setEnabled(bool(tools.brew_path()))
            b.clicked.connect(self._brew)
            btns.addWidget(b)
            if not tools.brew_path():
                lay.addWidget(label(tr("ff.no_brew"), "Warn", wrap=True))
        bc = QPushButton(tr("ff.choose"))
        bc.clicked.connect(self._choose)
        btns.addWidget(bc)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(self.log, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.button(QDialogButtonBox.StandardButton.Close).setText(tr("common.close"))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._show_status()
        ctx.tools_changed.connect(self._show_status)

    def _show_status(self) -> None:
        ff = self.ctx.ffmpeg
        if ff.ok:
            self.status.setText("✓ " + tr("set.ffmpeg_ok", ver=ff.version, source=ff.ffmpeg or ""))
        else:
            self.status.setText("✕ " + tr("set.ffmpeg_missing"))

    def _run(self, func) -> None:
        self.log.appendPlainText(tr("ff.running"))
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        th = FuncThread(func, self)
        th.log.connect(self.log.appendPlainText)
        th.progress.connect(self._progress)
        th.done.connect(self._done)
        th.failed.connect(lambda m: (self.log.appendPlainText(m), self._finish(False)))
        self.thread = th
        th.start()

    def _progress(self, done, total) -> None:
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done / total * 1000))

    def _done(self, ok) -> None:
        self._finish(bool(ok))

    def _finish(self, ok: bool) -> None:
        self.bar.setVisible(False)
        self.log.appendPlainText(tr("ff.done") if ok else tr("ff.failed"))
        if ok:
            self.ctx.set("ffmpeg_path", "")
        self.ctx.refresh_tools(redetect_encoders=True)

    def _download_win(self) -> None:
        self._run(lambda log, progress, cancelled: bool(tools.download_ffmpeg_windows(log, progress, cancelled)))

    def _brew(self) -> None:
        self._run(lambda log, progress, cancelled: tools.brew_install_ffmpeg(log, cancelled))

    def _choose(self) -> None:
        flt = "ffmpeg (ffmpeg.exe)" if paths.IS_WINDOWS else "ffmpeg (ffmpeg)"
        path, _ = QFileDialog.getOpenFileName(self, tr("ff.choose"), str(paths.APP_DIR), flt + ";;* (*)")
        if not path:
            return
        if not tools.ffmpeg_version(path):
            self.log.appendPlainText(tr("ff.failed") + f": {path}")
            return
        self.ctx.set("ffmpeg_path", path)
        self.log.appendPlainText(tr("ff.done") + f": {path}")
        self.ctx.refresh_tools(redetect_encoders=True)

    def reject(self) -> None:
        if self.thread and self.thread.isRunning():
            self.thread.cancel()
            self.thread.wait(3000)
        super().reject()


# --------------------------------------------------------------------------
# Task details / error
# --------------------------------------------------------------------------

class TaskDetailsDialog(QDialog):
    def __init__(self, ctx: AppContext, task, on_retry=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.task = task
        self.setWindowTitle(tr("errdlg.title") if task.status == "error" else tr("queue.details"))
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        title = label(task.title or task.url, "H2", wrap=True, selectable=True)
        lay.addWidget(title)
        lay.addWidget(label(task.url, "Small", selectable=True))
        if task.status == "error":
            kind = task.error_kind or errors.classify(task.error)
            head = QHBoxLayout()
            ico = IconLabel(22)
            THEME.bind_icon(ico, "ph.warning", "danger")
            head.addWidget(ico)
            head.addWidget(label(tr(f"err.{kind}.title"), "H3"), 1)
            lay.addLayout(head)
            advice = tr(f"err.{kind}.advice")
            if advice:
                lay.addWidget(label(tr("errdlg.what_to_do"), "Muted"))
                lay.addWidget(label("\n".join("• " + a for a in advice.split("\n") if a), wrap=True))
            lay.addWidget(label(tr("errdlg.message"), "Muted"))
            msg = QPlainTextEdit(task.error)
            msg.setReadOnly(True)
            msg.setMaximumHeight(90)
            lay.addWidget(msg)
        elif task.result:
            r = task.result
            files = "\n".join(r.get("files", []) + r.get("extra_files", []))
            lay.addWidget(label(tr("lib.field_file"), "Muted"))
            fl = QPlainTextEdit(files)
            fl.setReadOnly(True)
            fl.setMaximumHeight(100)
            lay.addWidget(fl)
            if r.get("encoder"):
                lay.addWidget(label(f"encoder: {r['encoder']}", "Small"))
        lay.addWidget(label(tr("errdlg.log"), "Muted"))
        log = QPlainTextEdit("\n".join(task.log[-400:]))
        log.setReadOnly(True)
        log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(log, 1)
        QTimer.singleShot(0, lambda: log.verticalScrollBar().setValue(log.verticalScrollBar().maximum()))
        row = QHBoxLayout()
        b_rep = QPushButton(tr("errdlg.copy_report"))
        THEME.bind_icon(b_rep, "ph.clipboard-text")
        b_rep.clicked.connect(self._copy_report)
        row.addWidget(b_rep)
        row.addStretch(1)
        if task.status == "error" and on_retry:
            b_retry = QPushButton(tr("common.retry"))
            b_retry.setObjectName("Primary")
            b_retry.clicked.connect(lambda: (on_retry(), self.accept()))
            row.addWidget(b_retry)
        b_close = QPushButton(tr("common.close"))
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        lay.addLayout(row)

    def _copy_report(self) -> None:
        t = self.task
        preset = (t.spec.get("preset") or {}).get("id", "")
        rep = errors.build_report(kura_version=__version__, versions=self.ctx.versions(),
                                  task={"url": t.url, "preset": preset,
                                        "auth_mode": self.ctx.settings.get("auth_mode")},
                                  message=t.error or "(no error)", log_lines=t.log)
        copy_text(rep)
        self.ctx.notify(tr("errdlg.report_copied"), "success")


# --------------------------------------------------------------------------
# First run
# --------------------------------------------------------------------------

class FirstRunDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(tr("first.title"))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        head = QHBoxLayout()
        ico = QLabel()
        from .theme import app_icon

        ico.setPixmap(app_icon().pixmap(QSize(56, 56)))
        head.addWidget(ico)
        col = QVBoxLayout()
        col.addWidget(label(tr("first.title"), "Title"))
        col.addWidget(label(tr("app.tagline"), "Muted"))
        head.addLayout(col, 1)
        lay.addLayout(head)
        lay.addWidget(label(tr("first.body"), wrap=True))

        lay.addWidget(label(tr("first.save_dir"), "H3"))
        row = QHBoxLayout()
        self.dir_label = ElidedLabel(ctx.settings.save_dir())
        row.addWidget(self.dir_label, 1)
        b = QPushButton(tr("common.change"))
        b.clicked.connect(self._choose_dir)
        row.addWidget(b)
        lay.addLayout(row)
        b_imp = QPushButton(tr("first.import"))
        THEME.bind_icon(b_imp, "ph.clock-counter-clockwise")
        b_imp.clicked.connect(self._import)
        lay.addWidget(b_imp, 0, Qt.AlignmentFlag.AlignLeft)
        self.import_result = label("", "Small", wrap=True)
        self.import_result.setVisible(False)
        lay.addWidget(self.import_result)

        lay.addWidget(label(tr("first.engine"), "H3"))
        self.engine = label("", wrap=True)
        lay.addWidget(self.engine)
        self.b_ff = QPushButton(tr("set.ffmpeg_setup"))
        self.b_ff.clicked.connect(lambda: FfmpegSetupDialog(ctx, self).exec())
        lay.addWidget(self.b_ff, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(label(tr("first.login_note"), "Muted", wrap=True))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("first.start"))
        bb.button(QDialogButtonBox.StandardButton.Ok).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        ctx.tools_changed.connect(self._engine_status)
        self._engine_status()

    def _engine_status(self) -> None:
        c = self.ctx
        if not c.tools_ready:
            self.engine.setText(tr("common.loading"))
            return
        ok, ng = "✓", "✕"
        lines = [f"{ok} yt-dlp {c.ytdlp_version}",
                 f"{ok if c.deno.ok else ng} Deno {c.deno.version or tr('first.missing')}",
                 f"{ok if c.ffmpeg.ok else ng} ffmpeg {c.ffmpeg.version or tr('first.missing')}"]
        self.engine.setText("\n".join(lines))
        self.b_ff.setVisible(not c.ffmpeg.ok)

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("common.choose_folder"), self.ctx.settings.save_dir())
        if d:
            self.ctx.settings.remember_save_dir(d)
            self.dir_label.setText(d)

    def _import(self) -> None:
        path = import_v2_dialog(self.ctx, self)
        if path is not None:
            self.import_result.setText(path)
            self.import_result.setVisible(True)
            self.dir_label.setText(self.ctx.settings.save_dir())


def import_v2_dialog(ctx: AppContext, parent: QWidget) -> str | None:
    path, _ = QFileDialog.getOpenFileName(parent, tr("set.import_v2"), str(paths.APP_DIR.parent),
                                          "user_config.json (user_config.json);;JSON (*.json)")
    if not path:
        return None
    try:
        applied = import_v2_settings(ctx.settings, path)
    except Exception as e:  # noqa: BLE001
        QMessageBox.warning(parent, tr("set.import_v2"), str(e))
        return None
    for key in applied:
        ctx.settings_changed.emit(key)
    msg = tr("set.import_v2_done", items=", ".join(applied)) if applied else tr("set.import_v2_none")
    ctx.notify(msg, "success" if applied else "info")
    return msg


def confirm(parent: QWidget, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Question)
    yes = box.addButton(tr("common.yes"), QMessageBox.ButtonRole.YesRole)
    box.addButton(tr("common.no"), QMessageBox.ButtonRole.NoRole)
    box.exec()
    return box.clickedButton() is yes


def file_exists(path: str | None) -> bool:
    return bool(path) and os.path.exists(path)
