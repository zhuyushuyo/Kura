"""Citation strings for downloaded videos (APA 7, MLA 9, Chicago, Japanese)."""

from __future__ import annotations

import datetime as _dt

STYLES = ("apa", "mla", "chicago", "ja")

_SITE_NAMES = {
    "youtube": "YouTube",
    "youtubetab": "YouTube",
    "vimeo": "Vimeo",
    "niconico": "ニコニコ動画",
    "twitch": "Twitch",
    "dailymotion": "Dailymotion",
    "bilibili": "bilibili",
    "tiktok": "TikTok",
    "twitter": "X (Twitter)",
    "ted": "TED",
}
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
_MLA_MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July",
               "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]


def site_name(meta: dict) -> str:
    key = (meta.get("extractor_key") or meta.get("extractor") or "").lower().split(":")[0]
    if key in _SITE_NAMES:
        return _SITE_NAMES[key]
    for k, v in _SITE_NAMES.items():
        if key.startswith(k):
            return v
    domain = meta.get("webpage_url_domain") or ""
    return domain or (meta.get("extractor_key") or "Web")


def _published(meta: dict) -> _dt.date | None:
    for key in ("upload_date", "release_date"):
        v = meta.get(key)
        if v and len(str(v)) == 8 and str(v).isdigit():
            s = str(v)
            try:
                return _dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
            except ValueError:
                pass
    ts = meta.get("timestamp") or meta.get("release_timestamp")
    if ts:
        try:
            return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            pass
    return None


def _duration(meta: dict) -> str:
    d = meta.get("duration")
    if not d:
        return ""
    d = int(d)
    h, rem = divmod(d, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def cite(meta: dict, style: str = "apa", accessed: _dt.date | None = None) -> str:
    accessed = accessed or _dt.date.today()
    author = meta.get("channel") or meta.get("uploader") or meta.get("creator") or "Unknown"
    title = (meta.get("title") or "Untitled").strip()
    url = meta.get("webpage_url") or meta.get("original_url") or meta.get("url") or ""
    site = site_name(meta)
    date = _published(meta)

    if style == "mla":
        when = f"{date.day} {_MLA_MONTHS[date.month - 1]} {date.year}" if date else "n.d."
        url_part = url.replace("https://", "").replace("http://", "")
        return f"“{title}.” {site}, uploaded by {author}, {when}, {url_part}."
    if style == "chicago":
        when = f"{_MONTHS[date.month - 1]} {date.day}, {date.year}" if date else "n.d."
        dur = _duration(meta)
        video = f"{site} video" + (f", {dur}" if dur else "")
        return f"{author}. “{title}.” {video}. {when}. {url}."
    if style == "ja":
        when = f"{date.year}年{date.month}月{date.day}日" if date else "日付不明"
        acc = f"{accessed.year}年{accessed.month}月{accessed.day}日"
        return f"{author}「{title}」{site}，{when}．{url}（{acc}閲覧）"
    # APA 7
    when = f"{date.year}, {_MONTHS[date.month - 1]} {date.day}" if date else "n.d."
    return f"{author}. ({when}). {title} [Video]. {site}. {url}"
