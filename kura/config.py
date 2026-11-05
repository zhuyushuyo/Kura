"""Persistent settings (stored locally, see :mod:`kura.paths`)."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import paths

FILENAME_TEMPLATE_DEFAULT = "{upload_date}_{title}_[{id}]"

DEFAULTS: dict[str, Any] = {
    "schema": 3,
    "first_run_done": False,
    # General
    "language": "auto",            # auto | ja | en
    "theme": "system",             # system | light | dark
    "save_dir": "",                # empty -> paths.default_save_dir()
    "recent_save_dirs": [],
    "filename_template": FILENAME_TEMPLATE_DEFAULT,
    "playlist_subfolder": True,
    "home_url": "https://www.youtube.com",
    # Download
    "default_preset": "edit1080",
    "edit4k_codec": "h264",        # h264 | hevc
    "custom_presets": [],
    "concurrency": 2,
    "polite_delay": True,
    "delay_min": 5,
    "delay_max": 10,
    "clipboard_monitor": not paths.IS_MAC,
    "clipboard_auto_add": False,
    "notifications": True,
    "resume_queue_on_start": False,
    "subtitles": {
        "enabled": False,
        "langs": "ja,en",
        "auto": True,
        "mode": "file",            # file | embed | both
        "txt": True,
        "csv": False,
    },
    "write_info_json": False,
    "citation_style": "apa",      # apa | mla | chicago | ja
    "skip_downloaded": True,
    # Account / authentication
    "auth_mode": "guest",         # guest | internal | firefox | chrome | safari | cookiefile
    "cookie_file": "",
    # Engine
    "ytdlp_channel": "stable",    # stable | nightly
    "auto_update": True,
    "last_update_check": 0,
    "ffmpeg_path": "",            # manual override
    "gpu_transcode": True,
    "encoder_override": "auto",   # auto | cpu | <encoder name>
    "webengine_gpu": paths.IS_MAC,
    # UI state
    "panel_visible": True,
    "window_geometry": "",
    "library_sort": "new",
}


class Settings:
    """Small JSON-backed settings store with defaults and atomic saves."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.CONFIG_FILE
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(stored, dict):
            return
        for key, value in stored.items():
            if key == "subtitles" and isinstance(value, dict):
                merged = copy.deepcopy(DEFAULTS["subtitles"])
                merged.update(value)
                self._data[key] = merged
            else:
                self._data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # -- access --------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if key in DEFAULTS:
            return copy.deepcopy(DEFAULTS[key])
        return default

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        self._data[key] = value
        if save:
            self.save()

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)
        self.save()

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    # -- derived -------------------------------------------------------------
    def save_dir(self) -> str:
        configured = self.get("save_dir")
        usable = paths.usable_dir(configured) if configured else None
        if usable:
            return usable
        return str(paths.default_save_dir())

    def remember_save_dir(self, path: str) -> None:
        recent = [p for p in self.get("recent_save_dirs", []) if p != path]
        recent.insert(0, path)
        self._data["recent_save_dirs"] = recent[:8]
        self._data["save_dir"] = path
        self.save()


# --------------------------------------------------------------------------
# Import settings from v2 (YouTube Downloader Professional 2.x)
# --------------------------------------------------------------------------

_V2_QUALITY = {
    "Premiere Pro Safe (1080p)": "edit1080",
    "Best Resolution (Max Available)": "best",
    "Audio Only": "audio_mp3",
}
_V2_AUTH = {
    "Internal Browser (Recommended)": "internal",
    "Import cookies.txt": "cookiefile",
    "Firefox": "firefox",
    "Chrome": "chrome" if paths.IS_MAC else "guest",
    "Edge": "guest",
    "Guest (No Login)": "guest",
}


def import_v2_settings(settings: Settings, v2_config_path: str) -> list[str]:
    """Copy compatible values from a v2 ``user_config.json``.

    Returns the list of setting names that were imported.
    """
    with open(v2_config_path, encoding="utf-8") as f:
        old = json.load(f)
    applied: list[str] = []
    if not isinstance(old, dict):
        return applied

    path = old.get("download_path")
    if path:
        usable = paths.usable_dir(path)
        if usable:
            settings.remember_save_dir(usable)
            applied.append("save_dir")

    quality = old.get("quality")
    if quality in _V2_QUALITY:
        settings.set("default_preset", _V2_QUALITY[quality], save=False)
        applied.append("default_preset")

    theme = old.get("theme")
    if theme in ("Light", "Dark"):
        settings.set("theme", theme.lower(), save=False)
        applied.append("theme")

    lang = old.get("language")
    if lang in ("English", "Japanese"):
        settings.set("language", "en" if lang == "English" else "ja", save=False)
        applied.append("language")

    auth = old.get("auth_source")
    if auth in _V2_AUTH:
        settings.set("auth_mode", _V2_AUTH[auth], save=False)
        applied.append("auth_mode")

    cookie = old.get("imported_cookie_path")
    if cookie and os.path.isfile(cookie):
        settings.set("cookie_file", cookie, save=False)
        applied.append("cookie_file")

    if isinstance(old.get("gpu_enabled"), bool):
        settings.set("webengine_gpu", old["gpu_enabled"], save=False)
        applied.append("webengine_gpu")

    settings.save()
    return applied
