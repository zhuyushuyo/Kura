"""URL helpers: pick URLs out of text and classify YouTube links."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_URL_RE = re.compile(
    r"(?:https?://|(?<![\w/.@-])(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/)"
    r"[A-Za-z0-9\-._~:/?#\[\]@!$&*+,;=%]+",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}>、。」』）】"
_YT_ID = re.compile(r"^[0-9A-Za-z_-]{11}$")

YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtube-nocookie.com", "youtube-nocookie.com",
}

_CHANNEL_RE = re.compile(
    r"^/(?:@[^/]+|channel/UC[\w-]+|c/[^/]+|user/[^/]+)"
    r"(?:/(?P<tab>videos|shorts|streams|featured|playlists|releases|podcasts|live))?/?$"
)


def extract_urls(text: str) -> list[str]:
    """Return the unique http(s) URLs found in ``text`` (order preserved)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(_TRAILING)
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def normalize(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.IGNORECASE):
        url = "https://" + url
    return url


def looks_like_url(text: str) -> bool:
    text = (text or "").strip()
    if not text or " " in text:
        return False
    if re.match(r"^https?://", text, re.IGNORECASE):
        return True
    return bool(re.match(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/.*)?$", text))


@dataclass
class UrlInfo:
    url: str
    kind: str                 # video | playlist | channel | search | home | other | empty
    site: str                 # "youtube" or host name
    video_id: str | None = None
    list_id: str | None = None
    list_is_mix: bool = False

    @property
    def is_youtube(self) -> bool:
        return self.site == "youtube"

    @property
    def has_list(self) -> bool:
        """True when the URL refers to a real (finite) playlist."""
        return bool(self.list_id) and not self.list_is_mix

    @property
    def is_collection(self) -> bool:
        return self.kind in ("playlist", "channel")

    @property
    def is_downloadable_guess(self) -> bool:
        return self.kind in ("video", "playlist", "channel", "other")


def _is_mix(list_id: str | None) -> bool:
    # RD… = auto-generated mixes/radio (practically endless), UL = uploads mix
    return bool(list_id) and list_id.startswith(("RD", "UL"))


def classify(url: str) -> UrlInfo:
    url = normalize(url)
    if not url:
        return UrlInfo(url, "empty", "")
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host not in YT_HOSTS and not host.endswith(".youtube.com"):
        return UrlInfo(url, "other", host)

    query = parse_qs(p.query)
    path = p.path or "/"
    list_id = (query.get("list") or [None])[0]

    video_id = None
    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0] or None
    elif path == "/watch":
        video_id = (query.get("v") or [None])[0]
    else:
        m = re.match(r"^/(?:shorts|live|embed|v|e)/([0-9A-Za-z_-]{11})", path)
        if m:
            video_id = m.group(1)

    if video_id and _YT_ID.match(video_id):
        return UrlInfo(url, "video", "youtube", video_id, list_id, _is_mix(list_id))
    if path.rstrip("/") == "/playlist" and list_id:
        return UrlInfo(url, "playlist", "youtube", None, list_id, _is_mix(list_id))
    if _CHANNEL_RE.match(path):
        return UrlInfo(url, "channel", "youtube")
    if path.startswith("/results"):
        return UrlInfo(url, "search", "youtube")
    if path in ("", "/") or path.startswith("/feed"):
        return UrlInfo(url, "home", "youtube")
    return UrlInfo(url, "other", "youtube")


def canonical_video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def playlist_url(list_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={list_id}"


def channel_videos_url(url: str) -> str:
    """For a channel root URL return its "Videos" tab URL."""
    p = urlparse(normalize(url))
    m = _CHANNEL_RE.match(p.path or "")
    if not m:
        return url
    tab = m.group("tab")
    if tab in (None, "featured"):
        base = p.path.rstrip("/")
        if tab == "featured":
            base = base[: -len("/featured")]
        return f"{p.scheme or 'https'}://{p.netloc}{base}/videos"
    return url


def search_url(query: str) -> str:
    from urllib.parse import quote_plus

    return f"https://www.youtube.com/results?search_query={quote_plus(query)}"
