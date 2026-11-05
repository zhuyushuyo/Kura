"""Custom yt-dlp post-processors used by Kura."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled, PostProcessingError

from . import subtitles as subs
from .transcode import TranscodeCancelled, TranscodeError, convert_for_editing

if TYPE_CHECKING:  # pragma: no cover
    from .engine import DownloadJob


class KuraPrepPP(PostProcessor):
    """``before_dl``: remember what will be downloaded (for progress/UI)."""

    def __init__(self, downloader, job: "DownloadJob"):
        super().__init__(downloader)
        self.job = job

    def run(self, info):
        self.job._on_prepared(info)
        return [], info


class KuraSubtitlePP(PostProcessor):
    """``before_dl``: turn downloaded subtitles into clean SRT (+TXT/CSV)."""

    def __init__(self, downloader, job: "DownloadJob"):
        super().__init__(downloader)
        self.job = job

    def run(self, info):
        opts = self.job.subtitle_options
        requested = info.get("requested_subtitles") or {}
        files_to_move = info.get("__files_to_move") or {}
        for lang, sub in list(requested.items()):
            src = sub.get("filepath")
            if lang == "live_chat" or not src or not os.path.exists(src):
                continue
            ext = sub.get("ext") or src.rsplit(".", 1)[-1]
            try:
                cues = subs.clean_cues(subs.parse_file(src, ext))
            except Exception as e:  # noqa: BLE001
                self.report_warning(f"Could not read subtitles {lang}: {e}")
                continue
            if not cues:
                self.report_warning(f"Subtitles {lang} are empty")
                continue
            suffix = f".{lang}.{ext}"
            base = src[: -len(suffix)] if src.endswith(suffix) else os.path.splitext(src)[0]
            srt_path = f"{base}.{lang}.srt"
            subs.write_text(srt_path, subs.to_srt(cues))
            if opts and opts.txt:
                p = f"{base}.{lang}.txt"
                subs.write_text(p, subs.to_txt(cues))
                self.job.extra_outputs.append(p)
            if opts and opts.csv:
                p = f"{base}.{lang}.csv"
                subs.write_text(p, subs.to_csv(cues), bom=True)
                self.job.extra_outputs.append(p)
            if os.path.abspath(src) != os.path.abspath(srt_path):
                try:
                    os.remove(src)
                except OSError:
                    pass
                final = files_to_move.pop(src, None)
                if final is not None:
                    fbase = final[: -len(suffix)] if final.endswith(suffix) else os.path.splitext(final)[0]
                    files_to_move[srt_path] = f"{fbase}.{lang}.srt"
            sub["filepath"] = srt_path
            sub["ext"] = "srt"
            self.job.subtitle_files[lang] = srt_path
            self.to_screen(f"Subtitles {lang}: {len(cues)} cues -> SRT")
        return [], info


class KuraTranscodePP(PostProcessor):
    """``post_process``: produce an editing-friendly file (H.264/HEVC + AAC MP4)."""

    def __init__(self, downloader, job: "DownloadJob", target_v: str, target_a: str, container: str):
        super().__init__(downloader)
        self.job = job
        self.target_v = target_v
        self.target_a = target_a
        self.container = container

    def run(self, info):
        src = info["filepath"]
        root, _ext = os.path.splitext(src)
        dst = f"{root}.{self.container}"
        out = dst if os.path.abspath(dst) != os.path.abspath(src) else f"{root}.kura-tmp.{self.container}"
        tools = self.job.tools
        self.job._set_phase("converting", "")

        def progress(frac: float, label: str) -> None:
            self.job._on_convert_progress(frac, label)

        try:
            res = convert_for_editing(
                src, out, ffmpeg=tools.ffmpeg, ffprobe=tools.ffprobe, target_v=self.target_v,
                target_a=self.target_a, container=self.container, encoders=tools.encoders,
                use_gpu=tools.use_gpu, override=tools.encoder_override,
                duration_hint=info.get("duration"), progress=progress,
                cancelled=self.job.is_cancelled, log=self.job._log, holder=self.job._proc_holder,
            )
        except TranscodeCancelled as e:
            raise DownloadCancelled("Canceled by user") from e
        except TranscodeError as e:
            raise PostProcessingError(str(e)) from e
        if out != dst:
            os.replace(out, dst)
        self.job.encoder_used = res.get("encoder")
        info["filepath"] = dst
        info["ext"] = self.container
        to_delete = [src] if os.path.abspath(src) != os.path.abspath(dst) else []
        return to_delete, info
