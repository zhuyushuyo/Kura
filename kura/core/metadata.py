"""Metadata extraction/export helpers."""

from __future__ import annotations

import csv
import json
from typing import Iterable

SUMMARY_KEYS = (
    "id", "title", "fulltitle", "channel", "channel_id", "channel_url", "uploader",
    "uploader_id", "uploader_url", "creator", "upload_date", "release_date", "timestamp",
    "duration", "view_count", "like_count", "comment_count", "channel_follower_count",
    "tags", "categories", "description", "webpage_url", "original_url", "extractor",
    "extractor_key", "webpage_url_domain", "language", "availability", "live_status",
    "age_limit", "width", "height", "fps", "vcodec", "acodec", "dynamic_range",
    "format_id", "format_note", "filesize", "filesize_approx", "thumbnail",
    "playlist_title", "playlist_index", "is_live", "was_live", "media_type",
)


def summarize(info: dict | None) -> dict:
    """JSON-serialisable subset of a yt-dlp info dict."""
    if not info:
        return {}
    out = {}
    for key in SUMMARY_KEYS:
        val = info.get(key)
        if val is not None and val != "":
            out[key] = val
    chapters = info.get("chapters")
    if chapters:
        out["chapters"] = [
            {"start_time": c.get("start_time"), "end_time": c.get("end_time"), "title": c.get("title")}
            for c in chapters
        ]
    subs = sorted((info.get("subtitles") or {}).keys())
    if subs:
        out["subtitle_languages"] = subs
    if info.get("requested_formats"):
        out["formats_used"] = [
            {k: f.get(k) for k in ("format_id", "ext", "vcodec", "acodec", "width", "height", "fps", "tbr")}
            for f in info["requested_formats"]
        ]
    return out


def write_json(meta: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)


LIBRARY_CSV_COLUMNS = (
    "title", "channel", "upload_date", "duration", "url", "video_id", "preset_name",
    "file_path", "file_size", "width", "height", "vcodec", "acodec", "view_count",
    "like_count", "downloaded_at", "playlist_title", "tags", "description",
)


def write_library_csv(rows: Iterable[dict], path: str) -> int:
    n = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(LIBRARY_CSV_COLUMNS)
        for r in rows:
            vals = []
            for col in LIBRARY_CSV_COLUMNS:
                v = r.get(col)
                if col == "tags" and v:
                    try:
                        v = ", ".join(json.loads(v)) if isinstance(v, str) else ", ".join(v)
                    except (ValueError, TypeError):
                        pass
                vals.append("" if v is None else v)
            w.writerow(vals)
            n += 1
    return n
