"""对标账号库 — SQLite 存储。

数据模型参照 Sunbird OS：
    accounts   对标账号（平台、昵称、粉丝数、同步时间）
    works      作品（标题、封面、点赞、链接、媒体落盘路径、拆解状态）

用法:
    from lib.bench.store import BenchStore
    db = BenchStore("data/bench.db")
    db.upsert_account(platform="douyin", uid="xxx", nickname="A", followers=1000)
    db.upsert_work(platform="douyin", uid="xxx", wid="w1", title="t", likes=50)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    uid TEXT NOT NULL,
    nickname TEXT DEFAULT '',
    followers INTEGER DEFAULT 0,
    extra TEXT DEFAULT '{}',
    synced_at REAL DEFAULT 0,
    UNIQUE(platform, uid)
);
CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    uid TEXT NOT NULL,
    wid TEXT NOT NULL,
    title TEXT DEFAULT '',
    cover_path TEXT DEFAULT '',
    media_path TEXT DEFAULT '',
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    collected INTEGER DEFAULT 0,
    analyzed INTEGER DEFAULT 0,
    extra TEXT DEFAULT '{}',
    synced_at REAL DEFAULT 0,
    UNIQUE(platform, wid)
);
CREATE INDEX IF NOT EXISTS idx_works_uid ON works(platform, uid);
"""


class BenchStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---- accounts ----
    def upsert_account(self, platform: str, uid: str, **fields) -> None:
        now = time.time()
        self.conn.execute(
            """INSERT INTO accounts(platform, uid, nickname, followers, extra, synced_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(platform, uid) DO UPDATE SET
                 nickname=excluded.nickname, followers=excluded.followers,
                 extra=excluded.extra, synced_at=excluded.synced_at""",
            (
                platform,
                uid,
                fields.get("nickname", ""),
                fields.get("followers", 0),
                json.dumps(fields.get("extra", {}), ensure_ascii=False),
                now,
            ),
        )
        self.conn.commit()

    def list_accounts(self, platform: str | None = None) -> list[dict]:
        sql = "SELECT * FROM accounts"
        args: tuple = ()
        if platform:
            sql += " WHERE platform=?"
            args = (platform,)
        return [dict(r) for r in self.conn.execute(sql, args)]

    # ---- works ----
    def upsert_work(self, platform: str, wid: str, uid: str = "", **fields) -> None:
        now = time.time()
        self.conn.execute(
            """INSERT INTO works(platform, wid, uid, title, cover_path, media_path,
                                 likes, comments, shares, extra, synced_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(platform, wid) DO UPDATE SET
                 title=excluded.title, cover_path=excluded.cover_path,
                 media_path=excluded.media_path, likes=excluded.likes,
                 comments=excluded.comments, shares=excluded.shares,
                 extra=excluded.extra, synced_at=excluded.synced_at""",
            (
                platform,
                wid,
                uid,
                fields.get("title", ""),
                fields.get("cover_path", ""),
                fields.get("media_path", ""),
                fields.get("likes", 0),
                fields.get("comments", 0),
                fields.get("shares", 0),
                json.dumps(fields.get("extra", {}), ensure_ascii=False),
                now,
            ),
        )
        self.conn.commit()

    def mark(self, platform: str, wid: str, field: str, value: int = 1) -> None:
        assert field in ("collected", "analyzed")
        self.conn.execute(
            f"UPDATE works SET {field}=? WHERE platform=? AND wid=?", (value, platform, wid)
        )
        self.conn.commit()

    def pending_works(self, field: str = "collected", platform: str | None = None) -> list[dict]:
        sql = f"SELECT * FROM works WHERE {field}=0"
        args: tuple = ()
        if platform:
            sql += " AND platform=?"
            args = (platform,)
        return [dict(r) for r in self.conn.execute(sql, args)]

    def list_works(self, platform: str | None = None, uid: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM works WHERE 1=1", []
        if platform:
            sql += " AND platform=?"
            args.append(platform)
        if uid:
            sql += " AND uid=?"
            args.append(uid)
        return [dict(r) for r in self.conn.execute(sql, args)]

    def close(self) -> None:
        self.conn.close()
