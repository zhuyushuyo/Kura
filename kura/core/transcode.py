"""ffmpeg helpers: probing, encoder choice and conversion with progress."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .tools import CPU_ENCODERS, NO_WINDOW, run_quiet


class TranscodeCancelled(Exception):
    pass


class TranscodeError(Exception):
    pass


_CODEC_ALIASES = {"avc1": "h264", "avc": "h264", "h265": "hevc", "hev1": "hevc", "hvc1": "hevc",
                  "mp4a": "aac", "av01": "av1"}


def norm_codec(name: str | None) -> str | None:
    if not name or name == "none":
        return None
    n = name.lower().split(".")[0]
    return _CODEC_ALIASES.get(n, n)


@dataclass
class Probe:
    vcodec: str | None = None
    acodec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    pix_fmt: str | None = None
    duration: float | None = None
    size: int | None = None


def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate in ("0/0",):
        return None
    try:
        if "/" in rate:
            a, b = rate.split("/")
            return round(float(a) / float(b), 3) if float(b) else None
        return float(rate)
    except ValueError:
        return None


def probe(path: str, ffprobe: str | None, ffmpeg: str | None = None) -> Probe:
    pr = Probe()
    try:
        pr.size = os.path.getsize(path)
    except OSError:
        pass
    if ffprobe:
        try:
            res = run_quiet([ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                            timeout=60)
            data = json.loads(res.stdout or "{}")
            for st in data.get("streams") or []:
                disp = st.get("disposition") or {}
                if st.get("codec_type") == "video" and not disp.get("attached_pic") and pr.vcodec is None:
                    pr.vcodec = norm_codec(st.get("codec_name"))
                    pr.width, pr.height = st.get("width"), st.get("height")
                    pr.fps = _parse_rate(st.get("avg_frame_rate")) or _parse_rate(st.get("r_frame_rate"))
                    pr.pix_fmt = st.get("pix_fmt")
                elif st.get("codec_type") == "audio" and pr.acodec is None:
                    pr.acodec = norm_codec(st.get("codec_name"))
            dur = (data.get("format") or {}).get("duration")
            pr.duration = float(dur) if dur else None
            return pr
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if ffmpeg:  # fallback: parse "ffmpeg -i" output
        try:
            err = run_quiet([ffmpeg, "-hide_banner", "-i", path], timeout=60).stderr
        except (OSError, subprocess.SubprocessError):
            return pr
        m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
        if m:
            pr.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        for line in err.splitlines():
            if "Video:" in line and "attached pic" not in line and pr.vcodec is None:
                mm = re.search(r"Video: (\w+)", line)
                pr.vcodec = norm_codec(mm.group(1)) if mm else None
                ms = re.search(r", (\d{2,5})x(\d{2,5})", line)
                if ms:
                    pr.width, pr.height = int(ms.group(1)), int(ms.group(2))
                mf = re.search(r"([\d.]+) fps", line)
                if mf:
                    pr.fps = float(mf.group(1))
                mp = re.search(r"Video: \w+[^,]*, (\w+)", line)
                pr.pix_fmt = mp.group(1) if mp else None
            elif "Audio:" in line and pr.acodec is None:
                mm = re.search(r"Audio: (\w+)", line)
                pr.acodec = norm_codec(mm.group(1)) if mm else None
    return pr


# --------------------------------------------------------------------------
# Encoder selection
# --------------------------------------------------------------------------

def target_bitrate(codec: str, height: int | None, fps: float | None) -> int:
    """Generous bitrates for an editing-friendly intermediate (bits/s)."""
    h = height or 1080
    table = [(480, 3_000_000), (720, 7_500_000), (1080, 12_000_000), (1440, 24_000_000),
             (2160, 45_000_000), (99999, 80_000_000)]
    br = next(b for limit, b in table if h <= limit)
    if fps and fps > 31:
        br = int(br * 1.5)
    if codec == "hevc":
        br = int(br * 0.6)
    return br


def choose_encoders(codec: str, encoders: dict, use_gpu: bool, override: str = "auto") -> list[str]:
    """Ordered list of encoders to try (hardware first, CPU fallback)."""
    order: list[str] = []
    cpu = CPU_ENCODERS[codec]
    if override and override not in ("auto", "cpu"):
        if override.startswith(codec + "_") or override == cpu:
            order.append(override)
    if use_gpu and override != "cpu":
        order += [e for e in encoders.get(codec, []) if e not in order]
    if cpu not in order and (encoders.get("cpu", {}).get(codec, True)):
        order.append(cpu)
    return order


def encoder_args(enc: str, codec: str, height: int | None, fps: float | None) -> list[str]:
    br = target_bitrate(codec, height, fps)
    rate = ["-b:v", str(br), "-maxrate", str(int(br * 1.5)), "-bufsize", str(br * 2)]
    args: list[str] = ["-c:v", enc]
    if enc.endswith("_videotoolbox"):
        args += rate + ["-allow_sw", "1"]
        args += ["-profile:v", "high" if codec == "h264" else "main"]
    elif enc.endswith("_amf"):
        args += ["-quality", "quality", "-rc", "vbr_peak"] + rate
        args += ["-profile:v", "high" if codec == "h264" else "main"]
    elif enc.endswith("_nvenc"):
        args += ["-preset", "p5", "-tune", "hq", "-rc", "vbr"] + rate
        args += ["-profile:v", "high" if codec == "h264" else "main"]
    elif enc.endswith("_qsv"):
        args += rate + ["-profile:v", "high" if codec == "h264" else "main"]
    elif enc == "libx264":
        args += ["-preset", "medium", "-crf", "18", "-profile:v", "high"]
    elif enc == "libx265":
        args += ["-preset", "medium", "-crf", "20", "-x265-params", "log-level=error"]
    if codec == "hevc":
        args += ["-tag:v", "hvc1"]
    args += ["-pix_fmt", "yuv420p"]
    return args


def is_hardware(enc: str) -> bool:
    return enc not in ("libx264", "libx265")


# --------------------------------------------------------------------------
# Running ffmpeg with progress
# --------------------------------------------------------------------------

ProgressCb = Callable[[float, str], None]


class ProcessHolder:
    """Lets another thread kill the running ffmpeg process."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def kill(self) -> None:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.kill()
                except OSError:
                    pass


def run_ffmpeg(cmd: list[str], duration: float | None, progress: ProgressCb | None,
               cancelled: Callable[[], bool] | None, holder: ProcessHolder | None = None,
               label: str = "") -> tuple[int, str]:
    """Run ffmpeg (with ``-progress pipe:1``) and report a 0..1 fraction."""
    err_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err_file, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    if holder:
        with holder.lock:
            holder.proc = proc
    assert proc.stdout is not None
    last = 0.0
    speed = ""
    try:
        for line in proc.stdout:
            if cancelled and cancelled():
                proc.kill()
                proc.wait()
                raise TranscodeCancelled()
            key, _, val = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and val.strip().lstrip("-").isdigit():
                t = int(val) / 1_000_000
                if duration and progress and time.monotonic() - last > 0.3:
                    last = time.monotonic()
                    progress(max(0.0, min(t / duration, 1.0)), f"{label} {speed}".strip())
            elif key == "speed":
                speed = val.strip()
        rc = proc.wait()
    finally:
        if holder:
            with holder.lock:
                holder.proc = None
    if cancelled and cancelled():
        raise TranscodeCancelled()
    err_file.seek(0)
    err = err_file.read()[-4000:]
    err_file.close()
    return rc, err


def convert_for_editing(src: str, dst: str, *, ffmpeg: str, ffprobe: str | None, target_v: str,
                        target_a: str, container: str, encoders: dict, use_gpu: bool,
                        override: str = "auto", duration_hint: float | None = None,
                        progress: ProgressCb | None = None, cancelled: Callable[[], bool] | None = None,
                        log: Callable[[str], None] | None = None,
                        holder: ProcessHolder | None = None) -> dict:
    """Make ``src`` editing-friendly: H.264/HEVC + AAC in MP4 (or remux only).

    Streams that already match are copied.  Hardware encoders are tried
    first and the CPU encoder is used as a fallback.
    """
    log = log or (lambda _m: None)
    pr = probe(src, ffprobe, ffmpeg)
    duration = pr.duration or duration_hint
    need_v = target_v in ("h264", "hevc") and pr.vcodec is not None and pr.vcodec != target_v
    need_a = target_a == "aac" and pr.acodec is not None and pr.acodec != "aac"
    log(f"[Kura] source: video={pr.vcodec} {pr.width}x{pr.height}@{pr.fps} audio={pr.acodec} "
        f"-> video={'encode ' + target_v if need_v else 'copy'} audio={'encode aac' if need_a else 'copy'}")

    audio_args = ["-c:a", "aac", "-b:a", "256k"] if need_a else ["-c:a", "copy"]
    mux_args = ["-movflags", "+faststart"] if container == "mp4" else []
    if container == "mp4" and not need_v and pr.vcodec == "hevc":
        mux_args += ["-tag:v", "hvc1"]

    attempts = choose_encoders(target_v, encoders, use_gpu, override) if need_v else [None]
    last_err = ""
    for enc in attempts:
        if enc is None:
            vargs = ["-c:v", "copy"]
            label = "remux"
        else:
            vargs = encoder_args(enc, target_v, pr.height, pr.fps)
            label = enc
        pre = ["-hwaccel", "auto"] if (enc and is_hardware(enc)) else []
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *pre, "-i", src,
               "-map", "0:v:0?", "-map", "0:a?", "-map_metadata", "0", "-map_chapters", "0",
               *vargs, *audio_args, *mux_args, "-progress", "pipe:1", "-nostats", dst]
        log(f"[Kura] ffmpeg encoder: {label}")
        rc, err = run_ffmpeg(cmd, duration, progress, cancelled, holder, label=label)
        if rc == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return {"encoder": label, "video_encoded": need_v, "audio_encoded": need_a}
        last_err = err.strip()
        log(f"[Kura] encoder {label} failed: {last_err[-500:]}")
        try:
            os.remove(dst)
        except OSError:
            pass
    raise TranscodeError(f"ffmpeg conversion failed: {last_err[-800:]}")
