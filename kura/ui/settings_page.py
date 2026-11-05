"""Settings page (changes apply immediately)."""

from __future__ import annotations

import shutil

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import __author__, __release_date__, __repo__, __version__, i18n, paths
from ..config import FILENAME_TEMPLATE_DEFAULT
from ..core import naming, updater
from ..core.presets import Preset, new_custom_preset
from .context import AppContext
from .dialogs import FfmpegSetupDialog, confirm, edit_custom_preset, fill_preset_combo, import_v2_dialog
from .theme import THEME
from .widgets import ElidedLabel, label

tr = i18n.tr


def _row(*widgets, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for i, x in enumerate(widgets):
        lay.addWidget(x, 1 if (stretch_last and i == len(widgets) - 1) else 0)
    if not stretch_last:
        lay.addStretch(1)
    return w


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, main):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.main = main
        s = ctx.settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(6)
        lay.addWidget(label(tr("set.title"), "Title"))
        self.sections: dict[str, QGroupBox] = {}

        # -- General ----------------------------------------------------------------------------------
        g = self._group("general", tr("set.general"), lay)
        f = QFormLayout(g)
        self.lang = QComboBox()
        self.lang.addItem(tr("set.lang_auto"), "auto")
        self.lang.addItem("日本語", "ja")
        self.lang.addItem("English", "en")
        self.lang.setCurrentIndex(max(self.lang.findData(s.get("language")), 0))
        self.lang.currentIndexChanged.connect(self._lang_changed)
        self.lang_note = label("", "Small")
        f.addRow(tr("set.language"), _row(self.lang, self.lang_note))
        self.theme = QComboBox()
        for k in ("system", "light", "dark"):
            self.theme.addItem(tr(f"set.theme_{k}"), k)
        self.theme.setCurrentIndex(max(self.theme.findData(s.get("theme")), 0))
        self.theme.currentIndexChanged.connect(self._theme_changed)
        f.addRow(tr("set.theme"), _row(self.theme))
        self.save_dir = ElidedLabel(s.save_dir())
        b = QPushButton(tr("common.change"))
        b.clicked.connect(self._choose_dir)
        bo = QPushButton(tr("common.open"))
        bo.clicked.connect(lambda: paths.open_path(self.ctx.settings.save_dir()))
        f.addRow(tr("set.save_dir"), _row(b, bo, self.save_dir, stretch_last=True))
        self.tmpl = QLineEdit(s.get("filename_template"))
        self.tmpl.textChanged.connect(self._tmpl_changed)
        rb = QPushButton(tr("set.filename_reset"))
        rb.clicked.connect(lambda: self.tmpl.setText(FILENAME_TEMPLATE_DEFAULT))
        f.addRow(tr("set.filename"), _row(self.tmpl, rb, stretch_last=False))
        self.tmpl.setMinimumWidth(380)
        self.tmpl_preview = label("", "Muted")
        tok = label(tr("set.filename_tokens"), "Small", wrap=True)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        bl.addWidget(self.tmpl_preview)
        bl.addWidget(tok)
        f.addRow("", box)
        self.pl_sub = self._check(tr("set.playlist_subfolder"), "playlist_subfolder")
        f.addRow("", self.pl_sub)
        self.home = QLineEdit(s.get("home_url"))
        self.home.editingFinished.connect(lambda: ctx.set("home_url", self.home.text().strip() or
                                                          "https://www.youtube.com"))
        f.addRow(tr("set.home_url"), self.home)
        self._tmpl_changed()

        # -- Downloads ---------------------------------------------------------------------------------
        g = self._group("download", tr("set.download"), lay)
        f = QFormLayout(g)
        self.def_preset = QComboBox()
        fill_preset_combo(self.def_preset, ctx, s.get("default_preset"))
        self.def_preset.currentIndexChanged.connect(lambda *_: ctx.set("default_preset", self.def_preset.currentData()))
        f.addRow(tr("set.default_preset"), _row(self.def_preset))
        self.c4k = QComboBox()
        self.c4k.addItem(tr("panel.codec_h264"), "h264")
        self.c4k.addItem(tr("panel.codec_hevc"), "hevc")
        self.c4k.setCurrentIndex(max(self.c4k.findData(s.get("edit4k_codec")), 0))
        self.c4k.currentIndexChanged.connect(lambda *_: (ctx.set("edit4k_codec", self.c4k.currentData()),
                                                          ctx.presets_changed.emit()))
        f.addRow(tr("set.edit4k_codec"), _row(self.c4k))
        self.conc = QSpinBox()
        self.conc.setRange(1, 6)
        self.conc.setValue(int(s.get("concurrency")))
        self.conc.valueChanged.connect(lambda v: ctx.set("concurrency", v))
        f.addRow(tr("set.concurrency"), _row(self.conc))
        self.polite = self._check(tr("set.polite"), "polite_delay")
        f.addRow("", self.polite)
        self.dmin = QSpinBox()
        self.dmin.setRange(0, 120)
        self.dmin.setValue(int(s.get("delay_min")))
        self.dmax = QSpinBox()
        self.dmax.setRange(0, 300)
        self.dmax.setValue(int(s.get("delay_max")))
        self.dmin.valueChanged.connect(lambda v: ctx.set("delay_min", v))
        self.dmax.valueChanged.connect(lambda v: ctx.set("delay_max", v))
        f.addRow(tr("set.polite_range"), _row(self.dmin, QLabel("–"), self.dmax))
        f.addRow("", label(tr("set.polite_hint"), "Small", wrap=True))
        f.addRow("", self._check(tr("set.skip_downloaded"), "skip_downloaded"))
        self.clip = self._check(tr("set.clipboard"), "clipboard_monitor")
        f.addRow("", self.clip)
        f.addRow("", self._check(tr("set.clipboard_auto"), "clipboard_auto_add"))
        if paths.IS_MAC:
            f.addRow("", label(tr("set.clipboard_mac_note"), "Small", wrap=True))
        f.addRow("", self._check(tr("set.notifications"), "notifications"))
        f.addRow("", self._check(tr("set.resume_queue"), "resume_queue_on_start"))
        self.cite = QComboBox()
        for k in ("apa", "mla", "chicago", "ja"):
            self.cite.addItem(tr(f"set.cite_{k}"), k)
        self.cite.setCurrentIndex(max(self.cite.findData(s.get("citation_style")), 0))
        self.cite.currentIndexChanged.connect(lambda *_: ctx.set("citation_style", self.cite.currentData()))
        f.addRow(tr("set.citation_style"), _row(self.cite))

        # -- Account ----------------------------------------------------------------------------------
        g = self._group("account", tr("set.account"), lay)
        v = QVBoxLayout(g)
        warn = label(tr("set.auth_warning"), "Warn", wrap=True)
        v.addWidget(warn)
        self.auth_group = QButtonGroup(self)
        modes = ["guest", "internal", "firefox"] + (["chrome", "safari"] if paths.IS_MAC else []) + ["cookiefile"]
        for m in modes:
            rb = QRadioButton(tr(f"set.auth_{m}"))
            rb.setProperty("mode", m)
            rb.setChecked(s.get("auth_mode") == m)
            self.auth_group.addButton(rb)
            v.addWidget(rb)
            if m == "internal":
                self.internal_status = label("", "Small")
                bl = QPushButton(tr("set.internal_login"))
                bl.clicked.connect(self._login)
                bo = QPushButton(tr("set.internal_logout"))
                bo.setObjectName("Danger")
                bo.clicked.connect(self._logout)
                v.addWidget(_row(QLabel("   "), bl, bo, self.internal_status))
            if m == "safari":
                v.addWidget(label("      " + tr("set.auth_safari_note"), "Small", wrap=True))
            if m == "cookiefile":
                self.cookie_path = ElidedLabel(s.get("cookie_file") or "—")
                bc = QPushButton(tr("common.browse"))
                bc.clicked.connect(self._choose_cookie)
                v.addWidget(_row(QLabel("   "), bc, self.cookie_path, stretch_last=True))
        if paths.IS_WINDOWS:
            v.addWidget(label(tr("set.auth_win_chrome"), "Small", wrap=True))
        self.auth_group.buttonToggled.connect(self._auth_changed)

        # -- Engine -----------------------------------------------------------------------------------
        g = self._group("engine", tr("set.engine"), lay)
        f = QFormLayout(g)
        self.ytdlp_label = label("", wrap=True)
        self.b_update = QPushButton(tr("set.check_update"))
        self.b_update.clicked.connect(self._update_now)
        f.addRow(tr("set.ytdlp"), _row(self.ytdlp_label, self.b_update))
        self.channel = QComboBox()
        self.channel.addItem(tr("set.channel_stable"), "stable")
        self.channel.addItem(tr("set.channel_nightly"), "nightly")
        self.channel.setCurrentIndex(max(self.channel.findData(s.get("ytdlp_channel")), 0))
        self.channel.currentIndexChanged.connect(lambda *_: ctx.set("ytdlp_channel", self.channel.currentData()))
        f.addRow(tr("set.ytdlp_channel"), _row(self.channel))
        f.addRow("", self._check(tr("set.auto_update"), "auto_update"))
        self.js_label = label("", wrap=True)
        self.b_js = QPushButton(tr("set.js_install"))
        self.b_js.clicked.connect(self._install_deno)
        f.addRow(tr("set.js_runtime"), _row(self.js_label, self.b_js))
        self.ff_label = label("", wrap=True)
        b1 = QPushButton(tr("set.ffmpeg_setup"))
        b1.clicked.connect(lambda: FfmpegSetupDialog(ctx, self).exec())
        b2 = QPushButton(tr("set.ffmpeg_auto"))
        b2.clicked.connect(lambda: (ctx.set("ffmpeg_path", ""), ctx.refresh_tools(True)))
        f.addRow(tr("set.ffmpeg"), self.ff_label)
        f.addRow("", _row(b1, b2))
        self.gpu = self._check(tr("set.gpu_transcode"), "gpu_transcode")
        f.addRow("", self.gpu)
        self.enc_label = label(tr("set.gpu_detecting"), "Small", wrap=True)
        f.addRow("", self.enc_label)
        self.enc = QComboBox()
        self.enc.currentIndexChanged.connect(self._encoder_changed)
        f.addRow(tr("set.encoder_override"), _row(self.enc))
        self.wgpu = self._check(tr("set.webengine_gpu"), "webengine_gpu")
        f.addRow("", _row(self.wgpu, label(tr("common.restart_needed"), "Small")))

        # -- Presets ------------------------------------------------------------------------------------
        g = self._group("presets", tr("set.presets"), lay)
        v = QVBoxLayout(g)
        v.addWidget(label(tr("set.presets_hint"), "Muted", wrap=True))
        self.preset_list = QListWidget()
        self.preset_list.setMaximumHeight(150)
        self.preset_list.itemDoubleClicked.connect(lambda *_: self._edit_preset())
        v.addWidget(self.preset_list)
        bn = QPushButton(tr("set.preset_new"))
        THEME.bind_icon(bn, "ph.plus")
        bn.clicked.connect(lambda: edit_custom_preset(ctx, None, self))
        be = QPushButton(tr("common.edit"))
        be.clicked.connect(self._edit_preset)
        bd = QPushButton(tr("set.preset_dup"))
        bd.clicked.connect(self._dup_preset)
        bx = QPushButton(tr("common.delete"))
        bx.setObjectName("Danger")
        bx.clicked.connect(self._delete_preset)
        v.addWidget(_row(bn, be, bd, bx))

        # -- Data ----------------------------------------------------------------------------------------
        g = self._group("data", tr("set.data"), lay)
        f = QFormLayout(g)
        f.addRow(tr("set.data_dir"), label(str(paths.HOME), "Muted", wrap=True, selectable=True))
        b_open = QPushButton(tr("set.open_data_dir"))
        b_open.clicked.connect(lambda: paths.open_path(paths.HOME))
        b_logs = QPushButton(tr("set.open_logs"))
        b_logs.clicked.connect(lambda: paths.open_path(paths.LOG_DIR))
        b_imp = QPushButton(tr("set.import_v2"))
        b_imp.clicked.connect(lambda: import_v2_dialog(ctx, self))
        b_cache = QPushButton(tr("set.clear_web_cache"))
        b_cache.clicked.connect(self._clear_cache)
        f.addRow("", _row(b_open, b_logs, b_imp, b_cache))

        # -- About ---------------------------------------------------------------------------------------
        g = self._group("about", tr("set.about"), lay)
        v = QVBoxLayout(g)
        v.addWidget(label(tr("set.about_body", ver=__version__, date=__release_date__, author=__author__),
                          selectable=True))
        self.versions = label("", "Small", wrap=True, selectable=True)
        v.addWidget(self.versions)
        br = QPushButton(tr("set.open_repo"))
        THEME.bind_icon(br, "ph.github-logo")
        br.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(__repo__)))
        v.addWidget(br, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)

        ctx.tools_changed.connect(self._tools_changed)
        ctx.auth_changed.connect(self._auth_status)
        ctx.presets_changed.connect(self._fill_presets)
        ctx.settings_changed.connect(self._setting_changed)
        ctx.update_state.connect(self._update_state)
        self._tools_changed()
        self._auth_status()
        self._fill_presets()

    # -- helpers -----------------------------------------------------------------------------------
    def _group(self, key: str, title: str, lay: QVBoxLayout) -> QGroupBox:
        g = QGroupBox(title)
        self.sections[key] = g
        lay.addWidget(g)
        return g

    def _check(self, text: str, key: str) -> QCheckBox:
        c = QCheckBox(text)
        c.setChecked(bool(self.ctx.settings.get(key)))
        c.toggled.connect(lambda v, k=key: self.ctx.set(k, v))
        return c

    def scroll_to(self, key: str) -> None:
        g = self.sections.get(key)
        if g is None:
            return
        area = self.findChild(QScrollArea)
        if area:
            area.ensureWidgetVisible(g, 0, 20)

    def _setting_changed(self, key: str) -> None:
        if key in ("save_dir", "recent_save_dirs"):
            self.save_dir.setText(self.ctx.settings.save_dir())
        elif key == "default_preset":
            fill_preset_combo(self.def_preset, self.ctx, self.ctx.settings.get("default_preset"))
        elif key == "theme":
            self.theme.setCurrentIndex(max(self.theme.findData(self.ctx.settings.get("theme")), 0))

    # -- general ----------------------------------------------------------------------------------
    def _lang_changed(self) -> None:
        self.ctx.set("language", self.lang.currentData())
        self.lang_note.setText(tr("common.restart_needed"))

    def _theme_changed(self) -> None:
        mode = self.theme.currentData()
        self.ctx.settings.set("theme", mode)
        THEME.apply(mode)

    def _choose_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("common.choose_folder"), self.ctx.settings.save_dir())
        if d:
            self.ctx.settings.remember_save_dir(d)
            self.ctx.settings_changed.emit("save_dir")

    def _tmpl_changed(self) -> None:
        text = self.tmpl.text().strip() or FILENAME_TEMPLATE_DEFAULT
        self.ctx.set("filename_template", text)
        sample = {"upload_date": "2026-11-05", "title": "授業研究の実際 Lesson Study", "id": "dQw4w9WgXcQ",
                  "channel": "Edu Channel", "height": "1080"}
        lit = {"dl_date": "2026-11-06", "preset": "1080p"}
        self.tmpl_preview.setText(tr("set.filename_preview", name=naming.preview_name(text, sample, lit) + ".mp4"))

    # -- account ----------------------------------------------------------------------------------
    def _auth_changed(self, button, checked: bool) -> None:
        if not checked:
            return
        self.ctx.set("auth_mode", button.property("mode"))
        self.ctx.auth_changed.emit()

    def _auth_status(self) -> None:
        self.internal_status.setText(tr("set.internal_status_in") if self.ctx.internal_logged_in()
                                     else tr("set.internal_status_out"))

    def _login(self) -> None:
        for b in self.auth_group.buttons():
            if b.property("mode") == "internal":
                b.setChecked(True)
        self.main.open_login()

    def _logout(self) -> None:
        if confirm(self, tr("set.internal_logout"), tr("set.logout_confirm")):
            self.main.logout_internal()

    def _choose_cookie(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "cookies.txt", "", "cookies (*.txt);;* (*)")
        if path:
            self.ctx.set("cookie_file", path)
            self.cookie_path.setText(path)
            for b in self.auth_group.buttons():
                if b.property("mode") == "cookiefile":
                    b.setChecked(True)

    # -- engine -------------------------------------------------------------------------------------
    def _tools_changed(self) -> None:
        c = self.ctx
        ver = c.ytdlp_version or "?"
        text = f"{ver} (ejs {updater.tools.package_version('yt-dlp-ejs') or '-'})"
        if c.update_available and c.update_latest:
            text += "\n" + tr("set.update_available", ver=c.update_latest)
        self.ytdlp_label.setText(text)
        if c.deno.ok:
            self.js_label.setText("✓ " + tr("set.js_ok", ver=c.deno.version or "?"))
            self.b_js.setVisible(False)
        else:
            self.js_label.setText("✕ " + tr("set.js_missing"))
            self.b_js.setVisible(True)
        ff = c.ffmpeg
        if ff.ok:
            src = tr(f"set.src_{ff.source}") if f"set.src_{ff.source}" in i18n.STRINGS else ff.source
            self.ff_label.setText("✓ " + tr("set.ffmpeg_ok", ver=ff.version, source=src) + f"\n{ff.ffmpeg}")
        else:
            self.ff_label.setText("✕ " + tr("set.ffmpeg_missing"))
        if not c.tools_ready:
            self.enc_label.setText(tr("set.gpu_detecting"))
        else:
            hw = sorted(set(c.encoders.get("h264", []) + c.encoders.get("hevc", [])))
            self.enc_label.setText(tr("set.gpu_found", list=", ".join(hw)) if hw else tr("set.gpu_none"))
        self.enc.blockSignals(True)
        self.enc.clear()
        self.enc.addItem(tr("set.encoder_auto"), "auto")
        self.enc.addItem(tr("set.encoder_cpu"), "cpu")
        for e in sorted(set(c.encoders.get("h264", []) + c.encoders.get("hevc", []))):
            self.enc.addItem(e, e)
        self.enc.setCurrentIndex(max(self.enc.findData(c.settings.get("encoder_override", "auto")), 0))
        self.enc.blockSignals(False)
        lines = [f"{k}: {v}" for k, v in c.versions().items() if k not in ("time", "auth")]
        self.versions.setText(" · ".join(lines))

    def _encoder_changed(self) -> None:
        self.ctx.set("encoder_override", self.enc.currentData() or "auto")

    def _update_state(self, state: str) -> None:
        if state == "latest":
            self.ytdlp_label.setText(f"{self.ctx.ytdlp_version} — " + tr("set.update_latest", ver=self.ctx.update_latest))
        elif state == "offline":
            self.ytdlp_label.setText(f"{self.ctx.ytdlp_version} — " + tr("set.update_offline"))

    def _update_now(self) -> None:
        self.main.update_engine()

    def _install_deno(self) -> None:
        self.b_js.setEnabled(False)
        self.js_label.setText(tr("set.update_running"))
        self.ctx.run_bg(lambda log, *_: updater.install_deno(log),
                        lambda ok: (self.b_js.setEnabled(True), self.ctx.refresh_tools()),
                        lambda msg: (self.b_js.setEnabled(True), self.js_label.setText(msg)))

    # -- presets ---------------------------------------------------------------------------------------
    def _fill_presets(self) -> None:
        self.preset_list.clear()
        for p in self.ctx.presets():
            if p.builtin:
                continue
            it = QListWidgetItem(THEME.icon("ph.music-notes" if p.is_audio else "ph.film-strip", "muted"),
                                 f"{p.name}  —  {self.ctx.preset_description(p)}")
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            self.preset_list.addItem(it)

    def _current_custom(self) -> Preset | None:
        it = self.preset_list.currentItem()
        if not it:
            return None
        return self.ctx.preset(it.data(Qt.ItemDataRole.UserRole))

    def _edit_preset(self) -> None:
        p = self._current_custom()
        if p:
            edit_custom_preset(self.ctx, p, self)

    def _dup_preset(self) -> None:
        p = self._current_custom()
        if p:
            copy = new_custom_preset(p, p.name + " 2")
            customs = list(self.ctx.settings.get("custom_presets", [])) + [copy.to_dict()]
            self.ctx.set("custom_presets", customs)
            self.ctx.presets_changed.emit()

    def _delete_preset(self) -> None:
        p = self._current_custom()
        if p and confirm(self, tr("common.delete"), p.name):
            customs = [c for c in self.ctx.settings.get("custom_presets", []) if c.get("id") != p.id]
            self.ctx.set("custom_presets", customs)
            if self.ctx.settings.get("default_preset") == p.id:
                self.ctx.set("default_preset", "edit1080")
            self.ctx.presets_changed.emit()

    # -- data --------------------------------------------------------------------------------------------
    def _clear_cache(self) -> None:
        self.main.clear_web_cache()
        shutil.rmtree(paths.CACHE_DIR / "yt-dlp", ignore_errors=True)
        self.ctx.notify(tr("set.cache_cleared"), "success")
