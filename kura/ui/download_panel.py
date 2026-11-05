"""The right-hand download panel of the Browse page."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, paths
from ..core import citation, urls
from ..core.engine import estimate
from ..core.presets import SubtitleOptions
from ..core.transcode import norm_codec
from .context import AppContext
from .dialogs import FormatPickerDialog, PlaylistDialog, confirm, copy_text, fill_preset_combo, parse_time
from .theme import THEME, human_duration, human_size
from .widgets import CollapsibleSection, ElidedLabel, IconLabel, ThumbLabel, flat_button, label
from .workers import InfoThread

tr = i18n.tr
PANEL_WIDTH = 360


class DownloadPanel(QFrame):
    open_queue = pyqtSignal()

    def __init__(self, ctx: AppContext, queue):
        super().__init__()
        self.ctx = ctx
        self.queue = queue
        self.setObjectName("Panel")
        self.setFixedWidth(PANEL_WIDTH)
        self.url = ""
        self.state = "empty"
        self.url_info = urls.classify("")
        self.info: dict | None = None
        self.thumb_bytes = b""
        self.format_override: str | None = None
        self.container_override: str | None = None
        self._req = 0
        self._threads: list[InfoThread] = []
        self._cache: dict[str, tuple[dict, bytes]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        self.body_layout = lay

        # -- info card ---------------------------------------------------------------
        self.card = QFrame()
        self.card.setObjectName("Card")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(10, 10, 10, 10)
        cl.setSpacing(6)
        tw = PANEL_WIDTH - 2 * 14 - 2 * 10 - 16  # leave room for the scroll bar
        self.thumb = ThumbLabel(tw, int(tw * 9 / 16))
        cl.addWidget(self.thumb)
        self.title = label(tr("panel.empty_title"), "VideoTitle", wrap=True, selectable=True)
        cl.addWidget(self.title)
        self.meta = label(tr("panel.empty_body"), "Muted", wrap=True)
        cl.addWidget(self.meta)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        cl.addLayout(self.chips)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setVisible(False)
        cl.addWidget(self.busy)
        self.retry_btn = QPushButton(tr("common.retry"))
        self.retry_btn.setVisible(False)
        self.retry_btn.clicked.connect(lambda: self.set_url(self.url, force=True))
        cl.addWidget(self.retry_btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.card)

        # -- playlist choice ---------------------------------------------------------
        self.list_box = QFrame()
        self.list_box.setObjectName("Card")
        ll = QVBoxLayout(self.list_box)
        ll.setContentsMargins(12, 10, 12, 10)
        hl = QHBoxLayout()
        ico = IconLabel(16)
        THEME.bind_icon(ico, "ph.list-bullets", "accent")
        hl.addWidget(ico)
        self.list_label = label(tr("panel.list_detected"), "H3")
        hl.addWidget(self.list_label, 1)
        ll.addLayout(hl)
        self.rb_only = QRadioButton(tr("panel.only_this"))
        self.rb_list = QRadioButton(tr("panel.whole_list"))
        self.rb_only.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_only)
        grp.addButton(self.rb_list)
        ll.addWidget(self.rb_only)
        ll.addWidget(self.rb_list)
        self.rb_list.toggled.connect(lambda *_: self._update_action())
        lay.addWidget(self.list_box)

        # -- preset -----------------------------------------------------------------------
        self.options = QWidget()
        ol = QVBoxLayout(self.options)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(8)
        ol.addWidget(label(tr("panel.preset"), "H3"))
        self.preset = QComboBox()
        self.preset.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        fill_preset_combo(self.preset, ctx)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        ol.addWidget(self.preset)
        self.codec_row = QWidget()
        cr = QHBoxLayout(self.codec_row)
        cr.setContentsMargins(0, 0, 0, 0)
        self.rb_h264 = QRadioButton(tr("panel.codec_h264"))
        self.rb_hevc = QRadioButton(tr("panel.codec_hevc"))
        cgrp = QButtonGroup(self)
        cgrp.addButton(self.rb_h264)
        cgrp.addButton(self.rb_hevc)
        (self.rb_hevc if ctx.settings.get("edit4k_codec") == "hevc" else self.rb_h264).setChecked(True)
        self.rb_h264.toggled.connect(self._codec_changed)
        cr.addWidget(self.rb_h264)
        cr.addWidget(self.rb_hevc)
        cr.addStretch(1)
        ol.addWidget(self.codec_row)
        self.preset_desc = label("", "Small", wrap=True)
        ol.addWidget(self.preset_desc)
        self.convert_note = label("", "Small", wrap=True)
        ol.addWidget(self.convert_note)

        # -- subtitles ----------------------------------------------------------------------
        sub = ctx.settings.get("subtitles")
        self.sec_sub = CollapsibleSection(tr("panel.subtitles"), expanded=bool(sub.get("enabled")))
        b = self.sec_sub.body_layout
        self.sub_enable = QCheckBox(tr("panel.sub_enable"))
        self.sub_enable.setChecked(bool(sub.get("enabled")))
        b.addWidget(self.sub_enable)
        g = QGridLayout()
        g.addWidget(QLabel(tr("panel.sub_langs")), 0, 0)
        self.sub_langs = QLineEdit(sub.get("langs", "ja,en"))
        self.sub_langs.setPlaceholderText(tr("panel.sub_langs_ph"))
        g.addWidget(self.sub_langs, 0, 1)
        g.addWidget(QLabel(tr("panel.sub_mode")), 1, 0)
        self.sub_mode = QComboBox()
        self.sub_mode.addItem(tr("panel.sub_mode_file"), "file")
        self.sub_mode.addItem(tr("panel.sub_mode_embed"), "embed")
        self.sub_mode.addItem(tr("panel.sub_mode_both"), "both")
        self.sub_mode.setCurrentIndex(max(self.sub_mode.findData(sub.get("mode", "file")), 0))
        g.addWidget(self.sub_mode, 1, 1)
        b.addLayout(g)
        self.sub_auto = QCheckBox(tr("panel.sub_auto"))
        self.sub_auto.setChecked(bool(sub.get("auto", True)))
        self.sub_txt = QCheckBox(tr("panel.sub_txt"))
        self.sub_txt.setChecked(bool(sub.get("txt", True)))
        self.sub_csv = QCheckBox(tr("panel.sub_csv"))
        self.sub_csv.setChecked(bool(sub.get("csv", False)))
        for w in (self.sub_auto, self.sub_txt, self.sub_csv):
            b.addWidget(w)
        self.sub_avail = label("", "Small", wrap=True)
        b.addWidget(self.sub_avail)
        for w in (self.sub_enable, self.sub_auto, self.sub_txt, self.sub_csv):
            w.toggled.connect(self._save_sub_defaults)
        self.sub_langs.editingFinished.connect(self._save_sub_defaults)
        self.sub_mode.currentIndexChanged.connect(self._save_sub_defaults)
        self.sub_enable.toggled.connect(self._sync_enabled)
        ol.addWidget(self.sec_sub)

        # -- clip ------------------------------------------------------------------------------
        self.sec_clip = CollapsibleSection(tr("panel.clip"), expanded=False)
        b = self.sec_clip.body_layout
        self.clip_enable = QCheckBox(tr("panel.clip_enable"))
        b.addWidget(self.clip_enable)
        rr = QHBoxLayout()
        self.rb_time = QRadioButton(tr("panel.clip_by_time"))
        self.rb_chap = QRadioButton(tr("panel.clip_by_chapter"))
        self.rb_time.setChecked(True)
        cg = QButtonGroup(self)
        cg.addButton(self.rb_time)
        cg.addButton(self.rb_chap)
        rr.addWidget(self.rb_time)
        rr.addWidget(self.rb_chap)
        rr.addStretch(1)
        b.addLayout(rr)
        tg = QGridLayout()
        tg.addWidget(QLabel(tr("panel.clip_start")), 0, 0)
        self.clip_start = QLineEdit()
        self.clip_start.setPlaceholderText("0:00")
        tg.addWidget(self.clip_start, 0, 1)
        tg.addWidget(QLabel(tr("panel.clip_end")), 0, 2)
        self.clip_end = QLineEdit()
        self.clip_end.setPlaceholderText("1:30")
        tg.addWidget(self.clip_end, 0, 3)
        self.time_box = QWidget()
        self.time_box.setLayout(tg)
        tg.setContentsMargins(0, 0, 0, 0)
        b.addWidget(self.time_box)
        self.chapter = QComboBox()
        b.addWidget(self.chapter)
        self.clip_precise = QCheckBox(tr("panel.clip_precise"))
        b.addWidget(self.clip_precise)
        self.clip_error = label("", "Error", wrap=True)
        b.addWidget(self.clip_error)
        for w in (self.clip_enable, self.rb_time, self.rb_chap):
            w.toggled.connect(self._sync_enabled)
        ol.addWidget(self.sec_clip)

        # -- advanced -------------------------------------------------------------------------
        self.sec_adv = CollapsibleSection(tr("panel.advanced"), expanded=False)
        b = self.sec_adv.body_layout
        self.fmt_label = label(tr("panel.format_auto"), "Small", wrap=True)
        b.addWidget(self.fmt_label)
        fr = QHBoxLayout()
        self.fmt_btn = QPushButton(tr("panel.pick_format"))
        self.fmt_btn.clicked.connect(self._pick_formats)
        self.fmt_reset = QPushButton(tr("panel.format_reset"))
        self.fmt_reset.clicked.connect(self._reset_formats)
        fr.addWidget(self.fmt_btn)
        fr.addWidget(self.fmt_reset)
        fr.addStretch(1)
        b.addLayout(fr)
        self.info_json = QCheckBox(tr("panel.info_json"))
        self.info_json.setChecked(bool(ctx.settings.get("write_info_json")))
        self.info_json.toggled.connect(lambda v: ctx.set("write_info_json", v))
        b.addWidget(self.info_json)
        ol.addWidget(self.sec_adv)

        # -- save to -----------------------------------------------------------------------------
        ol.addWidget(label(tr("panel.save_to"), "H3"))
        sr = QHBoxLayout()
        self.save_label = ElidedLabel(ctx.settings.save_dir())
        self.save_label.setObjectName("Muted")
        sr.addWidget(self.save_label, 1)
        self.save_btn = flat_button("ph.folder-simple", tr("common.change"))
        self.save_btn.clicked.connect(self._choose_dir)
        sr.addWidget(self.save_btn)
        ob = flat_button("ph.arrow-square-out", tr("panel.open_folder"))
        ob.clicked.connect(lambda: paths.open_path(self.ctx.settings.save_dir()))
        sr.addWidget(ob)
        ol.addLayout(sr)
        lay.addWidget(self.options)

        # -- action ------------------------------------------------------------------------------
        self.estimate_label = label("", "Small", wrap=True)
        lay.addWidget(self.estimate_label)
        self.warn_label = label("", "Warn", wrap=True)
        lay.addWidget(self.warn_label)
        self.dl_btn = QPushButton(tr("panel.download"))
        self.dl_btn.setObjectName("Primary")
        THEME.bind_icon(self.dl_btn, "ph.download-simple", "accent_text")
        self.dl_btn.setIconSize(QSize(18, 18))
        self.dl_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dl_btn.setToolTip("Ctrl+D")
        self.dl_btn.clicked.connect(self.trigger_download)
        lay.addWidget(self.dl_btn)
        ar = QHBoxLayout()
        self.cite_btn = QPushButton(tr("panel.copy_citation"))
        THEME.bind_icon(self.cite_btn, "ph.quotes")
        cite_menu = QMenu(self.cite_btn)
        for style in citation.STYLES:
            act = cite_menu.addAction(tr(f"set.cite_{style}"))
            act.triggered.connect(lambda _=False, s=style: self._copy_citation(s))
        self.cite_btn.setMenu(cite_menu)
        self.url_btn = QPushButton(tr("panel.copy_url"))
        THEME.bind_icon(self.url_btn, "ph.link")
        self.url_btn.clicked.connect(self._copy_url)
        ar.addWidget(self.cite_btn)
        ar.addWidget(self.url_btn)
        lay.addLayout(ar)

        # -- mini queue ----------------------------------------------------------------------------
        lay.addSpacing(6)
        mq_head = QHBoxLayout()
        mq_head.addWidget(label(tr("panel.mini_queue"), "H3"))
        mq_head.addStretch(1)
        self.mq_link = QPushButton(tr("panel.open_queue", n=0))
        self.mq_link.setObjectName("Link")
        self.mq_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mq_link.clicked.connect(self.open_queue.emit)
        mq_head.addWidget(self.mq_link)
        lay.addLayout(mq_head)
        self.mq_box = QVBoxLayout()
        self.mq_box.setSpacing(6)
        lay.addLayout(self.mq_box)
        self.mq_empty = label(tr("panel.queue_idle"), "Small")
        self.mq_box.addWidget(self.mq_empty)
        self._mq_rows: dict[str, tuple[QWidget, QLabel, QProgressBar]] = {}
        lay.addStretch(1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(450)
        self._debounce.timeout.connect(self._fetch)

        queue.updated.connect(self._mq_update)
        queue.added.connect(self._mq_update)
        queue.removed.connect(self._mq_update)
        ctx.tools_changed.connect(self._refresh_estimate)
        ctx.presets_changed.connect(self._reload_presets)
        ctx.settings_changed.connect(self._on_setting)
        self._preset_changed()
        self._show_state("empty")

    # ------------------------------------------------------------------------------------------
    # URL / info
    # ------------------------------------------------------------------------------------------
    def set_url(self, url: str, force: bool = False) -> None:
        url = url or ""
        if url == self.url and not force:
            return
        self.url = url
        self.url_info = urls.classify(url)
        self.format_override = self.container_override = None
        self.fmt_label.setText(tr("panel.format_auto"))
        k = self.url_info.kind
        if k in ("empty", "home", "search") or not url.startswith("http"):
            self.info = None
            self._show_state("empty")
            return
        if self.url_info.is_collection:
            self.info = None
            self._show_state("collection")
            return
        self._show_state("loading")
        self._debounce.start()

    def _cache_key(self) -> str:
        if self.url_info.video_id:
            return "yt:" + self.url_info.video_id
        return self.url

    def _fetch(self) -> None:
        key = self._cache_key()
        if key in self._cache:
            info, thumb = self._cache[key]
            self._on_info(self._req, info, thumb, cached=True)
            return
        self._req += 1
        url = urls.canonical_video_url(self.url_info.video_id) if self.url_info.video_id else self.url
        th = InfoThread(self._req, url, self.ctx.tool_config(), self.ctx.auth_config(), parent=self)
        th.done.connect(self._on_info)
        th.failed.connect(self._on_info_failed)
        th.finished.connect(lambda th=th: self._threads.remove(th) if th in self._threads else None)
        self._threads.append(th)
        th.start()

    def _on_info(self, req: int, info: dict, thumb: bytes, cached: bool = False) -> None:
        if req != self._req:
            return
        if info.get("_type") == "playlist" and not info.get("formats"):
            entries = [e for e in (info.get("entries") or []) if e]
            if len(entries) == 1:
                info = entries[0]
            else:
                self.info = None
                self._show_state("collection")
                return
        if not cached:
            self._cache[self._cache_key()] = (info, thumb)
            if len(self._cache) > 40:
                self._cache.pop(next(iter(self._cache)))
        self.info = info
        self.thumb_bytes = thumb
        self._show_state("info")

    def _on_info_failed(self, req: int, msg: str, kind: str) -> None:
        if req != self._req:
            return
        self.info = None
        if kind == "unsupported" or self.url_info.kind == "other":
            self._show_state("unsupported", msg)
        else:
            self._show_state("error", msg, kind)

    # ------------------------------------------------------------------------------------------
    # States
    # ------------------------------------------------------------------------------------------
    def _clear_chips(self) -> None:
        while self.chips.count():
            it = self.chips.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _chip(self, text: str, accent: bool = False) -> None:
        c = QLabel(text)
        c.setObjectName("ChipAccent" if accent else "Chip")
        self.chips.addWidget(c)

    def _show_state(self, state: str, msg: str = "", kind: str = "") -> None:
        self.state = state
        self._clear_chips()
        self.busy.setVisible(state == "loading")
        self.retry_btn.setVisible(state == "error")
        self.list_box.setVisible(state == "info" and self.url_info.has_list)
        self.thumb.setVisible(state in ("info", "loading", "collection", "error"))
        if state == "empty":
            self.thumb.setVisible(False)
            self.title.setText(tr("panel.empty_title"))
            self.meta.setText(tr("panel.empty_body"))
        elif state == "loading":
            self.thumb.clear_thumb()
            self.title.setText(self.url)
            self.meta.setText(tr("panel.loading"))
        elif state == "collection":
            self.thumb.clear_thumb("ph.list-bullets")
            self.title.setText(tr("panel.collection_title"))
            self.meta.setText(tr("panel.collection_body"))
        elif state == "unsupported":
            self.thumb.setVisible(False)
            self.title.setText(self.url)
            self.meta.setText(tr("panel.not_supported"))
        elif state == "error":
            self.thumb.clear_thumb("ph.warning")
            self.title.setText(tr("panel.info_failed"))
            k = kind or "unknown"
            self.meta.setText(f"{tr(f'err.{k}.title')}\n{msg[:300]}")
        elif state == "info" and self.info:
            i = self.info
            if not self.thumb.set_image_bytes(self.thumb_bytes):
                self.thumb.clear_thumb()
            self.title.setText(i.get("title") or self.url)
            bits = [i.get("channel") or i.get("uploader") or ""]
            if i.get("duration"):
                bits.append(human_duration(i.get("duration")))
            if i.get("upload_date"):
                d = str(i["upload_date"])
                bits.append(f"{d[:4]}-{d[4:6]}-{d[6:]}")
            self.meta.setText(" · ".join(b for b in bits if b))
            heights = [min(f.get("height") or 0, f.get("width") or 0) or (f.get("height") or 0)
                       for f in i.get("formats") or [] if f.get("vcodec") not in (None, "none")]
            if heights:
                h = max(heights)
                self._chip(tr("panel.max_res", res=_res_label(h)), accent=True)
            if any((f.get("dynamic_range") or "SDR") != "SDR" for f in i.get("formats") or []):
                self._chip("HDR")
            if i.get("chapters"):
                self._chip(tr("panel.chapters_n", n=len(i["chapters"])))
            if i.get("live_status") == "is_live":
                self._chip(tr("panel.live"))
            elif i.get("live_status") == "is_upcoming":
                self._chip(tr("panel.upcoming"))
            if "/shorts/" in self.url:
                self._chip(tr("panel.shorts"))
            if i.get("subtitles"):
                self._chip("CC")
        self.chips.addStretch(1)
        self._fill_chapters()
        self._fill_sub_avail()
        self._refresh_estimate()
        self._update_action()

    def _fill_chapters(self) -> None:
        self.chapter.clear()
        chapters = (self.info or {}).get("chapters") or []
        for c in chapters:
            self.chapter.addItem(f"{human_duration(c.get('start_time'))}  {c.get('title')}", c.get("title"))
        if not chapters:
            self.chapter.addItem(tr("panel.clip_no_chapters"), None)
        self._sync_enabled()

    def _fill_sub_avail(self) -> None:
        subs = sorted(((self.info or {}).get("subtitles") or {}).keys())
        subs = [s for s in subs if s != "live_chat"]
        if not self.info:
            self.sub_avail.setText("")
        elif subs:
            self.sub_avail.setText(tr("panel.available_subs", langs=", ".join(subs[:12])))
        else:
            self.sub_avail.setText(tr("panel.no_subs"))

    # ------------------------------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------------------------------
    def current_preset(self):
        return self.ctx.preset(self.preset.currentData())

    def _reload_presets(self) -> None:
        fill_preset_combo(self.preset, self.ctx, self.preset.currentData())
        self._preset_changed()

    def _on_setting(self, key: str) -> None:
        if key in ("save_dir", "recent_save_dirs"):
            self.save_label.setText(self.ctx.settings.save_dir())
        elif key in ("custom_presets", "edit4k_codec"):
            self._reload_presets()
        elif key == "default_preset":
            idx = self.preset.findData(self.ctx.settings.get("default_preset"))
            if idx >= 0:
                self.preset.setCurrentIndex(idx)

    def _preset_changed(self) -> None:
        p = self.current_preset()
        self.codec_row.setVisible(p.id == "edit4k")
        self.preset_desc.setText(self.ctx.preset_description(p))
        self.sub_mode.model().item(1).setEnabled(not p.is_audio)
        self.sub_mode.model().item(2).setEnabled(not p.is_audio)
        if p.is_audio and self.sub_mode.currentData() != "file":
            self.sub_mode.setCurrentIndex(0)
        self._refresh_estimate()

    def _codec_changed(self) -> None:
        self.ctx.set("edit4k_codec", "h264" if self.rb_h264.isChecked() else "hevc")

    def _save_sub_defaults(self) -> None:
        self.ctx.set("subtitles", {
            "enabled": self.sub_enable.isChecked(), "langs": self.sub_langs.text().strip() or "ja,en",
            "auto": self.sub_auto.isChecked(), "mode": self.sub_mode.currentData(),
            "txt": self.sub_txt.isChecked(), "csv": self.sub_csv.isChecked(),
        })

    def _sync_enabled(self) -> None:
        on = self.sub_enable.isChecked()
        for w in (self.sub_langs, self.sub_mode, self.sub_auto, self.sub_txt, self.sub_csv):
            w.setEnabled(on)
        clip = self.clip_enable.isChecked()
        has_ch = bool((self.info or {}).get("chapters"))
        self.rb_chap.setEnabled(clip and has_ch)
        if not has_ch and self.rb_chap.isChecked():
            self.rb_time.setChecked(True)
        self.rb_time.setEnabled(clip)
        self.time_box.setVisible(self.rb_time.isChecked())
        self.time_box.setEnabled(clip)
        self.chapter.setVisible(self.rb_chap.isChecked())
        self.chapter.setEnabled(clip and has_ch)
        self.clip_precise.setEnabled(clip)
        self.clip_error.setText("")

    def _subtitle_options(self) -> SubtitleOptions | None:
        if not self.sub_enable.isChecked():
            return None
        return SubtitleOptions.from_settings({
            "langs": self.sub_langs.text(), "auto": self.sub_auto.isChecked(), "mode": self.sub_mode.currentData(),
            "txt": self.sub_txt.isChecked(), "csv": self.sub_csv.isChecked()})

    def _clip_options(self) -> dict | None:
        if not self.clip_enable.isChecked():
            return None
        if self.rb_chap.isChecked() and self.chapter.currentData():
            return {"chapters": [self.chapter.currentData()], "precise": self.clip_precise.isChecked()}
        try:
            start = parse_time(self.clip_start.text()) or 0.0
            end = parse_time(self.clip_end.text())
        except ValueError:
            self.clip_error.setText(tr("panel.clip_bad_time"))
            self.sec_clip.expand(True)
            raise
        if end is not None and end <= start:
            self.clip_error.setText(tr("panel.clip_bad_time"))
            self.sec_clip.expand(True)
            raise ValueError("end <= start")
        if end is None and self.info and self.info.get("duration"):
            end = float(self.info["duration"])
        return {"start": start, "end": end, "precise": self.clip_precise.isChecked()}

    def _pick_formats(self) -> None:
        if not self.info:
            return
        dlg = FormatPickerDialog(self.ctx, self.info, self)
        if dlg.exec():
            fmt, container = dlg.selection()
            if fmt:
                self.format_override, self.container_override = fmt, container
                self.fmt_label.setText(tr("panel.format_manual", fmt=fmt, container=container.upper()))
                self._refresh_estimate()

    def _reset_formats(self) -> None:
        self.format_override = self.container_override = None
        self.fmt_label.setText(tr("panel.format_auto"))
        self._refresh_estimate()

    def _choose_dir(self) -> None:
        menu = QMenu(self)
        for d in self.ctx.settings.get("recent_save_dirs", [])[:6]:
            if d != self.ctx.settings.save_dir():
                menu.addAction(d).triggered.connect(lambda _=False, d=d: self._set_dir(d))
        if menu.actions():
            menu.addSeparator()
        menu.addAction(tr("common.choose_folder") + "…").triggered.connect(self._browse_dir)
        menu.exec(self.save_btn.mapToGlobal(self.save_btn.rect().bottomLeft()))

    def _browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("common.choose_folder"), self.ctx.settings.save_dir())
        if d:
            self._set_dir(d)

    def _set_dir(self, d: str) -> None:
        self.ctx.settings.remember_save_dir(d)
        self.ctx.settings_changed.emit("save_dir")

    # ------------------------------------------------------------------------------------------
    # Estimate & action
    # ------------------------------------------------------------------------------------------
    def _refresh_estimate(self) -> None:
        p = self.current_preset()
        ff = self.ctx.ffmpeg.ok or not self.ctx.tools_ready
        warn = "" if ff else tr("panel.ffmpeg_missing")
        self.warn_label.setText(warn)
        self.warn_label.setVisible(bool(warn))
        if not self.info or self.state != "info":
            self.estimate_label.setText("")
            self.convert_note.setText("")
            return
        try:
            size, chosen = estimate(self.info, p, self.ctx.ffmpeg.ok)
        except Exception:  # noqa: BLE001
            size, chosen = None, []
        if self.format_override:
            fids = self.format_override.split("+")
            chosen = [f for f in self.info.get("formats") or [] if f.get("format_id") in fids]
            sizes = [f.get("filesize") or f.get("filesize_approx") for f in chosen]
            size = int(sum(sizes)) if sizes and all(sizes) else None
        v = next((f for f in chosen if f.get("vcodec") not in (None, "none")), None)
        a = next((f for f in chosen if f.get("acodec") not in (None, "none")), None)
        src = "/".join(x for x in ((norm_codec(v.get("vcodec")) if v else None),
                                   (norm_codec(a.get("acodec")) if a else None)) if x)
        if v and v.get("height"):
            src = f"{_res_label(min(v.get('height') or 0, v.get('width') or 99999))} {src}"
        will_convert = False
        if not p.is_audio and not self.format_override and self.ctx.ffmpeg.ok:
            vc = norm_codec(v.get("vcodec")) if v else None
            ac = norm_codec(a.get("acodec")) if a else None
            need_v = p.vcodec in ("h264", "hevc") and vc and vc != p.vcodec
            need_a = p.acodec == "aac" and ac and ac != "aac"
            will_convert = bool(need_v or need_a)
            dst = "/".join(x for x in ((p.vcodec if p.vcodec != "copy" else vc),
                                       ("aac" if p.acodec == "aac" else ac)) if x)
            self.convert_note.setText(tr("panel.will_convert", src=src, dst=f"{dst} {p.container.upper()}")
                                      if will_convert else (src + " · " + tr("panel.no_convert") if src else ""))
        elif p.is_audio:
            self.convert_note.setText(f"{src} → {p.acodec.upper()}" if src else "")
        else:
            self.convert_note.setText(src)
        if size:
            key = "panel.estimate_convert" if will_convert or p.is_audio else "panel.estimate"
            self.estimate_label.setText(tr(key, size=human_size(size)))
        else:
            self.estimate_label.setText("")

    def _update_action(self) -> None:
        st = getattr(self, "state", "empty")
        collection = st == "collection" or (st == "info" and self.url_info.has_list and self.rb_list.isChecked())
        self.dl_btn.setText(tr("panel.download_list") if collection else tr("panel.download"))
        self.dl_btn.setEnabled(st in ("info", "collection"))
        self.options.setEnabled(st in ("info", "collection"))
        self.cite_btn.setEnabled(st == "info")
        self.url_btn.setEnabled(bool(self.url) and st != "empty")
        self.fmt_btn.setEnabled(st == "info")

    def trigger_download(self) -> None:
        st = getattr(self, "state", "empty")
        if st not in ("info", "collection"):
            return
        try:
            clip = self._clip_options()
        except ValueError:
            return
        preset = self.current_preset()
        subs = self._subtitle_options()
        if st == "collection" or (self.url_info.has_list and self.rb_list.isChecked()):
            PlaylistDialog(self.ctx, self.queue, self.url, preset.id, subs, self).exec()
            return
        info = self.info or {}
        vid = info.get("id") or self.url_info.video_id or ""
        if vid:
            prev = self.ctx.db.find_downloaded(vid, preset.id)
            if prev and not clip and not confirm(self, tr("panel.already_title"),
                                                 tr("panel.already_body", path=prev.get("file_path"))):
                return
        url = urls.canonical_video_url(self.url_info.video_id) if self.url_info.video_id else (
            info.get("webpage_url") or self.url)
        self.queue.add(url, preset, title=info.get("title") or "", video_id=vid,
                       thumb_bytes=self.thumb_bytes or None, subtitles=subs, clip=clip,
                       format_override=self.format_override, container_override=self.container_override,
                       write_info_json=self.info_json.isChecked())
        self.ctx.notify(tr("panel.added") + "\n" + (info.get("title") or url), "success")

    def _copy_citation(self, style: str) -> None:
        if not self.info:
            return
        copy_text(citation.cite(self.info, style))
        self.ctx.notify(tr("common.copied"), "success", timeout=2000)

    def _copy_url(self) -> None:
        if self.url:
            url = urls.canonical_video_url(self.url_info.video_id) if self.url_info.video_id else self.url
            copy_text(url)
            self.ctx.notify(tr("common.copied"), "success", timeout=2000)

    # ------------------------------------------------------------------------------------------
    # Mini queue
    # ------------------------------------------------------------------------------------------
    def _mq_update(self, *_args) -> None:
        tasks = [t for t in self.queue.ordered() if t.status in ("running", "pending", "paused")]
        active_ids = [t.id for t in tasks[:3]]
        for tid in list(self._mq_rows):
            if tid not in active_ids:
                w, _, _ = self._mq_rows.pop(tid)
                w.deleteLater()
        for t in tasks[:3]:
            if t.id not in self._mq_rows:
                w = QWidget()
                v = QVBoxLayout(w)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(3)
                lb = ElidedLabel("", Qt.TextElideMode.ElideRight)
                lb.setObjectName("Small")
                bar = QProgressBar()
                bar.setRange(0, 1000)
                v.addWidget(lb)
                v.addWidget(bar)
                self.mq_box.addWidget(w)
                self._mq_rows[t.id] = (w, lb, bar)
            _, lb, bar = self._mq_rows[t.id]
            lb.setText(t.title or t.url)
            if t.fraction is None and t.status == "running":
                bar.setRange(0, 0)
            else:
                bar.setRange(0, 1000)
                bar.setValue(int((t.fraction or 0) * 1000))
        self.mq_empty.setVisible(not tasks)
        self.mq_link.setText(tr("panel.open_queue", n=len(tasks)))


def _res_label(h: int) -> str:
    if h >= 4320:
        return "8K"
    if h >= 2160:
        return "4K"
    if h >= 1440:
        return "1440p"
    return f"{h}p"
