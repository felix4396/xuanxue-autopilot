"""统一采集基类：页面去重、缺口计算、入库全部集中在此，平台 adapter 只做抓取。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[3]


class Work:
    """统一作品表示，兼容图文/视频/纯文字。"""
    __slots__ = ("wid", "title", "desc", "content_type", "likes", "comments",
                 "shares", "collected_count", "ts", "url", "token")

    def __init__(self, wid: str, title: str = "", desc: str = "", content_type: str = "",
                 likes: int = 0, comments: int = 0, shares: int = 0,
                 collected_count: int = 0, ts: float = 0.0, url: str = "", token: str = ""):
        self.wid, self.title, self.desc = str(wid), title, desc
        self.content_type = content_type  # video | image_text | text | audio
        self.likes, self.comments, self.shares = int(likes or 0), int(comments or 0), int(shares or 0)
        self.collected_count, self.ts, self.url, self.token = int(collected_count or 0), ts, url, token

    def to_row(self) -> dict:
        return dict(wid=self.wid, title=self.title, desc=self.desc, content_type=self.content_type,
                    likes=self.likes, comments=self.comments, shares=self.shares,
                    collected=self.collected_count, ts=self.ts, url=self.url, token=self.token)


class UnifiedAccountCollector:
    """模板方法基类：子类实现 _platform/_resolve/_fetch_profile/_fetch_work/_download/_fetch_comments。"""

    # ---- 子类契约 ----
    platform: str = ""

    def _resolve(self, name_or_uid: str) -> str:
        """昵称/旧 ID → 平台唯一 ID。"""
        raise NotImplementedError

    def _fetch_profile(self, uid: str, limit: int) -> tuple[dict, list[Work]]:
        """主页 1 次。返回 (profile{nickname,followers}, works)。"""
        raise NotImplementedError

    def _fetch_work(self, w: Work) -> dict:
        """作品详情 1 次（可选，主页数据足够时返回 {}）。"""
        return {}

    def _download(self, w: Work, detail: dict, dest: Path) -> Path:
        """下载媒体。纯文字作品返回 None。"""
        raise NotImplementedError

    def _fetch_comments(self, w: Work) -> list[dict]:
        return []

    # ---- 通用工具 ----
    def __init__(self, db_path: str | Path = "data/bench.db", cdp: str = "http://127.0.0.1:9222"):
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        self.cdp = cdp.rstrip("/")

    def _account_extra(self, uid: str) -> dict:
        r = self.con.execute("SELECT extra FROM accounts WHERE platform=? AND uid=?",
                             (self.platform, uid)).fetchone()
        return json.loads(r[0]) if r and r[0] else {}

    def _save_account_extra(self, uid: str, patch: dict, prof: dict | None = None):
        extra = self._account_extra(uid)
        extra.update(patch)
        self.con.execute(
            """INSERT INTO accounts (platform, uid, nickname, followers, extra, synced_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(platform, uid) DO UPDATE SET
                 nickname=COALESCE(NULLIF(?,''), accounts.nickname),
                 followers=CASE WHEN ?>0 THEN ? ELSE accounts.followers END,
                 extra=?, synced_at=?""",
            (self.platform, uid, (prof or {}).get("nickname", ""), (prof or {}).get("followers", 0),
             json.dumps(extra, ensure_ascii=False), time.time(),
             (prof or {}).get("nickname", ""), (prof or {}).get("followers", 0),
             (prof or {}).get("followers", 0), json.dumps(extra, ensure_ascii=False), time.time()))
        self.con.commit()

    def _have_media(self, uid: str) -> set[str]:
        return {r[0] for r in self.con.execute(
            "SELECT wid FROM works WHERE platform=? AND uid=? AND media_path!=''",
            (self.platform, uid))}

    # ---- 模板方法：完整账号采集 ----
    comment_threshold: int = 50  # 评论数≥此值才算爆款，才值得开评论页

    def collect_account(self, uid: str, count: int | str = 10,
                        want_comments: bool = True, dest: str | Path = "output/media",
                        mode: str = "latest", order: str = "latest") -> dict:
        """count: 抓取数量。
        - int  如 10 = 抓 10 条；0 = 只刷新元数据不下载
        - "all" = 主页能看到的全部作品

        mode:
        - "latest" 默认：按主页时间线取前 N 条（最近的在前）
        - "monitor" 每日盯新：只抓库里没有的新 wid（发布即抓，无上限）
        - "top" 热度优先：未抓的作品按点赞排序取前 N

        order: 仅 latest 模式生效——"latest" 取最近 N 条；"top" 取最热 N 条。
        """
        dest = Path(dest)
        stat = {"platform": self.platform, "uid": uid, "works_new": 0, "media_new": 0,
                "comments_new": 0, "skipped": False, "reason": ""}
        uid = self._ensure_uid(uid)
        if not uid:
            stat["reason"] = "resolve_failed"
            stat["skipped"] = True
            return stat
        have = self._have_media(uid)
        have_all = {r[0] for r in self.con.execute(
            "SELECT wid FROM works WHERE platform=? AND uid=?", (self.platform, uid))}

        # 主页卡数量：all → 大页拉满；数字 → 拉对应卡数
        fetch_n = 60 if (count == "all" or (isinstance(count, int) and count > 25)) else max(int(count) + 5, 15)
        prof, works = self._fetch_profile(uid, fetch_n)
        if prof:
            self._save_account_extra(uid, {}, prof)
        if not works:
            stat["reason"] = "no_cards"
            return stat

        # 正文空的历史作品（风控时被吞）视同待抓，下轮自动重试
        have_partial = {r[0] for r in self.con.execute(
            "SELECT wid FROM works WHERE platform=? AND uid=? AND title=''", (self.platform, uid))}
        if mode == "monitor":
            todo = [w for w in works if w.wid not in have_all or w.wid in have_partial]
        elif mode == "top" or (mode == "latest" and order == "top"):
            todo = [w for w in works if w.wid not in have_all or w.wid in have_partial]
            todo.sort(key=lambda w: w.likes, reverse=True)
            if count != "all":
                todo = todo[:int(count)]
        else:  # latest：主页卡时间线序（最新在前）
            todo = [w for w in works if w.wid not in have_all or w.wid in have_partial]
            if count != "all":
                todo = todo[:int(count)]

        for w in todo:
            out = dest / f"{self.platform}-{w.wid}.mp4"
            pack = dest / f"{self.platform}-{w.wid}"
            have_media = (str(out) in {r[0] for r in self.con.execute(
                "SELECT media_path FROM works WHERE wid=?", (w.wid,))}) \
                or (pack / "asr.json").exists()  # 已加工完（原片可删），只差正文时走 detail-only
            try:
                detail = self._fetch_work(w)
            except Exception as e:
                # 详情页失败（风控/超时）→ 跳过不入库，下轮采集重试
                stat.setdefault("skipped_risk", 0)
                stat["skipped_risk"] += 1
                print(f"    ⏭️ {w.wid[:18]} 详情页失败（{str(e)[:40]}），下轮重试")
                continue
            if not detail.get("media_urls") and not detail.get("title"):
                # 风控静默吞页：空详情不入库，留待下轮
                stat.setdefault("skipped_risk", 0)
                stat["skipped_risk"] += 1
                print(f"    ⏭️ {w.wid[:18]} 空详情（疑似风控），下轮重试")
                continue
            row = w.to_row()
            comments = detail.pop("comments", [])  # 详情页顺带抓的评论
            row.update({k: v for k, v in detail.items() if v not in (None, "", [])})
            try:
                if have_media:
                    mp = out if out.exists() else None  # 媒体已加工过，不重下
                else:
                    mp = self._download(w, detail, dest)
                    row["media_path"] = str(mp) if mp else ""
            except Exception as e:
                row["err"] = str(e)[:80]
                mp = None
            self._upsert_work(uid, row)
            stat["works_new"] += 1
            if mp:
                stat["media_new"] += 1
            # 评论随作品页顺带入库（详情页已打开，零额外页面开销）
            if want_comments and comments:
                stat["comments_new"] += self._upsert_comments(w.wid, comments)
            elif want_comments and self.platform in ("bilibili", "weibo", "youtube", "douyin"):
                # API 平台：评论不在详情页 DOM 里，走零成本 API 补
                try:
                    cmts = self._fetch_comments(w)
                    if cmts:
                        stat["comments_new"] += self._upsert_comments(w.wid, cmts)
                except Exception:
                    pass
            time.sleep(1.0)  # 平台间统一轻间隔，具体限速在 adapter 内部 limiter
        return stat

    def _ensure_uid(self, uid: str) -> str:
        """账号标识归一：昵称/手机号 → 平台唯一 ID（hex_uid/sec_uid/mid）。
        缓存缺失时调用平台 _resolve 并写回。"""
        if self.platform == "bilibili" and uid.isdigit():
            return uid
        extra = self._account_extra(uid)
        if self.platform == "xhs":
            if extra.get("hex_uid"):
                return extra["hex_uid"]
            resolved = self._resolve(uid)
            if resolved:
                self._save_account_extra(uid, {"hex_uid": resolved})
            return resolved
        if self.platform == "douyin":
            if extra.get("sec_uid"):
                return extra["sec_uid"]
            resolved = self._resolve(uid)
            if resolved:
                self._save_account_extra(uid, {"sec_uid": resolved})
            return resolved
        return uid

    def _upsert_work(self, uid: str, row: dict):
        cols = ["platform", "uid", "wid", "title", "desc", "content_type", "likes",
                "comments", "shares", "collected", "media_path", "extra", "synced_at"]
        extra = {k: v for k, v in row.items() if k not in
                 ("platform", "uid", "wid", "title", "desc", "content_type", "likes",
                  "comments", "shares", "collected", "media_path", "ts")}
        vals = [self.platform, uid, row["wid"], row.get("title", ""), row.get("desc", ""),
                row.get("content_type", ""), row.get("likes", 0), row.get("comments", 0),
                row.get("shares", 0), row.get("collected", 0), row.get("media_path", ""),
                json.dumps(extra, ensure_ascii=False), time.time()]
        self.con.execute(
            f"""INSERT INTO works ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})
                ON CONFLICT(platform, wid) DO UPDATE SET
                  title=excluded.title, desc=excluded.desc, content_type=excluded.content_type,
                  likes=excluded.likes, comments=excluded.comments, shares=excluded.shares,
                  collected=excluded.collected, extra=excluded.extra, synced_at=excluded.synced_at""",
            vals)
        self.con.commit()

    def list_comments(self, platform: str | None = None, wid: str | None = None) -> list[dict]:
        import json as _json
        q = "SELECT * FROM comments WHERE 1=1"
        args: list = []
        if platform:
            q += " AND platform=?"
            args.append(platform)
        if wid:
            q += " AND wid=?"
            args.append(wid)
        q += " ORDER BY likes DESC"
        rows = [dict(r) for r in self.con.execute(q, args)]
        for r in rows:
            try:
                r["extra"] = _json.loads(r.get("extra") or "{}")
            except Exception:
                r["extra"] = {}
        return rows

    MAX_COMMENTS = 30  # 每作品一级评论上限

    def _upsert_comments(self, wid: str, items: list[dict]) -> int:
        import json as _json
        # 统一规则：只一级评论，最多 30 条
        items = [it for it in items if not str(it.get("parent_cid") or "").strip()
                 or it.get("parent_cid") in ("0", "", "None", "root")]
        items = items[:self.MAX_COMMENTS]
        n = 0
        for it in items:
            if not it.get("cid"):
                continue
            self.con.execute(
                """INSERT OR REPLACE INTO comments
                   (platform, wid, cid, uid, nickname, content, likes, parent_cid, extra, synced_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (self.platform, wid, str(it["cid"]), str(it.get("uid", "")),
                 str(it.get("nickname", "")), str(it.get("content", "")),
                 int(it.get("likes", 0) or 0), str(it.get("parent_cid", "")),
                 _json.dumps(it.get("extra", {}), ensure_ascii=False), time.time()))
            n += 1
        self.con.commit()
        return n
