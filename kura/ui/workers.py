"""Background threads (Qt) around the Qt-free core."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal

from ..core import tools
from ..core.engine import (
    AuthConfig,
    DownloadJob,
    InfoError,
    JobCancelled,
    JobFailed,
    JobListener,
    JobSpec,
    ToolConfig,
    fetch_info,
    pick_thumbnail,
)


class JobThread(QThread):
    phase_changed = pyqtSignal(str, str, str)       # task_id, phase, detail
    progressed = pyqtSignal(str, dict)              # task_id, data
    logged = pyqtSignal(str, str)                   # task_id, line
    info_ready = pyqtSignal(str, dict)              # task_id, summary
    succeeded = pyqtSignal(str, object)             # task_id, JobResult
    failed = pyqtSignal(str, str, str, list)        # task_id, message, kind, log lines
    stopped = pyqtSignal(str, bool)                 # task_id, paused

    def __init__(self, task_id: str, spec: JobSpec, tools_cfg: ToolConfig, auth: AuthConfig, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        thread = self

        class _Listener(JobListener):
            def phase(self, phase: str, detail: str = "") -> None:
                thread.phase_changed.emit(task_id, phase, detail)

            def progress(self, data: dict) -> None:
                thread.progressed.emit(task_id, data)

            def log(self, line: str) -> None:
                thread.logged.emit(task_id, line)

            def info(self, summary: dict) -> None:
                thread.info_ready.emit(task_id, summary)

        self.job = DownloadJob(spec, tools_cfg, auth, _Listener())

    def cancel(self, pause: bool = False) -> None:
        self.job.cancel(pause=pause)

    def run(self) -> None:  # noqa: D401
        try:
            result = self.job.run()
        except JobCancelled as e:
            self.stopped.emit(self.task_id, e.paused)
            return
        except JobFailed as e:
            self.failed.emit(self.task_id, e.message, e.kind, list(self.job.log_lines))
            return
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            self.failed.emit(self.task_id, f"{type(e).__name__}: {e}", "unknown", list(self.job.log_lines) + [tb])
            return
        self.succeeded.emit(self.task_id, result)


class InfoThread(QThread):
    """Fetch video (or flat playlist) information plus thumbnail bytes."""

    done = pyqtSignal(int, dict, bytes)
    failed = pyqtSignal(int, str, str)

    def __init__(self, req_id: int, url: str, tools_cfg: ToolConfig, auth: AuthConfig, *, flat: bool = False,
                 noplaylist: bool = True, playlist_end: int | None = None, want_thumb: bool = True, parent=None):
        super().__init__(parent)
        self.req_id = req_id
        self.url = url
        self.tools_cfg = tools_cfg
        self.auth = auth
        self.flat = flat
        self.noplaylist = noplaylist
        self.playlist_end = playlist_end
        self.want_thumb = want_thumb

    def run(self) -> None:
        try:
            info = fetch_info(self.url, self.tools_cfg, self.auth, flat=self.flat, noplaylist=self.noplaylist,
                              playlist_end=self.playlist_end)
        except InfoError as e:
            self.failed.emit(self.req_id, e.message, e.kind)
            return
        except Exception as e:  # noqa: BLE001
            self.failed.emit(self.req_id, f"{type(e).__name__}: {e}", "unknown")
            return
        thumb = b""
        if self.want_thumb:
            url = pick_thumbnail(info)
            if url:
                try:
                    thumb = tools.http_get(url, timeout=10)
                except Exception:  # noqa: BLE001
                    thumb = b""
        self.done.emit(self.req_id, info, thumb)


class FuncThread(QThread):
    """Run ``func(log, progress, cancelled)`` in the background."""

    log = pyqtSignal(str)
    progress = pyqtSignal(object, object)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func: Callable[..., Any], parent=None):
        super().__init__(parent)
        self.func = func
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            res = self.func(self.log.emit, lambda a, b: self.progress.emit(int(a), int(b)), lambda: self._cancel)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")
            return
        self.done.emit(res)
