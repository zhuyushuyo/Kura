"""File-name rules and safe file moving."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Callable

# Tokens a user can put in the file-name template.  Values are yt-dlp
# output-template fragments (resolved per video by yt-dlp).
TOKENS: dict[str, str] = {
    "title": "%(title).120B",
    "id": "%(id)s",
    "channel": "%(channel,uploader,creator|Unknown).60B",
    "upload_date": "%(upload_date>%Y-%m-%d|NA)s",
    "height": "%(height|NA)s",
}
# Tokens Kura fills in itself (same value for the whole task).
LITERAL_TOKENS = ("dl_date", "preset", "playlist", "index")

_FORBIDDEN = {
    "<": "＜", ">": "＞", ":": "：", '"': "＂", "/": "⧸", "\\": "⧹",
    "|": "｜", "?": "？", "*": "＊",
}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_component(text: str, max_len: int = 120) -> str:
    """Make ``text`` safe as (part of) a file name on Windows and macOS."""
    text = _CTRL.sub("", str(text))
    text = "".join(_FORBIDDEN.get(ch, ch) for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(". ")
    if len(text) > max_len:
        text = text[:max_len].rstrip(". ")
    return text


def _escape_literal(text: str) -> str:
    """Literal template text: replace forbidden characters, escape ``%``."""
    text = _CTRL.sub("", text)
    text = "".join(_FORBIDDEN.get(ch, ch) for ch in text)
    return text.replace("%", "%%")


def build_outtmpl(template: str, literals: dict[str, str] | None = None, suffix: str = "") -> str:
    """Convert a user template like ``{upload_date}_{title}_[{id}]`` into a
    yt-dlp output template (without directory, with ``.%(ext)s``)."""
    literals = literals or {}
    out: list[str] = []
    pos = 0
    for m in re.finditer(r"\{(\w+)\}", template or ""):
        out.append(_escape_literal(template[pos:m.start()]))
        token = m.group(1)
        if token in TOKENS:
            out.append(TOKENS[token])
        elif token in literals:
            out.append(_escape_literal(sanitize_component(str(literals[token] or ""), max_len=80)))
        elif token in LITERAL_TOKENS:
            pass  # e.g. {playlist} for a single video -> empty
        else:
            out.append(_escape_literal(m.group(0)))
        pos = m.end()
    out.append(_escape_literal((template or "")[pos:]))
    name = "".join(out)
    # tidy separators left behind by empty literal tokens
    name = re.sub(r"([_\- ])\1+", r"\1", name).strip("_- ")
    if not name or "%(" not in name:
        name = (name + "_" if name else "") + "%(title).120B [%(id)s]"
    return name + suffix + ".%(ext)s"


def preview_name(template: str, sample: dict[str, str], literals: dict[str, str] | None = None) -> str:
    """Human preview of a template (used in Settings)."""
    literals = literals or {}

    def repl(m: re.Match) -> str:
        token = m.group(1)
        if token in sample:
            return sanitize_component(sample[token])
        if token in literals:
            return sanitize_component(literals[token])
        if token in LITERAL_TOKENS:
            return ""
        return m.group(0)

    name = re.sub(r"\{(\w+)\}", repl, template or "")
    name = re.sub(r"([_\- ])\1+", r"\1", name).strip("_- ")
    return sanitize_component(name, max_len=200) or "video"


# --------------------------------------------------------------------------
# Destination names
# --------------------------------------------------------------------------

def fit_base_for_windows(directory: str, base: str, longest_suffix: str, limit: int = 250) -> str:
    """Shorten ``base`` so that ``directory/base+suffix`` stays under MAX_PATH."""
    if not sys.platform.startswith("win"):
        return base
    room = limit - len(str(directory)) - 1 - len(longest_suffix)
    if len(base) <= room:
        return base
    room = max(room, 16)
    m = re.search(r"(_?\[[^\[\]]{1,24}\])$", base)
    tail = m.group(1) if m else ""
    head = base[: len(base) - len(tail)] if tail else base
    keep = max(room - len(tail), 8)
    return head[:keep].rstrip(" ._") + tail


def unique_base(directory: str, base: str, suffixes: list[str]) -> str:
    """Return ``base`` or ``base (2)``, ``base (3)``… so that no file
    ``directory/base+suffix`` exists for any of ``suffixes``."""
    def taken(b: str) -> bool:
        return any(os.path.exists(os.path.join(directory, b + s)) for s in suffixes)

    if not taken(base):
        return base
    n = 2
    while taken(f"{base} ({n})"):
        n += 1
    return f"{base} ({n})"


class MoveCancelled(Exception):
    pass


def move_file(
    src: str,
    dst: str,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    chunk: int = 8 << 20,
) -> None:
    """Move ``src`` to ``dst``; across volumes copy in chunks with progress.

    While copying, the destination is written under a temporary name and
    renamed at the end so a partially copied file never carries the final
    name (important for Google Drive / cloud folders).
    """
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    try:
        os.replace(src, dst)
        if progress:
            size = os.path.getsize(dst)
            progress(size, size)
        return
    except OSError:
        pass  # different volume -> copy

    total = os.path.getsize(src)
    tmp = dst + ".kura-part"
    done = 0
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                if cancelled and cancelled():
                    raise MoveCancelled()
                block = fi.read(chunk)
                if not block:
                    break
                fo.write(block)
                done += len(block)
                if progress:
                    progress(done, total)
        try:
            shutil.copystat(src, tmp)
        except OSError:
            pass
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.remove(src)


def split_known_suffix(filename: str, base: str) -> str | None:
    """If ``filename`` starts with ``base`` return the remaining suffix."""
    name = Path(filename).name
    if name.startswith(base):
        return name[len(base):]
    return None
