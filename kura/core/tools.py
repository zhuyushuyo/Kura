"""Helper programs: ffmpeg / ffprobe / Deno (JS runtime) and yt-dlp itself."""

from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import paths

NO_WINDOW = 0x08000000 if paths.IS_WINDOWS else 0


def run_quiet(args: list[str], timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
    )


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def http_get(url: str, timeout: float = 15, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Kura", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:  # noqa: S310
        return r.read()


# --------------------------------------------------------------------------
# ffmpeg
# --------------------------------------------------------------------------

@dataclass
class FFmpegInfo:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    version: str = ""
    source: str = ""          # custom | kura | app | path | homebrew
    encoders: dict = field(default_factory=dict)   # {"h264": [...hw...], "hevc": [...], "cpu": {...}}

    @property
    def ok(self) -> bool:
        return bool(self.ffmpeg)


def _candidate_ffmpeg_paths(custom: str = "") -> list[tuple[str, str]]:
    exe = paths.exe_name("ffmpeg")
    cands: list[tuple[str, str]] = []
    if custom:
        p = Path(custom)
        cands.append((str(p / exe) if p.is_dir() else str(p), "custom"))
    cands.append((str(paths.BIN_DIR / exe), "kura"))
    cands.append((str(paths.APP_DIR / "plugin" / exe), "app"))
    found = shutil.which("ffmpeg")
    if found:
        cands.append((found, "path"))
    if paths.IS_MAC:
        cands += [("/opt/homebrew/bin/ffmpeg", "homebrew"), ("/usr/local/bin/ffmpeg", "homebrew")]
    elif paths.IS_LINUX:
        cands.append(("/usr/bin/ffmpeg", "path"))
    return cands


def find_ffmpeg(custom: str = "") -> FFmpegInfo:
    for cand, source in _candidate_ffmpeg_paths(custom):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            info = FFmpegInfo(ffmpeg=cand, source=source)
            probe = os.path.join(os.path.dirname(cand), paths.exe_name("ffprobe"))
            if os.path.isfile(probe):
                info.ffprobe = probe
            else:
                which = shutil.which("ffprobe")
                info.ffprobe = which
            info.version = ffmpeg_version(cand)
            if info.version:
                return info
    return FFmpegInfo()


def ffmpeg_version(ffmpeg: str) -> str:
    try:
        out = run_quiet([ffmpeg, "-hide_banner", "-version"], timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"ffmpeg version (\S+)", out)
    return m.group(1) if m else ""


# -- hardware encoders -----------------------------------------------------

HW_ENCODERS = {
    "h264": ["h264_videotoolbox", "h264_amf", "h264_nvenc", "h264_qsv"],
    "hevc": ["hevc_videotoolbox", "hevc_amf", "hevc_nvenc", "hevc_qsv"],
}
CPU_ENCODERS = {"h264": "libx264", "hevc": "libx265"}


def _listed_encoders(ffmpeg: str) -> set[str]:
    try:
        out = run_quiet([ffmpeg, "-hide_banner", "-encoders"], timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    names = set()
    for line in out.splitlines():
        m = re.match(r"^\s*[VAS][\w.]{5}\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return names


def _encoder_works(ffmpeg: str, enc: str) -> bool:
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi",
            "-i", "color=c=black:s=640x360:r=30:d=0.3", "-frames:v", "5", "-pix_fmt", "yuv420p",
            "-c:v", enc, "-f", "null", "-"]
    try:
        return run_quiet(args, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_encoders(ffmpeg: str, use_cache: bool = True) -> dict:
    """Return the encoders that *actually work* on this machine (cached)."""
    cache_file = paths.CACHE_DIR / "encoders.json"
    try:
        st = os.stat(ffmpeg)
        key = f"{ffmpeg}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        key = ffmpeg
    if use_cache:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return cached["encoders"]
        except (OSError, ValueError, KeyError):
            pass
    listed = _listed_encoders(ffmpeg)
    result: dict = {"cpu": {}}
    for codec, cands in HW_ENCODERS.items():
        result[codec] = [e for e in cands if e in listed and _encoder_works(ffmpeg, e)]
    for codec, enc in CPU_ENCODERS.items():
        result["cpu"][codec] = enc in listed
    result["aac"] = "aac" in listed
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"key": key, "encoders": result}), encoding="utf-8")
    except OSError:
        pass
    return result


# -- installers -------------------------------------------------------------

FFMPEG_WIN_URL = (
    "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)


def download_ffmpeg_windows(log: Callable[[str], None], progress: Callable[[int, int], None] | None = None,
                            cancelled: Callable[[], bool] | None = None) -> str:
    """Download yt-dlp's official FFmpeg build into Kura's bin folder."""
    paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {FFMPEG_WIN_URL}")
    req = urllib.request.Request(FFMPEG_WIN_URL, headers={"User-Agent": "Kura"})
    fd, tmp_zip = tempfile.mkstemp(suffix=".zip", dir=str(paths.CACHE_DIR))
    os.close(fd)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as r, open(tmp_zip, "wb") as f:  # noqa: S310
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled and cancelled():
                    raise RuntimeError("canceled")
                block = r.read(1 << 20)
                if not block:
                    break
                f.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
        log("Extracting ffmpeg.exe / ffprobe.exe …")
        with zipfile.ZipFile(tmp_zip) as z:
            wanted = {"ffmpeg.exe", "ffprobe.exe"}
            for member in z.namelist():
                name = member.rsplit("/", 1)[-1]
                if name in wanted:
                    target = paths.BIN_DIR / name
                    with z.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    wanted.discard(name)
            if "ffmpeg.exe" in wanted:
                raise RuntimeError("ffmpeg.exe not found in archive")
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass
    log("Done.")
    return str(paths.BIN_DIR / "ffmpeg.exe")


def brew_path() -> str | None:
    for p in (shutil.which("brew"), "/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
        if p and os.path.isfile(p):
            return p
    return None


def brew_install_ffmpeg(log: Callable[[str], None], cancelled: Callable[[], bool] | None = None) -> bool:
    brew = brew_path()
    if not brew:
        log("Homebrew not found.")
        return False
    log(f"$ {brew} install ffmpeg")
    env = dict(os.environ, HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_INSTALL_CLEANUP="1")
    proc = subprocess.Popen([brew, "install", "ffmpeg"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=env)
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip())
        if cancelled and cancelled():
            proc.kill()
            return False
    return proc.wait() == 0


def copy_ffmpeg_into_kura(src_ffmpeg: str) -> str:
    """Copy a user-chosen ffmpeg (and sibling ffprobe) into Kura's bin folder."""
    paths.BIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = paths.BIN_DIR / paths.exe_name("ffmpeg")
    shutil.copy2(src_ffmpeg, dst)
    probe = os.path.join(os.path.dirname(src_ffmpeg), paths.exe_name("ffprobe"))
    if os.path.isfile(probe):
        shutil.copy2(probe, paths.BIN_DIR / paths.exe_name("ffprobe"))
    if not paths.IS_WINDOWS:
        for f in paths.BIN_DIR.iterdir():
            if f.name in ("ffmpeg", "ffprobe"):
                f.chmod(0o755)
    return str(dst)


# --------------------------------------------------------------------------
# Deno (JavaScript runtime used by yt-dlp for YouTube)
# --------------------------------------------------------------------------

@dataclass
class DenoInfo:
    path: str | None = None
    version: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.path)


def find_deno() -> DenoInfo:
    cands: list[str] = []
    try:
        from deno import find_deno_bin  # the "deno" PyPI package (official)

        cands.append(find_deno_bin())
    except Exception:  # noqa: BLE001
        pass
    exe = paths.exe_name("deno")
    cands.append(os.path.join(os.path.dirname(sys.executable), exe))
    cands.append(str(paths.BIN_DIR / exe))
    w = shutil.which("deno")
    if w:
        cands.append(w)
    cands.append(str(Path.home() / ".deno" / "bin" / exe))
    for c in cands:
        if c and os.path.isfile(c):
            return DenoInfo(c, deno_version(c))
    return DenoInfo()


def deno_version(path: str) -> str:
    try:
        out = run_quiet([path, "--version"], timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"deno (\S+)", out)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# Python packages
# --------------------------------------------------------------------------

def package_version(dist: str) -> str:
    try:
        from importlib.metadata import version

        return version(dist)
    except Exception:  # noqa: BLE001
        return ""


def uv_path() -> str | None:
    env = os.environ.get("KURA_UV")
    if env and os.path.isfile(env):
        return env
    w = shutil.which("uv")
    if w:
        return w
    for p in (Path.home() / ".local" / "bin" / paths.exe_name("uv"), Path.home() / ".cargo" / "bin" / paths.exe_name("uv")):
        if p.is_file():
            return str(p)
    return None


def pip_install(packages: list[str], log: Callable[[str], None], upgrade: bool = True,
                prerelease: bool = False, timeout: float = 600) -> bool:
    """Install/upgrade packages into the running environment (uv, else pip)."""
    uv = uv_path()
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable]
        if upgrade:
            cmd.append("-U")
        if prerelease:
            cmd += ["--prerelease", "allow"]
        cmd += packages
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
        if upgrade:
            cmd.append("-U")
        if prerelease:
            cmd.append("--pre")
        cmd += packages
    log("$ " + " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                                stdin=subprocess.DEVNULL)
    except OSError as e:
        log(str(e))
        return False
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip())
    try:
        return proc.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        proc.kill()
        return False
