"""SQLite storage: the download library and the persistent queue."""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT, extractor TEXT, url TEXT, title TEXT, channel TEXT, channel_url TEXT,
    upload_date TEXT, duration REAL, preset_id TEXT, preset_name TEXT, kind TEXT,
    file_path TEXT, extra_files TEXT, file_size INTEGER,
    width INTEGER, height INTEGER, vcodec TEXT, acodec TEXT, fps REAL, encoder TEXT,
    thumb_path TEXT, playlist_title TEXT, description TEXT, tags TEXT,
    view_count INTEGER, like_count INTEGER, subtitles TEXT, clip TEXT,
    downloaded_at TEXT, meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_library_video ON library(video_id, preset_id);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, status TEXT, url TEXT,
    title TEXT, thumb_path TEXT, data TEXT, sort_order INTEGER
);
"""

LIBRARY_COLUMNS = (
    "video_id", "extractor", "url", "title", "channel", "channel_url", "upload_date", "duration",
    "preset_id", "preset_name", "kind", "file_path", "extra_files", "file_size", "width", "height",
    "vcodec", "acodec", "fps", "encoder", "thumb_path", "playlist_title", "description", "tags",
    "view_count", "like_count", "subtitles", "clip", "downloaded_at", "meta_json",
)


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else paths.DB_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- library -----------------------------------------------------------------
    def add_item(self, record: dict[str, Any]) -> int:
        rec = dict(record)
        for key in ("extra_files", "tags", "subtitles", "clip", "meta_json"):
            if key in rec and not isinstance(rec[key], (str, type(None))):
                rec[key] = json.dumps(rec[key], ensure_ascii=False, default=str)
        rec.setdefault("downloaded_at", now_iso())
        cols = [c for c in LIBRARY_COLUMNS if c in rec]
        sql = f"INSERT INTO library ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        cur = self.conn.execute(sql, [rec[c] for c in cols])
        self.conn.commit()
        return int(cur.lastrowid)

    def get_item(self, item_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM library WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def delete_item(self, item_id: int) -> None:
        self.conn.execute("DELETE FROM library WHERE id=?", (item_id,))
        self.conn.commit()

    def update_item(self, item_id: int, **values: Any) -> None:
        if not values:
            return
        cols = ", ".join(f"{k}=?" for k in values)
        self.conn.execute(f"UPDATE library SET {cols} WHERE id=?", [*values.values(), item_id])
        self.conn.commit()

    def search(self, text: str = "", kind: str = "all", sort: str = "new", limit: int = 5000) -> list[dict]:
        where, args = [], []
        if text:
            like = f"%{text}%"
            where.append("(title LIKE ? OR channel LIKE ? OR playlist_title LIKE ? OR tags LIKE ? OR video_id LIKE ?)")
            args += [like] * 5
        if kind in ("video", "audio"):
            where.append("kind=?")
            args.append(kind)
        order = {
            "new": "downloaded_at DESC, id DESC",
            "old": "downloaded_at ASC, id ASC",
            "title": "title COLLATE NOCASE ASC",
            "published": "upload_date DESC",
            "size": "file_size DESC",
        }.get(sort, "downloaded_at DESC, id DESC")
        sql = "SELECT * FROM library"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def all_items(self) -> list[dict]:
        return self.search(limit=1_000_000)

    def find_downloaded(self, video_id: str, preset_id: str | None = None) -> dict | None:
        """A previous download of ``video_id`` whose file still exists."""
        if not video_id:
            return None
        sql = "SELECT * FROM library WHERE video_id=?"
        args: list[Any] = [video_id]
        if preset_id:
            sql += " AND preset_id=?"
            args.append(preset_id)
        sql += " ORDER BY id DESC"
        for row in self.conn.execute(sql, args).fetchall():
            if row["file_path"] and os.path.exists(row["file_path"]):
                return dict(row)
        return None

    def downloaded_ids(self, video_ids: Iterable[str], preset_id: str | None = None) -> set[str]:
        out = set()
        for vid in video_ids:
            if self.find_downloaded(vid, preset_id):
                out.add(vid)
        return out

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM library").fetchone()[0])

    # -- tasks (queue persistence) ---------------------------------------------
    def save_task(self, task: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO tasks (id, created_at, updated_at, status, url, title, thumb_path, data, sort_order) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, "
            "status=excluded.status, url=excluded.url, title=excluded.title, thumb_path=excluded.thumb_path, "
            "data=excluded.data, sort_order=excluded.sort_order",
            (task["id"], task.get("created_at") or now_iso(), now_iso(), task.get("status"), task.get("url"),
             task.get("title"), task.get("thumb_path"), json.dumps(task, ensure_ascii=False, default=str),
             task.get("sort_order", 0)),
        )
        self.conn.commit()

    def delete_task(self, task_id: str) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.conn.commit()

    def load_tasks(self) -> list[dict]:
        out = []
        for row in self.conn.execute("SELECT data FROM tasks ORDER BY sort_order, created_at").fetchall():
            try:
                out.append(json.loads(row["data"]))
            except (TypeError, ValueError):
                continue
        return out
