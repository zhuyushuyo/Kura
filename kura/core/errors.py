"""Turn raw yt-dlp / ffmpeg error messages into a known error kind.

The explanation texts (title + what to do) live in :mod:`kura.i18n` under
``err.<kind>.title`` and ``err.<kind>.advice``.
"""

from __future__ import annotations

import platform
import re
import sys

# Order matters: the first matching kind wins.
_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("canceled", (r"cancel(?:l)?ed by user",)),
    ("age", (r"confirm your age", r"age[- ]restricted", r"inappropriate for some users",
             r"age verification")),
    ("bot", (r"not a bot", r"confirm you.{0,3}re not", r"unusual traffic", r"captcha")),
    ("members", (r"members[- ]only", r"channel'?s members", r"join this channel",
                 r"available to this channel")),
    ("private", (r"private video", r"this video is private", r"video is private")),
    ("upcoming", (r"premieres in", r"will begin in", r"is upcoming", r"live event will begin",
                  r"scheduled to start")),
    ("geo", (r"not available in your country", r"geo[- ]?restrict", r"blocked it in your country",
             r"not available from your location")),
    ("format", (r"requested format is not available", r"no video formats found",
                r"format is not available", r"no formats? (?:found|available)")),
    ("unavailable", (r"video unavailable", r"has been removed", r"no longer available",
                     r"account associated with this video has been terminated",
                     r"copyright (?:claim|grounds)", r"this video does not exist", r"HTTP Error 404")),
    ("rate", (r"HTTP Error 429", r"too many requests", r"rate[- ]?limit")),
    ("js", (r"javascript runtime", r"js runtime", r"n challenge", r"\bnsig\b",
            r"signature (?:extraction|function|solving)", r"unable to extract (?:initial )?player",
            r"challenge solving", r"\bEJS\b", r"\bdeno\b")),
    ("forbidden", (r"HTTP Error 403", r"\b403\b.*forbidden", r"forbidden")),
    ("ffmpeg", (r"ffmpeg (?:is )?not (?:found|installed)", r"ffprobe", r"ffmpeg",
                r"postprocessing", r"conversion failed", r"error opening output",
                r"merging of formats", r"encoder")),
    ("cookies", (r"cookie", r"failed to decrypt", r"keyring", r"could not find .* cookies database")),
    ("unsupported", (r"unsupported url", r"is not a valid url", r"no suitable infoextractor",
                     r"not a valid url")),
    ("disk", (r"no space left", r"errno 28", r"disk (?:is )?full", r"not enough space")),
    ("pathlen", (r"file name too long", r"errno 36", r"winerror 206", r"path too long")),
    ("permission", (r"permission denied", r"errno 13", r"access is denied", r"winerror 5\b",
                    r"read-only file system", r"operation not permitted")),
    ("network", (r"getaddrinfo failed", r"name or service not known", r"timed out",
                 r"connection (?:reset|refused|aborted)", r"temporary failure in name resolution",
                 r"unable to download webpage", r"certificate_verify_failed", r"ssl",
                 r"network is unreachable", r"remotedisconnected", r"urlopen error",
                 r"nodename nor servname", r"no route to host")),
]

_COMPILED = [(kind, [re.compile(p, re.IGNORECASE) for p in pats]) for kind, pats in _PATTERNS]


def classify(message: str | None) -> str:
    msg = message or ""
    for kind, regexes in _COMPILED:
        if any(r.search(msg) for r in regexes):
            return kind
    return "unknown"


def clean_message(message: str | None) -> str:
    """Strip yt-dlp prefixes / ANSI codes for display."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", message or "").strip()
    msg = re.sub(r"^(?:ERROR:\s*)+", "", msg)
    return msg


def build_report(*, kura_version: str, versions: dict, task: dict, message: str,
                 log_lines: list[str]) -> str:
    """Plain-text error report the user can paste into an issue / chat.

    Cookies and other secrets are never included.
    """
    home = None
    try:
        from pathlib import Path

        home = str(Path.home())
    except Exception:  # noqa: BLE001
        pass

    def scrub(text: str) -> str:
        text = re.sub(r"(?i)(cookie|authorization|sapisid\w*)[=:]\s*\S+", r"\1=<hidden>", text)
        if home:
            text = text.replace(home, "~")
        return text

    lines = [
        f"Kura {kura_version}",
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {sys.version.split()[0]}",
    ]
    for k, v in versions.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    for k in ("url", "preset", "auth_mode"):
        if task.get(k):
            lines.append(f"{k}: {task[k]}")
    lines += ["", "Error:", scrub(clean_message(message)), "", "Log (last lines):"]
    lines += [scrub(ln) for ln in log_lines[-40:]]
    return "\n".join(lines)
