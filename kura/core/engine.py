"""The download engine: one :class:`DownloadJob` = one video → final files.

This module is Qt-free so it can be tested headless and used from the CLI.
yt-dlp is imported lazily so that the launcher can update it before use.
"""

from __future__ import annotations

import collections
import copy
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from . import errors, metadata, naming
from .cookies import CookieRecord, inject, relevant_for
from .presets import Preset, SubtitleOptions
from .transcode import ProcessHolder, probe

FILENAME_TEMPLATE_DEFAULT = "{upload_date}_{title}_[{id}]"
MEDIA_EXTS = {"mp4", "mkv", "webm", "m4a", "mp3", "wav", "opus", "ogg", "flac", "mov", "aac", "flv", "avi",
              "ts", "m4v", "3gp", "mka"}


class JobCancelled(Exception):
    def __init__(self, paused: bool = False):
        super().__init__("paused" if paused else "canceled")
        self.paused = paused


class JobFailed(Exception):
    def __init__(self, message: str, kind: str = "unknown"):
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass
class ToolConfig:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    deno: str | None = None
    encoders: dict = field(default_factory=dict)
    use_gpu: bool = True
    encoder_override: str = "auto"
    cachedir: str | None = None

    def js_runtimes(self) -> dict:
        return {"deno": {"path": self.deno}} if self.deno else {"deno": {}}


@dataclass
class AuthConfig:
    mode: str = "guest"   # guest | internal | firefox | chrome | safari | cookiefile
    cookies: list[CookieRecord] = field(default_factory=list)
    cookie_file: str = ""

    def apply_params(self, params: dict) -> None:
        if self.mode in ("firefox", "chrome", "safari", "edge", "brave", "chromium"):
            params["cookiesfrombrowser"] = (self.mode,)
        elif self.mode == "cookiefile" and self.cookie_file and os.path.isfile(self.cookie_file):
            params["cookiefile"] = self.cookie_file

    def apply_jar(self, ydl, url: str) -> None:
        if self.mode == "internal" and self.cookies:
            inject(ydl.cookiejar, relevant_for(self.cookies, url))


@dataclass
class JobSpec:
    url: str
    preset: dict
    dest_dir: str
    work_dir: str
    name_template: str = FILENAME_TEMPLATE_DEFAULT
    literals: dict = field(default_factory=dict)
    subtitles: dict | None = None          # SubtitleOptions.to_dict()
    clip: dict | None = None               # {"start": s, "end": s, "chapters": [...], "precise": bool}
    format_override: str | None = None     # e.g. "137+140"
    container_override: str | None = None  # mp4 | mkv (with format_override)
    write_info_json: bool = False
    subfolder: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobSpec":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class JobResult:
    files: list[str]
    extra_files: list[str]
    meta: dict
    actual: dict
    encoder: str | None
    warnings: list[str]
    thumbnail_url: str | None


class JobListener:
    """Callbacks (called from the job's thread)."""

    def phase(self, phase: str, detail: str = "") -> None: ...
    def progress(self, data: dict) -> None: ...
    def log(self, line: str) -> None: ...
    def info(self, summary: dict) -> None: ...


class _YdlLogger:
    def __init__(self, job: "DownloadJob"):
        self.job = job

    def _check_cancel(self) -> None:
        # Makes pause/cancel responsive during extraction, which has no progress hooks.
        if self.job._interruptible and self.job._cancel.is_set():
            from yt_dlp.utils import DownloadCancelled

            raise DownloadCancelled("Canceled by user")

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            self.job._log(msg)
        self._check_cancel()

    def info(self, msg: str) -> None:
        self.job._log(msg)
        self._check_cancel()

    def warning(self, msg: str) -> None:
        self.job._log("WARNING: " + msg)
        self.job.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.job._log(msg)
        self.job.last_error = msg


class _CollectLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self.messages.append(msg)

    def error(self, msg: str) -> None:
        self.messages.append(msg)


def pick_thumbnail(info: dict) -> str | None:
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    jpgs = [t for t in thumbs if t["url"].split("?")[0].lower().endswith((".jpg", ".jpeg"))]
    pool = jpgs or thumbs
    if pool:
        def score(t: dict) -> tuple:
            w = t.get("width") or 0
            return (0 < w <= 720, w, t.get("preference") or 0)

        return max(pool, key=score)["url"]
    return info.get("thumbnail")


def info_summary(info: dict) -> dict:
    keys = ("id", "title", "channel", "uploader", "duration", "webpage_url", "extractor_key", "width",
            "height", "fps", "vcodec", "acodec", "upload_date", "live_status", "playlist_title")
    out = {k: info.get(k) for k in keys if info.get(k) is not None}
    out["thumbnail"] = pick_thumbnail(info)
    if not out.get("channel") and info.get("uploader"):
        out["channel"] = info.get("uploader")
    return out


class DownloadJob:
    def __init__(self, spec: JobSpec, tools: ToolConfig, auth: AuthConfig | None = None,
                 listener: JobListener | None = None):
        self.spec = spec
        self.tools = tools
        self.auth = auth or AuthConfig()
        self.listener = listener or JobListener()
        self.preset = Preset.from_dict(spec.preset)
        self.subtitle_options = SubtitleOptions(**spec.subtitles) if spec.subtitles else None
        self._cancel = threading.Event()
        self._paused = False
        self._proc_holder = ProcessHolder()
        self.warnings: list[str] = []
        self.last_error = ""
        self.extra_outputs: list[str] = []
        self.subtitle_files: dict[str, str] = {}
        self.encoder_used: str | None = None
        self.log_lines: collections.deque[str] = collections.deque(maxlen=500)
        self._parts: dict[str, dict] = {}
        self._expected: list[tuple[str, int | None]] = []
        self._phase = "queued"
        self._last_emit = 0.0
        self._summary: dict = {}
        self._interruptible = False

    # -- control -------------------------------------------------------------
    def cancel(self, pause: bool = False) -> None:
        self._paused = pause
        self._cancel.set()
        self._proc_holder.kill()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- internal callbacks ----------------------------------------------------
    def _log(self, line: str) -> None:
        line = re.sub(r"\x1b\[[0-9;]*m", "", str(line))
        self.log_lines.append(line)
        self.listener.log(line)

    def _set_phase(self, phase: str, detail: str = "") -> None:
        self._phase = phase
        self.listener.phase(phase, detail)

    def _raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            from yt_dlp.utils import DownloadCancelled

            raise DownloadCancelled("Canceled by user")

    def _on_prepared(self, info: dict) -> None:
        requested = info.get("requested_formats") or [info]
        self._expected = [(f.get("format_id") or "?", f.get("filesize") or f.get("filesize_approx"))
                          for f in requested]
        self._parts = {}
        self._summary = info_summary(info)
        self.listener.info(dict(self._summary))

    def _on_convert_progress(self, frac: float, label: str) -> None:
        self.listener.progress({"stage": "convert", "fraction": frac, "label": label})

    def _progress_hook(self, d: dict) -> None:
        self._raise_if_cancelled()
        status = d.get("status")
        info = d.get("info_dict") or {}
        key = str(info.get("format_id") or d.get("filename") or "?")
        if status == "downloading":
            if self._phase != "downloading":
                self._set_phase("downloading")
            self._parts[key] = {
                "done": d.get("downloaded_bytes") or 0,
                "total": d.get("total_bytes") or d.get("total_bytes_estimate"),
                "frag": (d.get("fragment_index"), d.get("fragment_count")),
                "finished": False,
            }
            now = time.monotonic()
            if now - self._last_emit < 0.25:
                return
            self._last_emit = now
            self.listener.progress(self._overall(d))
        elif status == "finished":
            part = self._parts.get(key, {})
            total = d.get("total_bytes") or d.get("downloaded_bytes") or part.get("total") or part.get("done") or 0
            self._parts[key] = {"done": total, "total": total, "frag": (None, None), "finished": True}
            self.listener.progress(self._overall(d))

    def _overall(self, d: dict) -> dict:
        keys = [k for k, _ in self._expected] or list(self._parts.keys())
        for k in self._parts:
            if k not in keys:
                keys.append(k)
        sizes = dict(self._expected)
        fracs: list[float | None] = []
        weights: list[float] = []
        done_bytes = 0
        for k in keys:
            p = self._parts.get(k)
            size = sizes.get(k) or (p or {}).get("total")
            weights.append(float(size) if size else 0.0)
            if not p:
                fracs.append(0.0)
                continue
            done_bytes += p.get("done") or 0
            if p.get("finished"):
                fracs.append(1.0)
            elif p.get("total"):
                fracs.append(min((p.get("done") or 0) / p["total"], 1.0))
            elif p.get("frag") and p["frag"][1]:
                fracs.append(min((p["frag"][0] or 0) / p["frag"][1], 1.0))
            else:
                fracs.append(None)
        fraction: float | None
        if any(f is None for f in fracs):
            fraction = None
        elif weights and all(w > 0 for w in weights):
            fraction = sum(f * w for f, w in zip(fracs, weights)) / sum(weights)  # type: ignore[operator]
        else:
            fraction = sum(fracs) / len(fracs) if fracs else None  # type: ignore[arg-type]
        total_bytes = int(sum(weights)) if weights and all(w > 0 for w in weights) else None
        speed = d.get("speed")
        eta = d.get("eta")
        if total_bytes and speed:
            eta = max(total_bytes - done_bytes, 0) / speed
        return {"stage": "download", "fraction": fraction, "downloaded": done_bytes, "total": total_bytes,
                "speed": speed, "eta": eta}

    def _pp_hook(self, d: dict) -> None:
        self._raise_if_cancelled()
        if d.get("status") != "started":
            return
        name = d.get("postprocessor") or ""
        if name in ("KuraPrep", "KuraSubtitle", "KuraTranscode", "MoveFiles"):
            return
        self._set_phase("processing", name)

    # -- yt-dlp configuration --------------------------------------------------
    def _build_params(self) -> dict:
        p, s = self.preset, self.spec
        ffmpeg_ok = bool(self.tools.ffmpeg)
        if s.format_override:
            fmt, sort, merge = s.format_override, [], ("mkv" if ffmpeg_ok else None)
        else:
            fmt, sort, merge = p.format_selection(ffmpeg_ok)
        if not ffmpeg_ok:
            self.warnings.append("ffmpeg-missing")

        suffix = ""
        if s.clip:
            if s.clip.get("chapters"):
                suffix = "_%(section_title).40B"
            else:
                suffix = "_[%(section_start>%H-%M-%S|0)s-%(section_end>%H-%M-%S|end)s]"
        name = naming.build_outtmpl(s.name_template or FILENAME_TEMPLATE_DEFAULT, s.literals, suffix)
        outtmpl = os.path.join(s.work_dir.replace("%", "%%"), name)

        params: dict[str, Any] = {
            "outtmpl": {"default": outtmpl},
            "format": fmt,
            "noplaylist": True,
            "logger": _YdlLogger(self),
            "noprogress": True,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._pp_hook],
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "extractor_retries": 3,
            "socket_timeout": 30,
            "continuedl": True,
            "concurrent_fragment_downloads": 4,
            "windowsfilenames": True,
            "color": {"stdout": "no_color", "stderr": "no_color"},
            "js_runtimes": self.tools.js_runtimes(),
        }
        if sort:
            params["format_sort"] = sort
        if merge:
            params["merge_output_format"] = merge
        if self.tools.ffmpeg:
            params["ffmpeg_location"] = self.tools.ffmpeg
        if self.tools.cachedir:
            params["cachedir"] = self.tools.cachedir
        if ffmpeg_ok and p.embed_thumbnail and p.ext() != "wav":
            params["writethumbnail"] = True

        so = self.subtitle_options
        if so and so.langs:
            params["writesubtitles"] = True
            params["writeautomaticsub"] = so.auto
            params["subtitleslangs"] = list(so.langs)
            params["subtitlesformat"] = "json3/vtt/srt/best"

        if s.clip:
            from yt_dlp.utils import download_range_func

            chapters = [f"^{re.escape(c)}$" for c in (s.clip.get("chapters") or [])]
            ranges = []
            if not chapters:
                start = float(s.clip.get("start") or 0)
                end = s.clip.get("end")
                ranges = [(start, float(end) if end not in (None, "") else float("inf"))]
            params["download_ranges"] = download_range_func(chapters, ranges)
            params["force_keyframes_at_cuts"] = bool(s.clip.get("precise"))

        self.auth.apply_params(params)
        return params

    def _add_postprocessors(self, ydl, params: dict) -> None:
        from yt_dlp.postprocessor import (
            EmbedThumbnailPP,
            FFmpegEmbedSubtitlePP,
            FFmpegExtractAudioPP,
            FFmpegMetadataPP,
        )

        from .postproc import KuraPrepPP, KuraSubtitlePP, KuraTranscodePP

        p, s = self.preset, self.spec
        ydl.add_post_processor(KuraPrepPP(ydl, self), when="before_dl")
        if self.subtitle_options:
            ydl.add_post_processor(KuraSubtitlePP(ydl, self), when="before_dl")
        if not self.tools.ffmpeg:
            return
        if p.is_audio:
            ydl.add_post_processor(
                FFmpegExtractAudioPP(ydl, preferredcodec=p.acodec, preferredquality=p.audio_quality),
                when="post_process")
        else:
            if s.format_override:
                container = s.container_override or "mkv"
                target_v = target_a = "copy"
            else:
                container, target_v, target_a = p.container, p.vcodec, p.acodec
            if target_v != "copy" or target_a != "copy" or container == "mp4":
                ydl.add_post_processor(KuraTranscodePP(ydl, self, target_v, target_a, container),
                                       when="post_process")
            so = self.subtitle_options
            if so and so.embed:
                ydl.add_post_processor(FFmpegEmbedSubtitlePP(ydl, already_have_subtitle=so.keep_files),
                                       when="post_process")
        if p.embed_metadata or p.embed_chapters:
            ydl.add_post_processor(
                FFmpegMetadataPP(ydl, add_metadata=p.embed_metadata,
                                 add_chapters=bool(p.embed_chapters and not s.clip), add_infojson=False),
                when="post_process")
        if params.get("writethumbnail"):
            ydl.add_post_processor(EmbedThumbnailPP(ydl, already_have_thumbnail=False), when="post_process")

    # -- main ----------------------------------------------------------------------
    def run(self) -> JobResult:
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled, DownloadError

        os.makedirs(self.spec.work_dir, exist_ok=True)
        params = self._build_params()
        self._set_phase("extracting")
        try:
            with yt_dlp.YoutubeDL(params) as ydl:
                self.auth.apply_jar(ydl, self.spec.url)
                self._add_postprocessors(ydl, params)
                self._interruptible = True
                try:
                    info = ydl.extract_info(self.spec.url, download=True)
                finally:
                    self._interruptible = False
        except DownloadCancelled:
            raise JobCancelled(self._paused) from None
        except DownloadError as e:
            if self._cancel.is_set():
                raise JobCancelled(self._paused) from None
            msg = errors.clean_message(str(e))
            raise JobFailed(msg, errors.classify(msg)) from None
        except Exception as e:  # noqa: BLE001
            if self._cancel.is_set():
                raise JobCancelled(self._paused) from None
            msg = f"{type(e).__name__}: {e}"
            raise JobFailed(msg, errors.classify(msg)) from None

        if self._cancel.is_set():
            raise JobCancelled(self._paused)
        if not info:
            msg = errors.clean_message(self.last_error) or "No result"
            raise JobFailed(msg, errors.classify(msg))

        mains = self._collect_main_files(info)
        if not mains:
            msg = errors.clean_message(self.last_error) or "Download finished but no media file was produced."
            raise JobFailed(msg, errors.classify(msg))

        self._set_phase("moving")
        result = self._finalize(info, mains)
        shutil.rmtree(self.spec.work_dir, ignore_errors=True)
        self._set_phase("done")
        return result

    def _collect_main_files(self, info: dict) -> list[str]:
        found: list[str] = []

        def visit(i: dict | None) -> None:
            if not i:
                return
            for e in i.get("entries") or []:
                visit(e)
            for rd in i.get("requested_downloads") or []:
                fp = rd.get("filepath")
                if fp and os.path.isfile(fp) and fp not in found:
                    found.append(fp)

        visit(info)
        if not found:
            skip = set(self.subtitle_files.values()) | set(self.extra_outputs)
            for name in sorted(os.listdir(self.spec.work_dir)):
                fp = os.path.join(self.spec.work_dir, name)
                ext = name.rsplit(".", 1)[-1].lower()
                if fp in skip or not os.path.isfile(fp) or ext not in MEDIA_EXTS:
                    continue
                if name.endswith((".part", ".ytdl", ".temp")) or re.search(r"\.f[\w-]+\.\w+$", name):
                    continue  # unfinished or not-yet-merged intermediate files
                found.append(fp)
        return found

    def _finalize(self, info: dict, mains: list[str]) -> JobResult:
        s = self.spec
        dest = s.dest_dir
        if s.subfolder:
            dest = os.path.join(dest, naming.sanitize_component(s.subfolder, 80) or "playlist")
        os.makedirs(dest, exist_ok=True)

        extras: list[str] = []
        so = self.subtitle_options
        if so and (so.keep_files or self.preset.is_audio):
            extras += [p for p in self.subtitle_files.values() if os.path.isfile(p)]
        extras += [p for p in self.extra_outputs if os.path.isfile(p)]

        meta = metadata.summarize(info)
        if s.literals.get("playlist"):
            meta["playlist_title"] = s.literals["playlist"]
        if s.literals.get("index"):
            meta["playlist_index"] = s.literals["index"]
        meta["kura_preset"] = self.preset.id
        if s.write_info_json and mains:
            base0 = os.path.splitext(mains[0])[0]
            jpath = base0 + ".info.json"
            metadata.write_json(meta, jpath)
            extras.append(jpath)

        all_files = mains + extras
        total = sum(os.path.getsize(f) for f in all_files if os.path.isfile(f)) or 1
        moved_before = 0

        def move(src: str, dst: str) -> None:
            nonlocal moved_before
            start = moved_before

            def prog(done: int, _tot: int) -> None:
                self.listener.progress({"stage": "move", "fraction": min((start + done) / total, 1.0)})

            naming.move_file(src, dst, progress=prog, cancelled=self.is_cancelled)
            moved_before += os.path.getsize(dst) if os.path.isfile(dst) else 0

        final_mains: list[str] = []
        final_extras: list[str] = []
        remaining = list(extras)
        try:
            for main in mains:
                base = os.path.splitext(os.path.basename(main))[0]
                ext = os.path.splitext(main)[1]
                related = [x for x in remaining if os.path.basename(x).startswith(base + ".")]
                suffixes = [ext] + [os.path.basename(x)[len(base):] for x in related]
                new_base = naming.fit_base_for_windows(dest, base, max(suffixes, key=len))
                new_base = naming.unique_base(dest, new_base, suffixes)
                target = os.path.join(dest, new_base + ext)
                move(main, target)
                final_mains.append(target)
                for x in related:
                    t = os.path.join(dest, new_base + os.path.basename(x)[len(base):])
                    move(x, t)
                    final_extras.append(t)
                    remaining.remove(x)
            for x in remaining:
                name = os.path.basename(x)
                stem, dot, rest = name.partition(".")
                nb = naming.unique_base(dest, stem, [dot + rest])
                t = os.path.join(dest, nb + dot + rest)
                move(x, t)
                final_extras.append(t)
        except naming.MoveCancelled:
            raise JobCancelled(self._paused) from None

        actual: dict = {}
        if final_mains:
            pr = probe(final_mains[0], self.tools.ffprobe, self.tools.ffmpeg)
            actual = {k: v for k, v in pr.__dict__.items() if v is not None}
        return JobResult(
            files=final_mains, extra_files=final_extras, meta=meta, actual=actual,
            encoder=self.encoder_used, warnings=list(self.warnings),
            thumbnail_url=self._summary.get("thumbnail") or pick_thumbnail(info),
        )


# --------------------------------------------------------------------------
# Information (preview, playlists, formats)
# --------------------------------------------------------------------------

class InfoError(Exception):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.message = message
        self.kind = kind


def fetch_info(url: str, tools: ToolConfig, auth: AuthConfig | None = None, *, flat: bool = False,
               noplaylist: bool = True, playlist_end: int | None = None, timeout: float = 30) -> dict:
    """Extract information without downloading (JSON-serialisable result)."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    auth = auth or AuthConfig()
    logger = _CollectLogger()
    params: dict[str, Any] = {
        "skip_download": True,
        "noplaylist": noplaylist,
        "logger": logger,
        "quiet": True,
        "socket_timeout": timeout,
        "color": {"stdout": "no_color", "stderr": "no_color"},
        "js_runtimes": tools.js_runtimes(),
        "windowsfilenames": True,
    }
    if flat:
        params["extract_flat"] = "in_playlist"
    if playlist_end:
        params["playlistend"] = int(playlist_end)
    if tools.cachedir:
        params["cachedir"] = tools.cachedir
    if tools.ffmpeg:
        params["ffmpeg_location"] = tools.ffmpeg
    auth.apply_params(params)
    try:
        with yt_dlp.YoutubeDL(params) as ydl:
            auth.apply_jar(ydl, url)
            info = ydl.extract_info(url, download=False)
            if not info:
                raise DownloadError("; ".join(logger.messages) or "No information")
            return ydl.sanitize_info(info)
    except DownloadError as e:
        msg = errors.clean_message(str(e))
        raise InfoError(msg, errors.classify(msg)) from None
    except Exception as e:  # noqa: BLE001 - extractor bugs etc.
        msg = f"{type(e).__name__}: {e}"
        raise InfoError(msg, errors.classify(msg)) from None


def select_formats(info: dict, fmt: str, sort: list[str] | None = None) -> list[dict]:
    """Which formats would yt-dlp pick for ``fmt``/``sort`` (no network)."""
    import yt_dlp

    params: dict[str, Any] = {"format": fmt, "quiet": True, "logger": _CollectLogger(), "simulate": True,
                              "check_formats": False}
    if sort:
        params["format_sort"] = sort
    try:
        with yt_dlp.YoutubeDL(params) as ydl:
            res = ydl.process_ie_result(copy.deepcopy(info), download=False)
    except Exception:  # noqa: BLE001
        return []
    if not res:
        return []
    return list(res.get("requested_formats") or [res])


def estimate(info: dict, preset: Preset, ffmpeg_ok: bool) -> tuple[int | None, list[dict]]:
    if not info.get("formats"):
        return None, []
    fmt, sort, _merge = preset.format_selection(ffmpeg_ok)
    chosen = select_formats(info, fmt, sort)
    sizes = [f.get("filesize") or f.get("filesize_approx") for f in chosen]
    size = int(sum(sizes)) if sizes and all(sizes) else None
    return size, chosen


def format_rows(info: dict) -> list[dict]:
    from .transcode import norm_codec

    rows = []
    for f in info.get("formats") or []:
        v = norm_codec(f.get("vcodec"))
        a = norm_codec(f.get("acodec"))
        if f.get("ext") == "mhtml" or (not v and not a and f.get("vcodec") == "none"):
            continue
        kind = "av" if v and a else "v" if v else "a" if a else "?"
        rows.append({
            "id": f.get("format_id"), "ext": f.get("ext"), "kind": kind,
            "width": f.get("width"), "height": f.get("height"), "fps": f.get("fps"),
            "hdr": f.get("dynamic_range"), "vcodec": v, "acodec": a,
            "tbr": f.get("tbr"), "abr": f.get("abr"), "size": f.get("filesize") or f.get("filesize_approx"),
            "note": f.get("format_note") or "", "lang": f.get("language"), "protocol": f.get("protocol"),
        })
    order = {"av": 0, "v": 0, "a": 1, "?": 2}
    rows.sort(key=lambda r: (order[r["kind"]], -(r["height"] or 0), -(r["fps"] or 0), -(r["tbr"] or 0)))
    return rows
