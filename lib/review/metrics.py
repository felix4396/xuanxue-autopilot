"""复盘回流 — 发布后数据回采，结论写回对标库与资产库。

时点约定：发布后 24h / 72h / 7d 三个回采点。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

CHECKPOINTS = (24 * 3600, 72 * 3600, 7 * 24 * 3600)

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    remote_id TEXT NOT NULL,
    checkpoint_h REAL NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    finishes INTEGER DEFAULT 0,
    recorded_at REAL,
    UNIQUE(platform, remote_id, checkpoint_h)
);
"""


class MetricsStore:
    def __init__(self, path: str | Path = "data/metrics.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def record(self, platform: str, remote_id: str, checkpoint_h: float, **m) -> None:
        self.conn.execute(
            """INSERT INTO metrics(platform,remote_id,checkpoint_h,views,likes,comments,shares,finishes,recorded_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(platform,remote_id,checkpoint_h) DO UPDATE SET
                 views=excluded.views, likes=excluded.likes, comments=excluded.comments,
                 shares=excluded.shares, finishes=excluded.finishes, recorded_at=excluded.recorded_at""",
            (
                platform, remote_id, checkpoint_h,
                m.get("views", 0), m.get("likes", 0), m.get("comments", 0),
                m.get("shares", 0), m.get("finishes", 0), time.time(),
            ),
        )
        self.conn.commit()

    def due_checkpoints(self, platform: str, remote_id: str, published_at: float) -> list[float]:
        """返回已到期但尚未回采的时点（小时）。"""
        done = {
            r["checkpoint_h"]
            for r in self.conn.execute(
                "SELECT checkpoint_h FROM metrics WHERE platform=? AND remote_id=?",
                (platform, remote_id),
            )
        }
        elapsed_h = (time.time() - published_at) / 3600
        return [h for h in CHECKPOINTS if h / 3600 <= elapsed_h and h not in done]

    def engagement(self, platform: str, remote_id: str) -> dict:
        """互动率汇总（赞藏评/播放）。"""
        rows = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM metrics WHERE platform=? AND remote_id=? ORDER BY checkpoint_h",
                (platform, remote_id),
            )
        ]
        if not rows:
            return {}
        last = rows[-1]
        denom = max(1, last["views"])
        return {
            "last": last,
            "like_rate": last["likes"] / denom,
            "comment_rate": last["comments"] / denom,
            "history": rows,
        }
