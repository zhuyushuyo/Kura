"""Keep the download engine (yt-dlp + EJS + Deno) current."""

from __future__ import annotations

import json
import re
from typing import Callable

from . import tools

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
ENGINE_PACKAGES = ["yt-dlp[default]", "deno"]


def parse_version(v: str) -> tuple:
    """yt-dlp uses date versions: 2026.10.26 / 2026.11.4.232659.dev0."""
    nums = [int(x) for x in re.findall(r"\d+", v or "")]
    dev = 1 if "dev" in (v or "") else 0
    # a .devN build is newer than the same-day stable build in yt-dlp's scheme
    return tuple(nums[:4] + [0] * (4 - min(len(nums), 4))) + (dev,)


def installed() -> str:
    return tools.package_version("yt-dlp")


def latest(channel: str = "stable", timeout: float = 5) -> str | None:
    try:
        data = json.loads(tools.http_get(PYPI_URL, timeout=timeout))
    except Exception:  # noqa: BLE001
        return None
    if channel != "nightly":
        return data.get("info", {}).get("version")
    versions = [v for v, files in (data.get("releases") or {}).items() if files]
    return max(versions, key=parse_version) if versions else data.get("info", {}).get("version")


def check(channel: str = "stable", timeout: float = 5) -> tuple[str, str | None, bool]:
    """Return (installed, latest, update_available)."""
    cur = installed()
    new = latest(channel, timeout)
    if not new or not cur:
        return cur, new, False
    return cur, new, parse_version(new) > parse_version(cur)


def update(channel: str, log: Callable[[str], None]) -> bool:
    return tools.pip_install(ENGINE_PACKAGES, log, upgrade=True, prerelease=(channel == "nightly"))


def install_deno(log: Callable[[str], None]) -> bool:
    return tools.pip_install(["deno"], log, upgrade=True)
