"""Shared fixtures: a local web server that serves generated test media.

YouTube itself is not reachable from CI, so the engine is exercised with
yt-dlp's generic extractor against media that ffmpeg generates here:

* ``/dash/manifest.mpd`` – DASH with separate VP9 video (720p + 360p) and
  Opus audio, i.e. the same shape as YouTube (merge + transcode needed)
* ``/page.html`` – an HTML5 page with an H.264/AAC MP4 and a Japanese
  WebVTT subtitle track written in YouTube's "rolling" auto-caption style
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FFMPEG = shutil.which("ffmpeg")

ROLLING_VTT = """WEBVTT
Kind: captions
Language: ja

00:00:00.000 --> 00:00:01.500
こんにちは

00:00:01.500 --> 00:00:01.510
こんにちは

00:00:01.510 --> 00:00:03.000
こんにちは
今日は<00:00:02.000><c>テスト</c>です

00:00:03.000 --> 00:00:03.010
今日はテストです

00:00:03.010 --> 00:00:04.800
今日はテストです
よろしくお願いします
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Kura テスト動画</title></head>
<body><video controls width="640">
<source src="sample.mp4" type="video/mp4">
<track kind="subtitles" src="sample.ja.vtt" srclang="ja" label="日本語">
</video></body></html>
"""


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def build_media(root: Path) -> None:
    (root / "dash").mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
          "-map", "0:v", "-map", "0:v", "-map", "1:a",
          "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1",
          "-b:v:0", "1M", "-s:v:0", "1280x720", "-b:v:1", "300k", "-s:v:1", "640x360",
          "-g", "60", "-keyint_min", "60",
          "-c:a", "libopus", "-b:a", "64k",
          "-f", "dash", "-seg_duration", "2", "-use_template", "1", "-use_timeline", "1",
          "-adaptation_sets", "id=0,streams=v id=1,streams=a", "manifest.mpd"], root / "dash")
    _run([FFMPEG, "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=5",
          "-f", "lavfi", "-i", "sine=frequency=660:duration=5", "-c:v", "libx264", "-preset", "ultrafast",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
          "sample.mp4"], root)
    (root / "sample.ja.vtt").write_text(ROLLING_VTT, encoding="utf-8")
    (root / "page.html").write_text(PAGE, encoding="utf-8")


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass


@pytest.fixture(scope="session")
def media_server(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    root = tmp_path_factory.mktemp("media")
    build_media(root)
    handler = functools.partial(_QuietHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture()
def kura_home(tmp_path, monkeypatch):
    """Isolated KURA_HOME for each test."""
    home = tmp_path / "kura-home"
    monkeypatch.setenv("KURA_HOME", str(home))
    from kura import paths

    for name, sub in (("HOME", ""), ("CONFIG_FILE", "config.json"), ("DB_FILE", "library.db"),
                      ("WORK_DIR", "work"), ("LOG_DIR", "logs"), ("THUMB_DIR", "thumbs"),
                      ("CACHE_DIR", "cache"), ("BIN_DIR", "bin"), ("WEB_DIR", "web")):
        monkeypatch.setattr(paths, name, home / sub if sub else home)
    paths.ensure_dirs()
    return home
