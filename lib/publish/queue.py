"""发布队列 — 人审网关是红线：没有人工 approve 的内容不允许发布。

状态机: draft -> pending_review -> approved -> scheduled -> published / rejected
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS publish_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    title TEXT NOT NULL,
    draft_md TEXT NOT NULL,
    status TEXT DEFAULT 'pending_review',
    scheduled_at REAL DEFAULT 0,
    published_at REAL DEFAULT 0,
    remote_id TEXT DEFAULT '',
    created_at REAL
);
"""


class PublishQueue:
    VALID_TRANSITIONS = {
        "pending_review": {"approved", "rejected"},
        "approved": {"scheduled", "pending_review"},   # 可打回重审
        "scheduled": {"published", "pending_review"},
        "published": set(),
        "rejected": {"pending_review"},
    }

    def __init__(self, path: str | Path = "data/publish.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def enqueue(self, platform: str, title: str, draft_md: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO publish_queue(platform,title,draft_md,status,created_at) VALUES(?,?,?,?,?)",
            (platform, title, draft_md, "pending_review", time.time()),
        )
        self.conn.commit()
        return cur.lastrowid

    def transition(self, qid: int, to_status: str) -> None:
        row = self.conn.execute("SELECT status FROM publish_queue WHERE id=?", (qid,)).fetchone()
        if row is None:
            raise ValueError(f"队列项不存在: {qid}")
        allowed = self.VALID_TRANSITIONS[row["status"]]
        if to_status not in allowed:
            raise ValueError(f"非法状态转移 {row['status']} -> {to_status}（人审网关不可跳过）")
        sets, vals = "status=?", [to_status]
        if to_status == "published":
            sets += ", published_at=?"
            vals.append(time.time())
        self.conn.execute(f"UPDATE publish_queue SET {sets} WHERE id=?", (*vals, qid))
        self.conn.commit()

    def schedule(self, qid: int, when: float) -> None:
        """approved -> scheduled（定时发布时间戳）。"""
        self.transition(qid, "scheduled")
        self.conn.execute("UPDATE publish_queue SET scheduled_at=? WHERE id=?", (when, qid))
        self.conn.commit()

    def due(self, now: float | None = None) -> list[dict]:
        """到期的已批准定时项。"""
        now = now or time.time()
        rows = self.conn.execute(
            "SELECT * FROM publish_queue WHERE status='scheduled' AND scheduled_at<=?", (now,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list(self, status: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM publish_queue", ()
        if status:
            sql, args = "SELECT * FROM publish_queue WHERE status=?", (status,)
        return [dict(r) for r in self.conn.execute(sql, args)]
