"""Subtitle parsing, cleaning and conversion (SRT / TXT / CSV).

YouTube's automatic captions are messy when downloaded as WebVTT (each cue
repeats the previous line while the next one "rolls in").  Kura prefers the
``json3`` format for YouTube and cleans every format into simple cues before
writing SRT files and transcripts.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
from typing import Iterable

Cue = tuple[float, float, str]

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
_TIMING = re.compile(
    r"(?P<s>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*(?P<e>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3})"
)
_INLINE_TS = re.compile(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{3}>|<\d{1,2}:\d{2}[.,]\d{3}>")
_TAGS = re.compile(r"</?[^>\n]{1,40}>")


def _ts_to_seconds(ts: str) -> float:
    m = _TS.fullmatch(ts.strip())
    if not m:
        raise ValueError(f"bad timestamp: {ts!r}")
    h, mi, s, ms = m.groups()
    ms = (ms or "0").ljust(3, "0")
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def _clean_text(text: str) -> str:
    text = _INLINE_TS.sub("", text)
    text = _TAGS.sub("", text)
    text = html.unescape(text).replace("​", "").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------

def parse_json3(text: str) -> list[Cue]:
    data = json.loads(text)
    cues: list[Cue] = []
    for ev in data.get("events") or []:
        segs = ev.get("segs")
        if not segs:
            continue
        txt = "".join(seg.get("utf8", "") for seg in segs)
        txt = _clean_text(txt)
        if not txt:
            continue
        start = (ev.get("tStartMs") or 0) / 1000
        dur = (ev.get("dDurationMs") or 0) / 1000
        cues.append((start, start + dur, txt))
    return cues


def _parse_blocks(text: str) -> list[Cue]:
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = _TIMING.search(line)
            if not m:
                continue
            try:
                start = _ts_to_seconds(m.group("s"))
                end = _ts_to_seconds(m.group("e"))
            except ValueError:
                break
            payload = _clean_text("\n".join(lines[i + 1:]))
            if payload:
                cues.append((start, end, payload))
            break
    return cues


def parse_vtt(text: str) -> list[Cue]:
    return _parse_blocks(text)


def parse_srt(text: str) -> list[Cue]:
    return _parse_blocks(text)


def parse(text: str, ext: str) -> list[Cue]:
    ext = (ext or "").lower().lstrip(".")
    if ext in ("json3", "json"):
        return parse_json3(text)
    if ext in ("vtt", "srt"):
        return _parse_blocks(text)
    # unknown: try json3, then generic timing blocks
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            return parse_json3(text)
        except ValueError:
            pass
    return _parse_blocks(text)


def parse_file(path: str, ext: str | None = None) -> list[Cue]:
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    return parse(text, ext or path.rsplit(".", 1)[-1])


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def clean_cues(cues: Iterable[Cue]) -> list[Cue]:
    """Remove rolling duplicates, merge repeats and fix overlaps."""
    ordered = sorted(cues, key=lambda c: (c[0], c[1]))
    out: list[list] = []
    prev_lines: list[str] = []
    for start, end, text in ordered:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        original = list(lines)
        # drop leading lines that repeat the end of the previous cue
        k = min(len(lines), len(prev_lines))
        while k > 0:
            if lines[:k] == prev_lines[-k:]:
                lines = lines[k:]
                break
            k -= 1
        prev_lines = original
        if not lines:
            continue
        txt = "\n".join(lines)
        if end < start:
            end = start
        if out and out[-1][2] == txt and start <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], end)  # same text continues
            continue
        out.append([start, end, txt])

    for i in range(len(out) - 1):
        nxt_start = out[i + 1][0]
        if out[i][1] > nxt_start > out[i][0]:
            out[i][1] = nxt_start
    return [(round(s, 3), round(e, 3), t) for s, e, t in out if t]


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def fmt_srt_time(seconds: float) -> str:
    ms_total = int(round(max(seconds, 0) * 1000))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_clock(seconds: float) -> str:
    total = int(max(seconds, 0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def to_srt(cues: list[Cue]) -> str:
    parts = []
    for i, (s, e, t) in enumerate(cues, 1):
        parts.append(f"{i}\n{fmt_srt_time(s)} --> {fmt_srt_time(e)}\n{t}\n")
    return "\n".join(parts)


def to_txt(cues: list[Cue], timestamps: bool = True) -> str:
    lines = []
    for s, _e, t in cues:
        one = " ".join(t.split("\n"))
        lines.append(f"[{fmt_clock(s)}] {one}" if timestamps else one)
    return "\n".join(lines) + ("\n" if lines else "")


def to_csv(cues: list[Cue]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["start", "end", "start_seconds", "end_seconds", "text"])
    for s, e, t in cues:
        w.writerow([fmt_clock(s), fmt_clock(e), f"{s:.3f}", f"{e:.3f}", " ".join(t.split("\n"))])
    return buf.getvalue()


def write_text(path: str, content: str, bom: bool = False) -> None:
    with open(path, "w", encoding="utf-8-sig" if bom else "utf-8", newline="\n") as f:
        f.write(content)
