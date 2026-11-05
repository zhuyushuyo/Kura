"""The download queue: scheduling, concurrency, polite delays, persistence."""

from __future__ import annotations

import datetime as _dt
import os
import random
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .. import i18n, paths
from ..core import errors, urls
from ..core.engine import JobResult, JobSpec
from ..core.presets import Preset, SubtitleOptions
from .context import AppContext
from .workers import JobThread

tr = i18n.tr

ACTIVE = ("running",)
FINISHED = ("done", "error", "canceled")


@dataclass
class Task:
    id: str
    spec: dict
    title: str = ""
    video_id: str = ""
    thumb_url: str | None = None
    thumb_path: str | None = None
    preset_name: str = ""
    status: str = "pending"          # pending | running | paused | done | error | canceled
    phase: str = ""
    phase_detail: str = ""
    fraction: float | None = None
    stage: str = ""
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: float | None = None
    error: str = ""
    error_kind: str = ""
    result: dict | None = None
    created_at: str = ""
    finished_at: str = ""
    sort_order: int = 0
    log: list = field(default_factory=list)
    canceling: bool = False

    def to_store(self) -> dict:
        d = asdict(self)
        d.pop("log", None)
        d.pop("canceling", None)
        for k in ("fraction", "speed", "eta"):
            d[k] = None
        return d

    @classmethod
    def from_store(cls, d: dict) -> "Task":
        names = set(cls.__dataclass_fields__)
        t = cls(**{k: v for k, v in d.items() if k in names})
        t.log = []
        return t

    @property
    def url(self) -> str:
        return self.spec.get("url", "")


class QueueManager(QObject):
    added = pyqtSignal(str)
    updated = pyqtSignal(str)
    removed = pyqtSignal(str)
    finished = pyqtSignal(str, bool)
    counts_changed = pyqtSignal(int, int)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.tasks: dict[str, Task] = {}
        self.threads: dict[str, JobThread] = {}
        self._order = 0
        self._last_start = 0.0
        self._next_delay = 0.0
        self._remove_after_stop: set[str] = set()
        self._lib_thumb_wait: dict[str, list[int]] = {}
        ctx.thumb_ready.connect(self.on_thumb_ready)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # -- creation ----------------------------------------------------------------------
    def build_spec(self, url: str, preset: Preset, *, task_id: str, subtitles: SubtitleOptions | None = None,
                   clip: dict | None = None, format_override: str | None = None,
                   container_override: str | None = None, write_info_json: bool | None = None,
                   subfolder: str = "", literals: dict | None = None, dest_dir: str | None = None) -> JobSpec:
        s = self.ctx.settings
        lit = {"dl_date": _dt.date.today().isoformat(), "preset": preset.tag or preset.id}
        lit.update(literals or {})
        return JobSpec(
            url=url, preset=preset.to_dict(), dest_dir=dest_dir or s.save_dir(),
            work_dir=str(paths.WORK_DIR / task_id), name_template=s.get("filename_template"),
            literals=lit, subtitles=subtitles.to_dict() if subtitles else None, clip=clip,
            format_override=format_override, container_override=container_override,
            write_info_json=s.get("write_info_json") if write_info_json is None else write_info_json,
            subfolder=subfolder,
        )

    def add(self, url: str, preset: Preset, *, title: str = "", video_id: str = "", thumb_url: str | None = None,
            thumb_bytes: bytes | None = None, start: bool = True, **spec_kwargs: Any) -> Task:
        task_id = uuid.uuid4().hex[:12]
        spec = self.build_spec(url, preset, task_id=task_id, **spec_kwargs)
        if not video_id:
            c = urls.classify(url)
            video_id = c.video_id or ""
        self._order += 1
        task = Task(id=task_id, spec=spec.to_dict(), title=title, video_id=video_id, thumb_url=thumb_url,
                    preset_name=self.ctx.preset_name(preset), status="pending" if start else "paused",
                    created_at=_now(), sort_order=self._order)
        if thumb_bytes:
            task.thumb_path = self.ctx.save_thumb_bytes(self.ctx.thumb_key(video_id, url), thumb_bytes)
        elif thumb_url:
            task.thumb_path = self.ctx.fetch_thumb(thumb_url, self.ctx.thumb_key(video_id, url))
        self.tasks[task_id] = task
        self._persist(task)
        self.added.emit(task_id)
        self._emit_counts()
        self._tick()
        return task

    # -- restore -----------------------------------------------------------------------
    def restore(self) -> int:
        resume = bool(self.ctx.settings.get("resume_queue_on_start"))
        n = 0
        for d in self.ctx.db.load_tasks():
            t = Task.from_store(d)
            if t.status in ("running", "pending", "paused"):
                t.status = "pending" if resume else "paused"
                t.phase = ""
                n += 1
            self._order = max(self._order, t.sort_order)
            self.tasks[t.id] = t
            self.added.emit(t.id)
        self._cleanup_orphan_workdirs()
        self._emit_counts()
        return n

    def _cleanup_orphan_workdirs(self) -> None:
        keep = {t.id for t in self.tasks.values() if t.status in ("pending", "paused", "running")}
        try:
            for entry in os.scandir(paths.WORK_DIR):
                if entry.is_dir() and entry.name not in keep:
                    if time.time() - entry.stat().st_mtime > 3600:
                        shutil.rmtree(entry.path, ignore_errors=True)
        except OSError:
            pass

    # -- scheduling --------------------------------------------------------------------------
    def running_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.status == "running")

    def pending_tasks(self) -> list[Task]:
        return sorted((t for t in self.tasks.values() if t.status == "pending"), key=lambda t: t.sort_order)

    def _tick(self) -> None:
        limit = max(1, min(int(self.ctx.settings.get("concurrency", 2) or 1), 6))
        pending = self.pending_tasks()
        if not pending:
            return
        now = time.monotonic()
        if self.running_count() >= limit:
            return
        if not self.ctx.tools_ready:
            return  # wait for ffmpeg / deno detection
        if now < self._last_start + self._next_delay:
            remaining = int(self._last_start + self._next_delay - now) + 1
            for t in pending[:1]:
                detail = tr("phase.waiting_delay", s=remaining)
                if t.phase_detail != detail:
                    t.phase_detail = detail
                    self.updated.emit(t.id)
            return
        self._start(pending[0])
        polite = bool(self.ctx.settings.get("polite_delay", True))
        if polite and len(pending) > 1:
            lo = float(self.ctx.settings.get("delay_min", 5) or 0)
            hi = max(lo, float(self.ctx.settings.get("delay_max", 10) or 0))
            self._next_delay = random.uniform(lo, hi)
        else:
            self._next_delay = 0.0
        self._last_start = now

    def _start(self, task: Task) -> None:
        spec = JobSpec.from_dict(task.spec)
        th = JobThread(task.id, spec, self.ctx.tool_config(), self.ctx.auth_config(), self)
        th.phase_changed.connect(self._on_phase)
        th.progressed.connect(self._on_progress)
        th.logged.connect(self._on_log)
        th.info_ready.connect(self._on_info)
        th.succeeded.connect(self._on_success)
        th.failed.connect(self._on_failed)
        th.stopped.connect(self._on_stopped)
        th.finished.connect(lambda tid=task.id: self._thread_finished(tid))
        self.threads[task.id] = th
        task.status = "running"
        task.phase = "extracting"
        task.phase_detail = ""
        task.fraction = None
        task.error = task.error_kind = ""
        task.canceling = False
        self._persist(task)
        self.updated.emit(task.id)
        self._emit_counts()
        th.start()

    def _thread_finished(self, task_id: str) -> None:
        th = self.threads.pop(task_id, None)
        if th is not None:
            th.deleteLater()
        if task_id in self._remove_after_stop:
            self._remove_after_stop.discard(task_id)
            self._really_remove(task_id)
        self._emit_counts()
        QTimer.singleShot(0, self._tick)

    # -- thread callbacks ----------------------------------------------------------------------
    def _on_phase(self, task_id: str, phase: str, detail: str) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        t.phase = phase
        if phase == "processing":
            key = f"pp.{detail}"
            t.phase_detail = tr(key) if key in i18n.STRINGS else tr("pp.other")
        else:
            t.phase_detail = ""
        if phase in ("processing", "moving", "converting", "extracting"):
            t.fraction = None
        self.updated.emit(task_id)

    def _on_progress(self, task_id: str, data: dict) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        t.stage = data.get("stage", "")
        t.fraction = data.get("fraction")
        if t.stage == "download":
            t.downloaded = data.get("downloaded") or 0
            t.total = data.get("total")
            t.speed = data.get("speed")
            t.eta = data.get("eta")
        elif t.stage == "convert":
            t.phase_detail = data.get("label") or ""
            t.speed = None
            t.eta = None
        else:
            t.speed = None
            t.eta = None
        self.updated.emit(task_id)

    def _on_log(self, task_id: str, line: str) -> None:
        t = self.tasks.get(task_id)
        if t:
            t.log.append(line)
            if len(t.log) > 600:
                del t.log[:100]

    def _on_info(self, task_id: str, summary: dict) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        if summary.get("title"):
            t.title = summary["title"]
        if summary.get("id") and not t.video_id:
            t.video_id = summary["id"]
        if summary.get("thumbnail") and not t.thumb_path:
            t.thumb_url = summary["thumbnail"]
            t.thumb_path = self.ctx.fetch_thumb(t.thumb_url, self.ctx.thumb_key(t.video_id, t.url))
        self._persist(t)
        self.updated.emit(task_id)

    def on_thumb_ready(self, key: str, path: str) -> None:
        for t in self.tasks.values():
            if not t.thumb_path and self.ctx.thumb_key(t.video_id, t.url) == key:
                t.thumb_path = path
                self._persist(t)
                self.updated.emit(t.id)
        waiting = self._lib_thumb_wait.pop(key, None)
        if waiting:
            for item_id in waiting:
                self.ctx.db.update_item(item_id, thumb_path=path)
            self.ctx.library_changed.emit()

    def _on_success(self, task_id: str, result: JobResult) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        t.status = "done"
        t.phase = "done"
        t.phase_detail = ""
        t.fraction = 1.0
        t.finished_at = _now()
        if result.meta.get("title"):
            t.title = result.meta["title"]
        t.result = {"files": result.files, "extra_files": result.extra_files, "actual": result.actual,
                    "encoder": result.encoder, "warnings": result.warnings}
        self._persist(t)
        self._add_to_library(t, result)
        self.updated.emit(task_id)
        self.finished.emit(task_id, True)
        self._emit_counts()

    def _on_failed(self, task_id: str, message: str, kind: str, log_lines: list) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        t.status = "error"
        t.phase = "error"
        t.error = errors.clean_message(message)
        t.error_kind = kind
        t.finished_at = _now()
        if log_lines:
            t.log = list(log_lines)[-600:]
        self._write_task_log(t)
        self._persist(t)
        self.updated.emit(task_id)
        self.finished.emit(task_id, False)
        self._emit_counts()

    def _on_stopped(self, task_id: str, paused: bool) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        t.canceling = False
        t.fraction = None
        t.speed = t.eta = None
        if paused:
            t.status = "paused"
            t.phase = "paused"
        else:
            t.status = "canceled"
            t.phase = "canceled"
            shutil.rmtree(t.spec.get("work_dir", ""), ignore_errors=True)
        t.phase_detail = ""
        self._persist(t)
        self.updated.emit(task_id)
        self._emit_counts()

    # -- library ----------------------------------------------------------------------
    def _add_to_library(self, t: Task, result: JobResult) -> None:
        meta = result.meta or {}
        preset = Preset.from_dict(t.spec["preset"])
        actual = result.actual or {}
        thumb_key = self.ctx.thumb_key(meta.get("id") or t.video_id, t.url)
        thumb = t.thumb_path or self.ctx.fetch_thumb(result.thumbnail_url, thumb_key)
        subs = (t.spec.get("subtitles") or {}).get("langs")
        rec = {
            "video_id": meta.get("id") or t.video_id, "extractor": meta.get("extractor_key"),
            "url": meta.get("webpage_url") or t.url, "title": meta.get("title") or t.title,
            "channel": meta.get("channel") or meta.get("uploader"), "channel_url": meta.get("channel_url"),
            "upload_date": meta.get("upload_date"), "duration": meta.get("duration"),
            "preset_id": preset.id, "preset_name": t.preset_name, "kind": preset.kind,
            "file_path": result.files[0] if result.files else None,
            "extra_files": result.files[1:] + result.extra_files, "file_size": actual.get("size"),
            "width": actual.get("width"), "height": actual.get("height"), "vcodec": actual.get("vcodec"),
            "acodec": actual.get("acodec"), "fps": actual.get("fps"), "encoder": result.encoder,
            "thumb_path": thumb, "playlist_title": meta.get("playlist_title"),
            "description": meta.get("description"), "tags": meta.get("tags") or [],
            "view_count": meta.get("view_count"), "like_count": meta.get("like_count"),
            "subtitles": subs or [], "clip": t.spec.get("clip"), "meta_json": meta,
        }
        try:
            item_id = self.ctx.db.add_item(rec)
        except Exception:  # noqa: BLE001
            return
        if not thumb and result.thumbnail_url:
            self._lib_thumb_wait.setdefault(thumb_key, []).append(item_id)
        self.ctx.library_changed.emit()

    def _write_task_log(self, t: Task) -> None:
        try:
            d = paths.LOG_DIR / "tasks"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{t.id}.log", "w", encoding="utf-8") as f:
                f.write(f"url: {t.url}\nerror: {t.error}\n\n")
                f.write("\n".join(t.log))
        except OSError:
            pass

    # -- user actions ------------------------------------------------------------------------
    def pause(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        if t.status == "running":
            th = self.threads.get(task_id)
            if th:
                t.canceling = True
                th.cancel(pause=True)
                self.updated.emit(task_id)
        elif t.status == "pending":
            t.status = "paused"
            t.phase = "paused"
            self._persist(t)
            self.updated.emit(task_id)
        self._emit_counts()

    def resume(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if t and t.status in ("paused", "canceled", "error"):
            t.status = "pending"
            t.phase = ""
            t.phase_detail = ""
            t.error = t.error_kind = ""
            t.fraction = None
            self._persist(t)
            self.updated.emit(task_id)
            self._emit_counts()
            self._tick()

    retry = resume

    def cancel(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        if t.status == "running":
            th = self.threads.get(task_id)
            if th:
                t.canceling = True
                th.cancel(pause=False)
                self.updated.emit(task_id)
        elif t.status in ("pending", "paused"):
            t.status = "canceled"
            t.phase = "canceled"
            shutil.rmtree(t.spec.get("work_dir", ""), ignore_errors=True)
            self._persist(t)
            self.updated.emit(task_id)
        self._emit_counts()

    def remove(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        if t.status == "running":
            self._remove_after_stop.add(task_id)
            self.cancel(task_id)
            return
        if t.status in ("pending", "paused"):
            shutil.rmtree(t.spec.get("work_dir", ""), ignore_errors=True)
        self._really_remove(task_id)

    def _really_remove(self, task_id: str) -> None:
        if self.tasks.pop(task_id, None) is not None:
            self.ctx.db.delete_task(task_id)
            self.removed.emit(task_id)
            self._emit_counts()

    def pause_all(self) -> None:
        for tid, t in list(self.tasks.items()):
            if t.status in ("running", "pending"):
                self.pause(tid)

    def resume_all(self) -> None:
        for tid, t in list(self.tasks.items()):
            if t.status == "paused":
                self.resume(tid)

    def clear_finished(self) -> None:
        for tid, t in list(self.tasks.items()):
            if t.status in ("done", "canceled"):
                self._really_remove(tid)

    def stop_all_for_exit(self, timeout_ms: int = 8000) -> None:
        for tid, t in list(self.tasks.items()):
            if t.status == "running":
                self.pause(tid)
        deadline = time.monotonic() + timeout_ms / 1000
        for th in list(self.threads.values()):
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            if not th.wait(remaining):
                th.terminate()  # last resort at exit; partial files stay in the work folder
                th.wait(2000)
        for t in self.tasks.values():
            if t.status == "running":
                t.status = "paused"
            self._persist(t)

    # -- helpers --------------------------------------------------------------------------
    def _persist(self, t: Task) -> None:
        try:
            self.ctx.db.save_task(t.to_store())
        except Exception:  # noqa: BLE001
            pass

    def _emit_counts(self) -> None:
        active = sum(1 for t in self.tasks.values() if t.status in ("running", "pending"))
        self.counts_changed.emit(active, len(self.tasks))

    def total_speed(self) -> float:
        return sum(t.speed or 0 for t in self.tasks.values() if t.status == "running")

    def ordered(self) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda t: t.sort_order)


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")
