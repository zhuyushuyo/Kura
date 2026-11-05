"""Queue page."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, paths
from .context import AppContext
from .dialogs import TaskDetailsDialog
from .queue_manager import QueueManager, Task
from .theme import THEME, human_duration, human_size
from .widgets import ElidedLabel, EmptyState, ThumbLabel, flat_button, label

tr = i18n.tr


def describe(task: Task) -> tuple[str, str]:
    """(status text, style object name) for a task."""
    st = task.status
    if task.canceling:
        return tr("phase.canceling"), "Muted"
    if st == "pending":
        return task.phase_detail or tr("phase.pending"), "Muted"
    if st == "paused":
        return tr("phase.paused"), "Muted"
    if st == "canceled":
        return tr("phase.canceled"), "Muted"
    if st == "error":
        kind = task.error_kind or "unknown"
        return f"{tr('phase.error')}: {tr(f'err.{kind}.title')}", "Error"
    if st == "done":
        a = (task.result or {}).get("actual") or {}
        bits = []
        if a.get("height"):
            bits.append(f"{min(a.get('height') or 0, a.get('width') or 99999)}p")
        codecs = "/".join(x.upper() if x in ("aac", "mp3", "opus") else x.upper().replace("H264", "H.264")
                          for x in (a.get("vcodec"), a.get("acodec")) if x)
        if codecs:
            bits.append(codecs)
        if a.get("size"):
            bits.append(human_size(a["size"]))
        return tr("queue.done_summary", what=" · ".join(bits) or "✓"), "Success"
    # running
    phase = task.phase or "extracting"
    text = tr(f"phase.{phase}") if f"phase.{phase}" in i18n.STRINGS else phase
    if phase == "processing" and task.phase_detail:
        text = task.phase_detail
    if phase == "converting" and task.phase_detail:
        text = f"{text}（{task.phase_detail}）" if i18n.language() == "ja" else f"{text} ({task.phase_detail})"
    if task.fraction is not None and phase in ("downloading", "converting", "moving"):
        text += f"  {task.fraction * 100:.0f}%"
    if phase == "downloading":
        extra = []
        if task.total:
            extra.append(f"{human_size(task.downloaded)} / {human_size(task.total)}")
        elif task.downloaded:
            extra.append(human_size(task.downloaded))
        if task.speed:
            extra.append(f"{human_size(task.speed)}/s")
        if task.eta:
            extra.append(tr("queue.eta", t=human_duration(task.eta)))
        if extra:
            text += " · " + " · ".join(extra)
    return text, "Muted"


class TaskRow(QFrame):
    def __init__(self, page: "QueuePage", task: Task):
        super().__init__()
        self.page = page
        self.task_id = task.id
        self.setObjectName("TaskRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(12)
        self.thumb = ThumbLabel(112, 63, 7)
        lay.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.title = ElidedLabel("", Qt.TextElideMode.ElideRight)
        self.title.setObjectName("H3")
        col.addWidget(self.title)
        meta = QHBoxLayout()
        meta.setSpacing(8)
        self.chip = QLabel()
        self.chip.setObjectName("Chip")
        meta.addWidget(self.chip)
        self.status = ElidedLabel("", Qt.TextElideMode.ElideRight)
        meta.addWidget(self.status, 1)
        col.addLayout(meta)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        col.addWidget(self.bar)
        lay.addLayout(col, 1)
        btns = QHBoxLayout()
        btns.setSpacing(2)
        self.b_pause = flat_button("ph.pause", tr("queue.pause"))
        self.b_retry = flat_button("ph.arrow-counter-clockwise", tr("queue.retry"))
        self.b_open = flat_button("ph.folder-open", tr("queue.open_folder"))
        self.b_info = flat_button("ph.info", tr("queue.details"))
        self.b_cancel = flat_button("ph.x", tr("queue.cancel"))
        for b in (self.b_pause, self.b_retry, self.b_open, self.b_info, self.b_cancel):
            btns.addWidget(b)
        lay.addLayout(btns)
        q = page.queue
        self.b_pause.clicked.connect(self._pause_resume)
        self.b_retry.clicked.connect(lambda: q.retry(self.task_id))
        self.b_open.clicked.connect(self._open)
        self.b_info.clicked.connect(lambda: page.show_details(self.task_id))
        self.b_cancel.clicked.connect(self._cancel_remove)
        self._thumb_shown = None
        self.refresh(task)

    def _pause_resume(self) -> None:
        t = self.page.queue.tasks.get(self.task_id)
        if not t:
            return
        if t.status in ("running", "pending"):
            self.page.queue.pause(self.task_id)
        else:
            self.page.queue.resume(self.task_id)

    def _cancel_remove(self) -> None:
        t = self.page.queue.tasks.get(self.task_id)
        if not t:
            return
        if t.status in ("running", "pending", "paused"):
            self.page.queue.cancel(self.task_id)
        else:
            self.page.queue.remove(self.task_id)

    def _open(self) -> None:
        t = self.page.queue.tasks.get(self.task_id)
        files = (t.result or {}).get("files") if t else None
        if files and os.path.exists(files[0]):
            paths.reveal_in_file_manager(files[0])
        elif t:
            paths.open_path(t.spec.get("dest_dir") or self.page.ctx.settings.save_dir())

    def refresh(self, t: Task) -> None:
        self.title.setText(t.title or tr("queue.untitled") + "  " + t.url)
        self.chip.setText(t.preset_name)
        text, style = describe(t)
        self.status.setText(text)
        if self.status.objectName() != style:
            self.status.setObjectName(style)
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
        if t.status == "running" and t.fraction is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int((t.fraction or (1 if t.status == "done" else 0)) * 1000))
        self.bar.setVisible(t.status in ("running", "pending", "paused"))
        running = t.status in ("running", "pending")
        THEME.bind_icon(self.b_pause, "ph.pause" if running else "ph.play")
        self.b_pause.setToolTip(tr("queue.pause") if running else tr("queue.resume"))
        self.b_pause.setVisible(t.status in ("running", "pending", "paused"))
        self.b_retry.setVisible(t.status in ("error", "canceled"))
        self.b_open.setVisible(t.status == "done")
        self.b_cancel.setToolTip(tr("queue.cancel") if t.status in ("running", "pending", "paused") else tr("queue.remove"))
        THEME.bind_icon(self.b_cancel, "ph.x" if t.status in ("running", "pending", "paused") else "ph.trash")
        if t.thumb_path and self._thumb_shown != t.thumb_path:
            if self.thumb.set_image_file(t.thumb_path):
                self._thumb_shown = t.thumb_path
        elif not t.thumb_path and self._thumb_shown is None:
            preset_audio = (t.spec.get("preset") or {}).get("kind") == "audio"
            self.thumb.clear_thumb("ph.music-notes" if preset_audio else "ph.film-strip")


class QueuePage(QWidget):
    add_urls_requested = pyqtSignal(str)

    def __init__(self, ctx: AppContext, queue: QueueManager):
        super().__init__()
        self.setObjectName("Page")
        self.ctx = ctx
        self.queue = queue
        self.rows: dict[str, TaskRow] = {}
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 12)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label(tr("queue.title"), "Title"))
        self.summary = label("", "Muted")
        head.addWidget(self.summary)
        head.addStretch(1)
        b_add = QPushButton(tr("queue.add_urls"))
        b_add.setObjectName("Primary")
        THEME.bind_icon(b_add, "ph.plus", "accent_text")
        b_add.clicked.connect(lambda: self.add_urls_requested.emit(""))
        b_pause = QPushButton(tr("queue.pause_all"))
        THEME.bind_icon(b_pause, "ph.pause")
        b_pause.clicked.connect(queue.pause_all)
        b_resume = QPushButton(tr("queue.resume_all"))
        THEME.bind_icon(b_resume, "ph.play")
        b_resume.clicked.connect(queue.resume_all)
        b_clear = QPushButton(tr("queue.clear_done"))
        THEME.bind_icon(b_clear, "ph.eraser")
        b_clear.clicked.connect(queue.clear_finished)
        head.addWidget(QLabel(tr("queue.concurrency")))
        self.conc = QSpinBox()
        self.conc.setRange(1, 6)
        self.conc.setValue(int(ctx.settings.get("concurrency", 2)))
        self.conc.valueChanged.connect(lambda v: ctx.set("concurrency", v))
        head.addWidget(self.conc)
        for b in (b_pause, b_resume, b_clear, b_add):
            head.addWidget(b)
        lay.addLayout(head)

        self.stack = QStackedWidget()
        self.empty = EmptyState("ph.tray", tr("queue.empty_title"), tr("queue.empty_body"))
        self.stack.addWidget(self.empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.list_layout = QVBoxLayout(inner)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        scroll.setWidget(inner)
        self.stack.addWidget(scroll)
        lay.addWidget(self.stack, 1)

        queue.added.connect(self._added)
        queue.updated.connect(self._updated)
        queue.removed.connect(self._removed)
        queue.counts_changed.connect(self._counts)
        ctx.settings_changed.connect(lambda k: self.conc.setValue(int(ctx.settings.get("concurrency", 2)))
                                     if k == "concurrency" else None)
        sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._paste)
        for t in queue.ordered():
            self._added(t.id)
        self._counts(0, len(queue.tasks))

    def _paste(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        self.add_urls_requested.emit(QGuiApplication.clipboard().text())

    def _added(self, task_id: str) -> None:
        t = self.queue.tasks.get(task_id)
        if not t or task_id in self.rows:
            return
        row = TaskRow(self, t)
        self.rows[task_id] = row
        self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self.stack.setCurrentIndex(1)

    def _updated(self, task_id: str) -> None:
        t = self.queue.tasks.get(task_id)
        row = self.rows.get(task_id)
        if t and row:
            row.refresh(t)

    def _removed(self, task_id: str) -> None:
        row = self.rows.pop(task_id, None)
        if row:
            row.deleteLater()
        if not self.rows:
            self.stack.setCurrentIndex(0)

    def _counts(self, active: int, total: int) -> None:
        running = self.queue.running_count()
        self.summary.setText(tr("queue.summary", active=running, total=total))
        self.stack.setCurrentIndex(1 if self.rows else 0)

    def show_details(self, task_id: str) -> None:
        t = self.queue.tasks.get(task_id)
        if t:
            TaskDetailsDialog(self.ctx, t, on_retry=lambda: self.queue.retry(task_id), parent=self).exec()

    # drag & drop
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        md = e.mimeData()
        text = "\n".join(u.toString() for u in md.urls()) if md.hasUrls() else md.text()
        self.add_urls_requested.emit(text)
