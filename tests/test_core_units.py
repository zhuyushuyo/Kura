"""Unit tests for the Qt-free helpers."""

from __future__ import annotations

import datetime as dt
import json
import os

from kura.core import citation, errors, naming, subtitles, urls
from kura.core.presets import Preset, SubtitleOptions, all_presets, builtin_presets, new_custom_preset


# -- urls -----------------------------------------------------------------------

def test_extract_urls_from_messy_text():
    text = ("見て→https://www.youtube.com/watch?v=dQw4w9WgXcQ。あと youtu.be/abcdefghijk と"
            "「https://example.com/a?b=1」\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert urls.extract_urls(text) == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/abcdefghijk",
        "https://example.com/a?b=1",
    ]


def test_classify_youtube():
    c = urls.classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123")
    assert c.kind == "video" and c.video_id == "dQw4w9WgXcQ" and c.has_list
    mix = urls.classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ")
    assert mix.kind == "video" and not mix.has_list
    assert urls.classify("https://youtube.com/shorts/abcdefghijk").video_id == "abcdefghijk"
    assert urls.classify("https://www.youtube.com/playlist?list=PLx").kind == "playlist"
    assert urls.classify("https://www.youtube.com/@someone").kind == "channel"
    assert urls.classify("https://www.youtube.com/@someone/videos").kind == "channel"
    assert urls.classify("https://www.youtube.com/").kind == "home"
    assert urls.classify("https://www.youtube.com/results?search_query=x").kind == "search"
    assert urls.classify("https://vimeo.com/123").kind == "other"
    assert urls.channel_videos_url("https://www.youtube.com/@someone") == "https://www.youtube.com/@someone/videos"
    assert urls.channel_videos_url("https://www.youtube.com/@someone/shorts") == "https://www.youtube.com/@someone/shorts"


# -- naming ---------------------------------------------------------------------

def test_build_outtmpl_tokens_and_literals():
    t = naming.build_outtmpl("{upload_date}_{title}_[{id}]", {})
    assert t == "%(upload_date>%Y-%m-%d|NA)s_%(title).120B_[%(id)s].%(ext)s"
    t2 = naming.build_outtmpl("{index}_{title} {preset}", {"index": "003", "preset": "4K"})
    assert t2 == "003_%(title).120B 4K.%(ext)s"
    # empty literal tokens do not leave stray separators
    t3 = naming.build_outtmpl("{playlist}_{index}_{title}", {})
    assert t3 == "%(title).120B.%(ext)s"
    # literal "%" and path separators are neutralised
    t4 = naming.build_outtmpl("100% {title}/x", {})
    assert "%%" in t4 and "/" not in t4.replace("%(", "")


def test_unique_base_and_fit(tmp_path):
    (tmp_path / "a.mp4").write_text("x")
    assert naming.unique_base(str(tmp_path), "a", [".mp4", ".ja.srt"]) == "a (2)"
    assert naming.unique_base(str(tmp_path), "b", [".mp4"]) == "b"


def test_move_file_across_dirs(tmp_path):
    src = tmp_path / "s.bin"
    src.write_bytes(b"x" * 1000)
    dst = tmp_path / "sub" / "d.bin"
    seen = []
    naming.move_file(str(src), str(dst), progress=lambda d, t: seen.append((d, t)))
    assert dst.read_bytes() == b"x" * 1000 and not src.exists() and seen


def test_preview_name():
    p = naming.preview_name("{upload_date}_{title}_[{id}]",
                            {"upload_date": "2026-11-05", "title": "A/B: test?", "id": "abc"})
    assert p == "2026-11-05_A⧸B： test？_[abc]"


# -- subtitles ------------------------------------------------------------------

def test_json3_parse_and_clean():
    data = {"events": [
        {"tStartMs": 0, "dDurationMs": 4000, "segs": [{"utf8": "hello"}, {"utf8": " world"}]},
        {"tStartMs": 2000, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2500, "dDurationMs": 3000, "segs": [{"utf8": "second line"}]},
    ]}
    cues = subtitles.clean_cues(subtitles.parse_json3(json.dumps(data)))
    assert cues == [(0.0, 2.5, "hello world"), (2.5, 5.5, "second line")]
    srt = subtitles.to_srt(cues)
    assert "00:00:00,000 --> 00:00:02,500" in srt
    csv_text = subtitles.to_csv(cues)
    assert csv_text.splitlines()[0] == "start,end,start_seconds,end_seconds,text"


def test_srt_roundtrip():
    cues = [(1.0, 2.0, "a\nb"), (3.5, 4.25, "c")]
    again = subtitles.parse_srt(subtitles.to_srt(cues))
    assert again == cues
    assert subtitles.to_txt(cues) == "[00:00:01] a b\n[00:00:03] c\n"


# -- errors -----------------------------------------------------------------------

def test_error_classification():
    c = errors.classify
    assert c("ERROR: [youtube] x: Sign in to confirm you’re not a bot.") == "bot"
    assert c("Sign in to confirm your age. This video may be inappropriate") == "age"
    assert c("ERROR: Requested format is not available. Use --list-formats") == "format"
    assert c("HTTP Error 403: Forbidden") == "forbidden"
    assert c("HTTP Error 429: Too Many Requests") == "rate"
    assert c("Private video. Sign in if you've been granted access") == "private"
    assert c("[Errno 28] No space left on device") == "disk"
    assert c("urlopen error [Errno 11001] getaddrinfo failed") == "network"
    assert c("Unsupported URL: https://example.com") == "unsupported"
    assert c("Join this channel to get access to members-only content") == "members"
    assert c("something odd") == "unknown"
    assert errors.clean_message("ERROR: ERROR: boom") == "boom"


def test_error_report_scrubs_secrets():
    rep = errors.build_report(kura_version="3.0.0", versions={"yt-dlp": "x"}, task={"url": "u"},
                              message="cookie: SAPISID=abc123", log_lines=["Cookie=secret"])
    assert "abc123" not in rep and "secret" not in rep


# -- citation -----------------------------------------------------------------------

META = {"title": "Lesson study in practice", "channel": "Edu Channel", "upload_date": "20261105",
        "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk", "extractor_key": "Youtube",
        "duration": 754}


def test_citations():
    acc = dt.date(2026, 11, 6)
    assert citation.cite(META, "apa", acc) == (
        "Edu Channel. (2026, November 5). Lesson study in practice [Video]. YouTube. "
        "https://www.youtube.com/watch?v=abcdefghijk")
    assert citation.cite(META, "mla", acc).startswith("“Lesson study in practice.” YouTube, uploaded by Edu Channel, 5 Nov. 2026")
    assert "12:34" in citation.cite(META, "chicago", acc)
    assert citation.cite(META, "ja", acc) == (
        "Edu Channel「Lesson study in practice」YouTube，2026年11月5日．"
        "https://www.youtube.com/watch?v=abcdefghijk（2026年11月6日閲覧）")


# -- presets ------------------------------------------------------------------------

def test_preset_format_selection():
    p = {x.id: x for x in builtin_presets()}
    fmt, sort, merge = p["edit1080"].format_selection(True)
    assert fmt == "bv*+ba/b" and sort[:2] == ["res:1080", "hdr:sdr"] and "+vcodec:avc" in sort and merge == "mkv"
    assert p["edit1080"].needs_transcode_step and not p["best"].needs_transcode_step
    fmt, sort, merge = p["edit1080"].format_selection(False)
    assert fmt.startswith("b/") and merge is None
    assert builtin_presets("hevc")[1].vcodec == "hevc"
    fmt, sort, _ = p["audio_m4a"].format_selection(True)
    assert fmt == "ba/b" and sort == ["+acodec:m4a"]


def test_custom_presets_roundtrip():
    c = new_custom_preset(builtin_presets()[0], name="My 720p")
    c.max_res = 720
    store = {"custom_presets": [c.to_dict()], "edit4k_codec": "h264"}
    got = all_presets(lambda k, d=None: store.get(k, d))
    assert got[-1].name == "My 720p" and got[-1].max_res == 720 and not got[-1].builtin
    assert Preset.from_dict({**c.to_dict(), "unknown_field": 1}).id == c.id


def test_subtitle_options_from_settings():
    so = SubtitleOptions.from_settings({"langs": "ja、 en,", "mode": "embed"})
    assert so.langs == ["ja", "en"] and so.embed and not so.keep_files


# -- library / config ---------------------------------------------------------------

def test_library_and_tasks(kura_home, tmp_path):
    from kura.core.library import Database

    db = Database(kura_home / "t.db")
    f = tmp_path / "v.mp4"
    f.write_text("x")
    item_id = db.add_item({"video_id": "abc", "title": "テスト動画", "channel": "ch", "preset_id": "edit1080",
                           "kind": "video", "file_path": str(f), "tags": ["a", "b"]})
    assert db.get_item(item_id)["title"] == "テスト動画"
    assert db.search("テスト")[0]["id"] == item_id
    assert db.find_downloaded("abc", "edit1080")
    assert db.find_downloaded("abc", "audio_mp3") is None
    os.remove(f)
    assert db.find_downloaded("abc") is None  # file gone -> not "downloaded"
    db.save_task({"id": "t1", "status": "paused", "url": "u", "title": "x"})
    db.save_task({"id": "t1", "status": "done", "url": "u", "title": "x"})
    assert [t["status"] for t in db.load_tasks()] == ["done"]


def test_import_v2_settings(kura_home, tmp_path):
    from kura.config import Settings, import_v2_settings

    target = tmp_path / "videos"
    target.mkdir()
    v2 = tmp_path / "user_config.json"
    v2.write_text(json.dumps({"download_path": str(target), "quality": "Audio Only", "theme": "Dark",
                              "gpu_enabled": True, "auth_source": "Guest (No Login)"}), encoding="utf-8")
    s = Settings(kura_home / "config.json")
    applied = import_v2_settings(s, str(v2))
    assert set(applied) >= {"save_dir", "default_preset", "theme", "webengine_gpu", "auth_mode"}
    s2 = Settings(kura_home / "config.json")
    assert s2.get("default_preset") == "audio_mp3" and s2.get("theme") == "dark"
    assert s2.save_dir() == str(target)
