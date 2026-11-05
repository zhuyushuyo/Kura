"""Application-wide state shared by all pages."""

from __future__ import annotations

import hashlib
import os
import time

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import i18n, paths
from ..config import Settings
from ..core import tools, updater
from ..core.cookies import CookieRecord, is_logged_in_youtube
from ..core.engine import AuthConfig, ToolConfig
from ..core.library import Database
from ..core.presets import Preset, all_presets, find_preset
from ..core.tools import DenoInfo, FFmpegInfo
from .workers import FuncThread

tr = i18n.tr


class AppContext(QObject):
    tools_changed = pyqtSignal()
    auth_changed = pyqtSignal()
    library_changed = pyqtSignal()
    presets_changed = pyqtSignal()
    settings_changed = pyqtSignal(str)
    thumb_ready = pyqtSignal(str, str)          # key, path
    update_state = pyqtSignal(str)              # "checking" | "available" | "latest" | "offline" | ...

    def __init__(self, settings: Settings, db: Database):
        super().__init__()
        self.settings = settings
        self.db = db
        self.ffmpeg = FFmpegInfo()
        self.deno = DenoInfo()
        self.encoders: dict = {}
        self.tools_ready = False
        self.ytdlp_version = tools.package_version("yt-dlp")
        self.update_latest: str | None = None
        self.update_available = False
        self._cookies: dict[tuple[str, str, str], CookieRecord] = {}
        self._threads: list = []
        self.nam = QNetworkAccessManager(self)
        self._thumb_pending: dict[str, list] = {}
        self.toast = None          # set by the main window: callable(text, kind, ...)
        self.main_window = None

    # -- settings helpers -------------------------------------------------------
    def set(self, key: str, value) -> None:
        self.settings.set(key, value)
        self.settings_changed.emit(key)

    # -- presets ------------------------------------------------------------------
    def presets(self) -> list[Preset]:
        return all_presets(self.settings.get)

    def preset(self, preset_id: str) -> Preset:
        return find_preset(self.settings.get, preset_id)

    def preset_name(self, p: Preset) -> str:
        if p.builtin:
            name = tr(f"preset.{p.id}")
            if p.id == "edit4k":
                name += " · " + ("HEVC" if p.vcodec == "hevc" else "H.264")
            return name
        return p.name or p.id

    def preset_description(self, p: Preset) -> str:
        if p.builtin:
            return tr(f"preset.{p.id}.desc")
        parts = []
        if p.is_audio:
            parts.append(p.acodec.upper())
        else:
            parts.append(f"≤{p.max_res}p" if p.max_res else tr("preset.res_any"))
            parts.append({"copy": tr("preset.vcodec_copy"), "h264": "H.264", "hevc": "HEVC"}[p.vcodec])
            parts.append(p.container.upper())
        return tr("preset.custom_desc", summary=" · ".join(parts))

    # -- tools ------------------------------------------------------------------------
    def tool_config(self) -> ToolConfig:
        return ToolConfig(
            ffmpeg=self.ffmpeg.ffmpeg, ffprobe=self.ffmpeg.ffprobe, deno=self.deno.path,
            encoders=self.encoders, use_gpu=bool(self.settings.get("gpu_transcode", True)),
            encoder_override=self.settings.get("encoder_override", "auto"),
            cachedir=str(paths.CACHE_DIR / "yt-dlp"),
        )

    def refresh_tools(self, redetect_encoders: bool = False) -> None:
        custom = self.settings.get("ffmpeg_path", "")

        def work(log, progress, cancelled):
            ff = tools.find_ffmpeg(custom)
            enc = tools.detect_encoders(ff.ffmpeg, use_cache=not redetect_encoders) if ff.ffmpeg else {}
            deno = tools.find_deno()
            return ff, enc, deno

        def done(res):
            self.ffmpeg, self.encoders, self.deno = res
            self.ffmpeg.encoders = self.encoders
            self.tools_ready = True
            self.ytdlp_version = tools.package_version("yt-dlp")
            self.tools_changed.emit()

        self.run_bg(work, done)

    def run_bg(self, func, on_done=None, on_fail=None, on_log=None) -> FuncThread:
        th = FuncThread(func, self)
        if on_done:
            th.done.connect(on_done)
        if on_fail:
            th.failed.connect(on_fail)
        if on_log:
            th.log.connect(on_log)
        th.finished.connect(lambda th=th: self._threads.remove(th) if th in self._threads else None)
        self._threads.append(th)
        th.start()
        return th

    def check_updates(self) -> None:
        channel = self.settings.get("ytdlp_channel", "stable")
        self.update_state.emit("checking")

        def done(res):
            cur, latest, avail = res
            self.update_latest = latest
            self.update_available = avail
            self.update_state.emit("offline" if latest is None else ("available" if avail else "latest"))
            self.tools_changed.emit()

        self.run_bg(lambda *_: updater.check(channel), done)

    # -- authentication -----------------------------------------------------------------
    def update_cookie(self, rec: CookieRecord, removed: bool = False) -> None:
        key = (rec.domain, rec.path, rec.name)
        before = self.internal_logged_in()
        if removed:
            self._cookies.pop(key, None)
        else:
            self._cookies[key] = rec
        if before != self.internal_logged_in():
            self.auth_changed.emit()

    def clear_cookies(self) -> None:
        self._cookies.clear()
        self.auth_changed.emit()

    def internal_logged_in(self) -> bool:
        return is_logged_in_youtube(list(self._cookies.values()))

    def auth_config(self) -> AuthConfig:
        mode = self.settings.get("auth_mode", "guest")
        if mode in ("chrome", "safari") and not paths.IS_MAC:
            mode = "guest"
        cookies = list(self._cookies.values()) if mode == "internal" else []
        return AuthConfig(mode=mode, cookies=cookies, cookie_file=self.settings.get("cookie_file", ""))

    def auth_label(self) -> str:
        mode = self.settings.get("auth_mode", "guest")
        if mode == "internal":
            return tr("sb.auth_internal_in") if self.internal_logged_in() else tr("sb.auth_internal_out")
        if mode in ("firefox", "chrome", "safari"):
            return tr("sb.auth_browser", browser=mode.capitalize())
        if mode == "cookiefile":
            return tr("sb.auth_cookiefile")
        return tr("sb.auth_guest")

    # -- thumbnails ------------------------------------------------------------------------
    @staticmethod
    def thumb_key(video_id: str | None, url: str) -> str:
        if video_id:
            safe = "".join(ch for ch in video_id if ch.isalnum() or ch in "-_")[:64]
            if safe:
                return safe
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    def thumb_path(self, key: str) -> str:
        return str(paths.THUMB_DIR / f"{key}.jpg")

    def fetch_thumb(self, url: str | None, key: str) -> str | None:
        """Return the cached path now, or download it and emit ``thumb_ready``."""
        path = self.thumb_path(key)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        if not url:
            return None
        if key in self._thumb_pending:
            return None
        self._thumb_pending[key] = []
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self.nam.get(req)

        def finished() -> None:
            self._thumb_pending.pop(key, None)
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = bytes(reply.readAll())
                if data:
                    try:
                        with open(path, "wb") as f:
                            f.write(data)
                        self.thumb_ready.emit(key, path)
                    except OSError:
                        pass
            reply.deleteLater()

        reply.finished.connect(finished)
        return None

    def save_thumb_bytes(self, key: str, data: bytes) -> str | None:
        if not data:
            return None
        path = self.thumb_path(key)
        try:
            with open(path, "wb") as f:
                f.write(data)
            return path
        except OSError:
            return None

    # -- misc ------------------------------------------------------------------------------
    def notify(self, text: str, kind: str = "info", action: str | None = None, callback=None,
               timeout: int = 4500) -> None:
        if self.toast:
            self.toast(text, kind, action, callback, timeout)

    def versions(self) -> dict:
        return {
            "yt-dlp": self.ytdlp_version or "?",
            "yt-dlp-ejs": tools.package_version("yt-dlp-ejs") or "-",
            "Deno": self.deno.version or "-",
            "ffmpeg": self.ffmpeg.version or "-",
            "PyQt6": tools.package_version("PyQt6") or "?",
            "auth": self.settings.get("auth_mode", "guest"),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
