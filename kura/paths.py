"""Filesystem locations.

Kura keeps everything that belongs to *this computer* (settings, library,
logs, the Python environment, helper binaries, the browser profile and the
download work area) in a local per-user folder, never inside the app folder.
The app folder may live on Google Drive and be shared between a Mac and a
Windows PC, so nothing machine-specific may be written there.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not (IS_WINDOWS or IS_MAC)

# Folder that contains the ``kura`` package (the app/version folder).
APP_DIR = Path(__file__).resolve().parent.parent


def _kura_home() -> Path:
    env = os.environ.get("KURA_HOME")
    if env:
        return Path(env).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Kura"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Kura"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "kura"


HOME = _kura_home()
CONFIG_FILE = HOME / "config.json"
DB_FILE = HOME / "library.db"
WORK_DIR = HOME / "work"
LOG_DIR = HOME / "logs"
THUMB_DIR = HOME / "thumbs"
CACHE_DIR = HOME / "cache"
BIN_DIR = HOME / "bin"
WEB_DIR = HOME / "web"


def ensure_dirs() -> None:
    for d in (HOME, WORK_DIR, LOG_DIR, THUMB_DIR, CACHE_DIR, BIN_DIR, WEB_DIR):
        d.mkdir(parents=True, exist_ok=True)


def default_save_dir() -> Path:
    if IS_MAC:
        return Path.home() / "Movies" / "Kura"
    return Path.home() / "Videos" / "Kura"


def exe_name(name: str) -> str:
    return f"{name}.exe" if IS_WINDOWS else name


# --------------------------------------------------------------------------
# Opening files / folders in the OS
# --------------------------------------------------------------------------

def _popen_detached(args: list[str]) -> None:
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


def native_path(path: str | os.PathLike) -> str:
    """Clean path with this OS's separators.

    Qt's file dialogs return ``C:/Users/...`` on Windows; joined with
    ``os.path.join`` that becomes ``C:/Users/...\\file.mp4``, which Explorer
    does not understand.
    """
    return os.path.normpath(str(path)) if str(path) else ""


def windows_select_command(path: str) -> str:
    """Explorer command line that opens a folder with ``path`` selected.

    It must be passed to ``Popen`` as ONE string: with an argument list
    Python quotes the whole ``/select,…`` argument when the path contains a
    space, Explorer cannot parse that and opens "Documents" instead.
    """
    import ntpath

    return f'explorer /select,"{ntpath.normpath(path)}"'


def open_path(path: str | os.PathLike) -> None:
    """Open a file or folder with the default application."""
    p = native_path(path)
    if not p:
        return
    if IS_WINDOWS:
        os.startfile(p)  # type: ignore[attr-defined]  # noqa: S606
    elif IS_MAC:
        _popen_detached(["open", p])
    else:
        _popen_detached(["xdg-open", p])


def reveal_in_file_manager(path: str | os.PathLike) -> None:
    """Show a file selected in Explorer / Finder (falls back to its folder)."""
    p = native_path(path)
    if not p:
        return
    if not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent and os.path.isdir(parent):
            open_path(parent)
        return
    if os.path.isdir(p):
        open_path(p)
    elif IS_WINDOWS:
        subprocess.Popen(windows_select_command(p))  # noqa: S602,S603 - see windows_select_command
    elif IS_MAC:
        _popen_detached(["open", "-R", p])
    else:
        open_path(os.path.dirname(p))


# --------------------------------------------------------------------------
# Google Drive path translation (Windows <-> macOS)
# --------------------------------------------------------------------------

_DRIVE_ROOT_NAMES = ("マイドライブ", "My Drive")


def google_drive_roots() -> list[Path]:
    """Local folders that correspond to the root of "My Drive"."""
    roots: list[Path] = []
    if IS_MAC:
        for name in _DRIVE_ROOT_NAMES:
            roots += [Path(p) for p in glob.glob(str(Path.home() / "Library" / "CloudStorage" / "GoogleDrive-*" / name))]
    elif IS_WINDOWS:
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            for name in _DRIVE_ROOT_NAMES:
                p = Path(f"{letter}:/") / name
                try:
                    if p.is_dir():
                        roots.append(p)
                except OSError:
                    pass
    else:
        for name in _DRIVE_ROOT_NAMES:
            p = Path.home() / "GoogleDrive" / name
            if p.is_dir():
                roots.append(p)
    return roots


def translate_drive_path(path: str) -> str | None:
    """Map a "My Drive" path written on another OS to this computer.

    ``G:/マイドライブ/Videos`` (Windows) becomes
    ``~/Library/CloudStorage/GoogleDrive-…/マイドライブ/Videos`` on a Mac and
    vice versa.  Returns ``None`` when the path is not inside My Drive or no
    local Google Drive folder exists.
    """
    norm = path.replace("\\", "/")
    for name in _DRIVE_ROOT_NAMES:
        marker = f"/{name}/"
        idx = norm.find(marker)
        if idx >= 0:
            rest = norm[idx + len(marker):]
        elif norm.endswith(f"/{name}"):
            rest = ""
        else:
            continue
        roots = google_drive_roots()
        if not roots:
            return None
        return str(roots[0] / rest) if rest else str(roots[0])
    return None


def usable_dir(path: str) -> str | None:
    """Return ``path`` (or its Drive translation) if it is usable here."""
    if not path:
        return None
    if os.path.isdir(path):
        return path
    translated = translate_drive_path(path)
    if translated and os.path.isdir(translated):
        return translated
    return None


def free_space(path: str | os.PathLike) -> int | None:
    p = Path(path)
    while not p.exists():
        if p.parent == p:
            return None
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None
