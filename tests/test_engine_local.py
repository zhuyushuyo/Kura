"""End-to-end engine tests against locally served media (no internet)."""

from __future__ import annotations

import os
import uuid

import pytest

from kura.core import tools
from kura.core.engine import AuthConfig, DownloadJob, JobListener, JobSpec, ToolConfig, fetch_info, estimate
from kura.core.presets import SubtitleOptions, builtin_presets
from kura.core.transcode import probe


class Recorder(JobListener):
    def __init__(self):
        self.phases, self.progress_events, self.infos, self.lines = [], [], [], []

    def phase(self, phase, detail=""):
        self.phases.append((phase, detail))

    def progress(self, data):
        self.progress_events.append(data)

    def log(self, line):
        self.lines.append(line)

    def info(self, summary):
        self.infos.append(summary)


@pytest.fixture(scope="module")
def toolcfg():
    ff = tools.find_ffmpeg()
    if not ff.ok:
        pytest.skip("ffmpeg missing")
    return ToolConfig(ffmpeg=ff.ffmpeg, ffprobe=ff.ffprobe, encoders=tools.detect_encoders(ff.ffmpeg, use_cache=False),
                      use_gpu=False)


def _preset(pid):
    return next(p for p in builtin_presets() if p.id == pid)


def _job(url, preset_id, dest, kura_home, toolcfg, **kw):
    spec = JobSpec(url=url, preset=_preset(preset_id).to_dict(), dest_dir=str(dest),
                   work_dir=str(kura_home / "work" / uuid.uuid4().hex[:8]),
                   literals={"preset": _preset(preset_id).tag}, **kw)
    rec = Recorder()
    return DownloadJob(spec, toolcfg, AuthConfig(), rec), rec


def test_edit1080_from_dash_transcodes_to_h264_aac_mp4(media_server, tmp_path, kura_home, toolcfg):
    job, rec = _job(media_server + "/dash/manifest.mpd", "edit1080", tmp_path / "out", kura_home, toolcfg)
    res = job.run()
    assert len(res.files) == 1
    out = res.files[0]
    assert out.endswith(".mp4") and os.path.isfile(out)
    pr = probe(out, toolcfg.ffprobe)
    assert pr.vcodec == "h264" and pr.acodec == "aac"
    assert pr.height == 720  # best available <= 1080
    assert res.encoder == "libx264"
    phases = [p for p, _ in rec.phases]
    for expected in ("extracting", "downloading", "converting", "moving", "done"):
        assert expected in phases
    assert any(e.get("stage") == "download" and e.get("fraction") for e in rec.progress_events)
    assert rec.infos and rec.infos[0].get("title")
    assert not os.path.exists(job.spec.work_dir)  # work area cleaned


def test_best_keeps_original_codecs_in_mkv(media_server, tmp_path, kura_home, toolcfg):
    job, _ = _job(media_server + "/dash/manifest.mpd", "best", tmp_path / "out", kura_home, toolcfg)
    res = job.run()
    pr = probe(res.files[0], toolcfg.ffprobe)
    assert res.files[0].endswith(".mkv")
    assert pr.vcodec == "vp9" and pr.acodec == "opus" and pr.height == 720


def test_audio_presets(media_server, tmp_path, kura_home, toolcfg):
    for pid, ext, codec in (("audio_mp3", ".mp3", "mp3"), ("audio_m4a", ".m4a", "aac"), ("audio_wav", ".wav", "pcm_s16le")):
        job, _ = _job(media_server + "/dash/manifest.mpd", pid, tmp_path / pid, kura_home, toolcfg)
        res = job.run()
        assert res.files[0].endswith(ext), res.files
        pr = probe(res.files[0], toolcfg.ffprobe)
        assert pr.vcodec is None and pr.acodec == codec


def test_subtitles_clean_srt_txt_csv_and_embed(media_server, tmp_path, kura_home, toolcfg):
    subs = SubtitleOptions(langs=["ja"], auto=True, mode="both", txt=True, csv=True).to_dict()
    job, _ = _job(media_server + "/page.html", "edit1080", tmp_path / "out", kura_home, toolcfg,
                  subtitles=subs, write_info_json=True)
    res = job.run()
    main = res.files[0]
    base = os.path.splitext(main)[0]
    names = {os.path.basename(p) for p in res.extra_files}
    assert os.path.basename(base) + ".ja.srt" in names
    assert os.path.basename(base) + ".ja.txt" in names
    assert os.path.basename(base) + ".ja.csv" in names
    assert os.path.basename(base) + ".info.json" in names
    srt = open(base + ".ja.srt", encoding="utf-8").read()
    assert srt.count("こんにちは") == 1 and srt.count("今日はテストです") == 1
    assert "<c>" not in srt
    txt = open(base + ".ja.txt", encoding="utf-8").read().splitlines()
    assert txt == ["[00:00:00] こんにちは", "[00:00:01] 今日はテストです", "[00:00:03] よろしくお願いします"]
    # embedded as mov_text in the MP4
    out = tools.run_quiet([toolcfg.ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                           "-of", "csv=p=0", main]).stdout
    assert "mov_text" in out


def test_clip_section(media_server, tmp_path, kura_home, toolcfg):
    job, _ = _job(media_server + "/page.html", "best", tmp_path / "out", kura_home, toolcfg,
                  clip={"start": 1, "end": 3, "precise": True})
    res = job.run()
    pr = probe(res.files[0], toolcfg.ffprobe)
    assert pr.duration is not None and 1.5 <= pr.duration <= 2.6, pr.duration
    assert "00-00-01" in os.path.basename(res.files[0])


def test_name_collision_gets_suffix(media_server, tmp_path, kura_home, toolcfg):
    outs = []
    for _ in range(2):
        job, _ = _job(media_server + "/page.html", "audio_m4a", tmp_path / "out", kura_home, toolcfg)
        outs.append(job.run().files[0])
    assert outs[0] != outs[1]
    assert outs[1].endswith(" (2).m4a")


def test_cancel_before_start(media_server, tmp_path, kura_home, toolcfg):
    from kura.core.engine import JobCancelled

    job, _ = _job(media_server + "/dash/manifest.mpd", "edit1080", tmp_path / "out", kura_home, toolcfg)
    job.cancel()
    with pytest.raises(JobCancelled):
        job.run()


def test_fetch_info_and_estimate(media_server, toolcfg):
    info = fetch_info(media_server + "/dash/manifest.mpd", toolcfg)
    assert info.get("formats")
    size, chosen = estimate(info, _preset("edit1080"), True)
    heights = [f.get("height") for f in chosen if f.get("height")]
    assert heights == [720]


def test_unsupported_url_is_classified(toolcfg, tmp_path, kura_home):
    from kura.core.engine import JobFailed

    job, _ = _job("http://127.0.0.1:9/nothing-here", "edit1080", tmp_path / "out", kura_home, toolcfg)
    with pytest.raises(JobFailed) as ei:
        job.run()
    assert ei.value.kind in ("network", "unsupported", "unknown")


def test_pause_keeps_work_dir_and_resume_completes(media_server, tmp_path, kura_home, toolcfg):
    from kura.core.engine import JobCancelled

    job, rec = _job(media_server + "/dash/manifest.mpd", "best", tmp_path / "out", kura_home, toolcfg)
    original = rec.progress

    def pause_on_first(data):
        original(data)
        if data.get("stage") == "download":
            job.cancel(pause=True)

    rec.progress = pause_on_first
    with pytest.raises(JobCancelled) as ei:
        job.run()
    assert ei.value.paused
    assert os.path.isdir(job.spec.work_dir)  # partial data kept for resume

    job2 = DownloadJob(job.spec, toolcfg, AuthConfig(), Recorder())
    res = job2.run()
    assert res.files and os.path.isfile(res.files[0])
