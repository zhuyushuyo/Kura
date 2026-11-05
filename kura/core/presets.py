"""Output presets: what to download and what file to produce."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any

VIDEO_CONTAINERS = ("mp4", "mkv")
AUDIO_FORMATS = ("m4a", "mp3", "wav", "opus", "flac")


@dataclass
class Preset:
    id: str
    name: str = ""                 # display name for custom presets
    kind: str = "video"            # video | audio
    max_res: int | None = None     # cap on the *smaller* side (handles vertical video)
    fps_max: int | None = None
    vcodec: str = "copy"           # copy | h264 | hevc
    acodec: str = "copy"           # video: copy | aac ; audio: m4a | mp3 | wav | opus | flac
    container: str = "mkv"         # video: mp4 | mkv ; audio: same as acodec
    audio_quality: str = "320"     # kbps for lossy audio
    prefer_sdr: bool = False
    prefer_h264_source: bool = False
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    embed_chapters: bool = True
    tag: str = ""                  # value of the {preset} file-name token
    builtin: bool = False

    # -- serialisation -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Preset":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    # -- behaviour -------------------------------------------------------------
    @property
    def is_audio(self) -> bool:
        return self.kind == "audio"

    @property
    def needs_transcode_step(self) -> bool:
        """Video presets that end in Kura's own ffmpeg step (codec/MP4 fix)."""
        return not self.is_audio and (self.vcodec != "copy" or self.acodec != "copy" or self.container == "mp4")

    def ext(self) -> str:
        return self.acodec if self.is_audio else self.container

    def format_selection(self, ffmpeg_ok: bool) -> tuple[str, list[str], str | None]:
        """Return (format, format_sort, merge_output_format) for yt-dlp."""
        if self.is_audio:
            sort = ["+acodec:m4a"] if self.acodec == "m4a" else []
            if not ffmpeg_ok:
                return "ba[ext=m4a]/ba/b", sort, None
            return "ba/b", sort, None

        sort: list[str] = []
        if self.max_res:
            sort.append(f"res:{int(self.max_res)}")
        if self.fps_max:
            sort.append(f"fps:{int(self.fps_max)}")
        if self.prefer_sdr:
            sort.append("hdr:sdr")
        if self.prefer_h264_source or self.vcodec == "h264":
            sort += ["+vcodec:avc", "+acodec:m4a"]
        elif self.vcodec == "hevc":
            sort += ["+vcodec:hevc", "+acodec:m4a"]
        if not ffmpeg_ok:
            # Only pre-merged single files can be used without ffmpeg.
            return "b/bv*+ba", sort, None
        return "bv*+ba/b", sort, "mkv"


def _builtins() -> list[Preset]:
    return [
        Preset("edit1080", kind="video", max_res=1080, vcodec="h264", acodec="aac", container="mp4",
               prefer_sdr=True, prefer_h264_source=True, tag="1080p", builtin=True),
        Preset("edit4k", kind="video", max_res=2160, vcodec="h264", acodec="aac", container="mp4",
               prefer_sdr=True, tag="4K", builtin=True),
        Preset("best", kind="video", vcodec="copy", acodec="copy", container="mkv", tag="best", builtin=True),
        Preset("audio_m4a", kind="audio", acodec="m4a", container="m4a", tag="m4a", builtin=True),
        Preset("audio_mp3", kind="audio", acodec="mp3", container="mp3", audio_quality="320", tag="mp3",
               builtin=True),
        Preset("audio_wav", kind="audio", acodec="wav", container="wav", embed_thumbnail=False, tag="wav",
               builtin=True),
    ]


BUILTIN_IDS = tuple(p.id for p in _builtins())


def builtin_presets(edit4k_codec: str = "h264") -> list[Preset]:
    out = _builtins()
    for p in out:
        if p.id == "edit4k" and edit4k_codec in ("h264", "hevc"):
            p.vcodec = edit4k_codec
    return out


def all_presets(settings_get) -> list[Preset]:
    """Built-in + custom presets (``settings_get`` is ``Settings.get``)."""
    presets = builtin_presets(settings_get("edit4k_codec", "h264"))
    for raw in settings_get("custom_presets", []) or []:
        try:
            p = Preset.from_dict(raw)
            p.builtin = False
            presets.append(p)
        except TypeError:
            continue
    return presets


def find_preset(settings_get, preset_id: str) -> Preset:
    for p in all_presets(settings_get):
        if p.id == preset_id:
            return p
    return all_presets(settings_get)[0]


def new_custom_preset(base: Preset | None = None, name: str = "") -> Preset:
    data: dict[str, Any] = base.to_dict() if base else Preset("tmp").to_dict()
    data.update(id="custom-" + uuid.uuid4().hex[:8], name=name or "Custom", builtin=False)
    if not data.get("tag"):
        data["tag"] = "custom"
    return Preset.from_dict(data)


@dataclass
class SubtitleOptions:
    langs: list[str] = field(default_factory=lambda: ["ja", "en"])
    auto: bool = True
    mode: str = "file"     # file | embed | both
    txt: bool = True
    csv: bool = False

    @property
    def keep_files(self) -> bool:
        return self.mode in ("file", "both")

    @property
    def embed(self) -> bool:
        return self.mode in ("embed", "both")

    @classmethod
    def from_settings(cls, data: dict) -> "SubtitleOptions":
        langs = [x.strip() for x in str(data.get("langs", "ja,en")).replace("、", ",").split(",") if x.strip()]
        return cls(langs=langs or ["ja"], auto=bool(data.get("auto", True)), mode=data.get("mode", "file"),
                   txt=bool(data.get("txt", True)), csv=bool(data.get("csv", False)))

    def to_dict(self) -> dict:
        return asdict(self)
