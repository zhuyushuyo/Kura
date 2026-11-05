"""Headless command line: ``python -m kura.core.cli URL [options]``.

Useful for scripting and for testing the engine without the GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

from .. import paths
from ..config import Settings
from . import tools as tools_mod
from .engine import AuthConfig, DownloadJob, JobCancelled, JobFailed, JobListener, JobSpec, ToolConfig
from .presets import SubtitleOptions, find_preset


class _Printer(JobListener):
    def __init__(self) -> None:
        self._last = ""

    def phase(self, phase: str, detail: str = "") -> None:
        print(f"\n== {phase} {detail}".rstrip(), flush=True)

    def progress(self, data: dict) -> None:
        frac = data.get("fraction")
        txt = f"{data.get('stage')}: {frac * 100:5.1f}%" if frac is not None else f"{data.get('stage')}: …"
        if txt != self._last:
            self._last = txt
            print("\r" + txt, end="", flush=True)

    def log(self, line: str) -> None:
        if os.environ.get("KURA_CLI_VERBOSE"):
            print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kura-cli", description="Kura headless downloader")
    ap.add_argument("url")
    ap.add_argument("--preset", default=None, help="edit1080 | edit4k | best | audio_m4a | audio_mp3 | audio_wav")
    ap.add_argument("--out", default=None, help="destination folder")
    ap.add_argument("--subs", default=None, help="subtitle languages, e.g. ja,en")
    ap.add_argument("--sub-mode", default="file", choices=["file", "embed", "both"])
    ap.add_argument("--csv", action="store_true", help="also write transcript CSV")
    ap.add_argument("--clip", default=None, help="START-END in seconds, e.g. 10-75")
    ap.add_argument("--precise", action="store_true")
    ap.add_argument("--info-json", action="store_true")
    ap.add_argument("--template", default=None)
    ap.add_argument("--no-gpu", action="store_true")
    args = ap.parse_args(argv)

    paths.ensure_dirs()
    settings = Settings()
    preset = find_preset(settings.get, args.preset or settings.get("default_preset"))
    ff = tools_mod.find_ffmpeg(settings.get("ffmpeg_path", ""))
    deno = tools_mod.find_deno()
    tcfg = ToolConfig(ffmpeg=ff.ffmpeg, ffprobe=ff.ffprobe, deno=deno.path,
                      encoders=tools_mod.detect_encoders(ff.ffmpeg) if ff.ffmpeg else {},
                      use_gpu=not args.no_gpu and settings.get("gpu_transcode", True),
                      encoder_override=settings.get("encoder_override", "auto"),
                      cachedir=str(paths.CACHE_DIR / "yt-dlp"))
    subs = None
    if args.subs:
        subs = SubtitleOptions(langs=[x for x in args.subs.split(",") if x], mode=args.sub_mode,
                               csv=args.csv).to_dict()
    clip = None
    if args.clip:
        a, _, b = args.clip.partition("-")
        clip = {"start": float(a or 0), "end": float(b) if b else None, "precise": args.precise}
    spec = JobSpec(url=args.url, preset=preset.to_dict(), dest_dir=args.out or settings.save_dir(),
                   work_dir=str(paths.WORK_DIR / ("cli-" + uuid.uuid4().hex[:8])),
                   name_template=args.template or settings.get("filename_template"),
                   literals={"preset": preset.tag}, subtitles=subs, clip=clip,
                   write_info_json=args.info_json)
    job = DownloadJob(spec, tcfg, AuthConfig(), _Printer())
    try:
        res = job.run()
    except JobCancelled:
        print("\ncanceled")
        return 130
    except JobFailed as e:
        print(f"\nERROR [{e.kind}]: {e.message}", file=sys.stderr)
        return 1
    print("\n" + json.dumps({"files": res.files, "extra": res.extra_files, "actual": res.actual,
                             "encoder": res.encoder, "warnings": res.warnings}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
