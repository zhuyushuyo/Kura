"""Every UI string key used in the code must exist in both languages."""

from __future__ import annotations

import re
from pathlib import Path

from kura import i18n
from kura.core import citation, errors
from kura.core.presets import BUILTIN_IDS

ROOT = Path(__file__).resolve().parent.parent / "kura"


def test_literal_keys_exist():
    missing = set()
    for py in ROOT.rglob("*.py"):
        if py.name == "i18n.py":
            continue
        text = py.read_text(encoding="utf-8")
        for key in re.findall(r"\btr\(\s*\"([a-z0-9_.]+)\"", text):
            if key not in i18n.STRINGS:
                missing.add(f"{py.name}: {key}")
    assert not missing, sorted(missing)


def test_dynamic_keys_exist():
    kinds = {k for k, _ in errors._PATTERNS} | {"unknown"}
    keys = [f"err.{k}.{part}" for k in kinds for part in ("title", "advice")]
    keys += [f"preset.{p}" for p in BUILTIN_IDS] + [f"preset.{p}.desc" for p in BUILTIN_IDS]
    keys += [f"set.cite_{s}" for s in citation.STYLES]
    keys += [f"phase.{p}" for p in ("pending", "extracting", "downloading", "processing", "converting",
                                    "moving", "done", "error", "canceled", "paused")]
    keys += [f"lib.kind_{k}" for k in ("all", "video", "audio")]
    keys += [f"lib.sort_{k}" for k in ("new", "old", "published", "title", "size")]
    keys += [f"set.theme_{k}" for k in ("system", "light", "dark")]
    keys += [f"set.auth_{m}" for m in ("guest", "internal", "firefox", "chrome", "safari", "cookiefile")]
    keys += [f"set.src_{s}" for s in ("custom", "kura", "app", "path", "homebrew")]
    keys += [f"lib.field_{k}" for k in ("channel", "published", "saved", "duration", "quality", "preset",
                                        "views", "file", "extra")]
    missing = [k for k in keys if k not in i18n.STRINGS]
    assert not missing, missing


def test_both_languages_present_and_format_fields_match():
    for key, (ja, en) in i18n.STRINGS.items():
        assert isinstance(ja, str) and isinstance(en, str), key
        if key.startswith("set.filename_tokens") or key == "preset.tag":
            continue
        assert set(re.findall(r"\{(\w+)\}", ja)) == set(re.findall(r"\{(\w+)\}", en)), key


def test_tr_formats_and_falls_back():
    i18n.set_language("en")
    assert i18n.tr("queue.added_n", n=3) == "Added 3 items to the queue"
    i18n.set_language("ja")
    assert i18n.tr("queue.added_n", n=3) == "3 件をキューに追加しました"
    assert i18n.tr("no.such.key") == "no.such.key"
