"""Cookies from Kura's built-in browser, handed to yt-dlp in memory.

Nothing is written to disk: the records are injected straight into the
yt-dlp cookie jar for one download and discarded afterwards.
"""

from __future__ import annotations

import http.cookiejar
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlparse


@dataclass
class CookieRecord:
    domain: str
    name: str
    value: str
    path: str = "/"
    secure: bool = False
    expires: int | None = None      # unix time; None = session cookie
    http_only: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


YOUTUBE_DOMAINS = ("youtube.com", "google.com", "youtu.be", "googlevideo.com")
_LOGIN_COOKIES = {"SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID", "LOGIN_INFO", "SID"}


def _domain_matches(cookie_domain: str, host: str) -> bool:
    d = cookie_domain.lstrip(".").lower()
    host = host.lower()
    return host == d or host.endswith("." + d)


def relevant_for(records: list[CookieRecord], url: str) -> list[CookieRecord]:
    host = (urlparse(url).hostname or "").lower()
    is_yt = any(_domain_matches(d, host) for d in ("youtube.com", "youtu.be"))
    out = []
    for r in records:
        dom = r.domain.lstrip(".").lower()
        if is_yt and any(dom == d or dom.endswith("." + d) for d in YOUTUBE_DOMAINS):
            out.append(r)
        elif host and _domain_matches(r.domain, host):
            out.append(r)
    return out


def is_logged_in_youtube(records: list[CookieRecord]) -> bool:
    now = time.time()
    for r in records:
        dom = r.domain.lstrip(".").lower()
        if r.name in _LOGIN_COOKIES and (dom.endswith("youtube.com") or dom.endswith("google.com")):
            if r.expires is None or r.expires > now:
                return True
    return False


def inject(jar: http.cookiejar.CookieJar, records: list[CookieRecord]) -> int:
    n = 0
    for r in records:
        domain = r.domain
        c = http.cookiejar.Cookie(
            version=0, name=r.name, value=r.value, port=None, port_specified=False,
            domain=domain, domain_specified=domain.startswith("."),
            domain_initial_dot=domain.startswith("."), path=r.path or "/", path_specified=True,
            secure=bool(r.secure), expires=r.expires, discard=r.expires is None,
            comment=None, comment_url=None, rest={"HttpOnly": None} if r.http_only else {},
        )
        jar.set_cookie(c)
        n += 1
    return n
