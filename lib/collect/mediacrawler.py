"""MediaCrawler 结果导入适配器。

MediaCrawler（github.com/NanmiCoder/MediaCrawler）输出 JSONL 到其 data/ 目录。
本适配器不重新实现爬虫，只消费其产物 → 写入对标库，避免重复抓取与风控暴露。

支持的记录字段（容错解析）：
    platform, note_id/video_id, nickname/user_id, title, liked_count,
    comment_count, share_count, cover_url, video_url, desc
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.bench.store import BenchStore

PLATFORM_MAP = {
    "xhs": "xhs", "xiaohongshu": "xhs",
    "dy": "douyin", "douyin": "douyin",
    "ks": "kuaishou", "kuaishou": "kuaishou",
    "bilibili": "bilibili", "bili": "bilibili",
    "wb": "weibo", "weibo": "weibo",
}


def _num(v) -> int:
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


def import_mediacrawler_dir(
    src: str | Path, db: BenchStore, limit_per_file: int = 200
) -> dict:
    """扫描 MediaCrawler 输出目录下所有 *.jsonl，导入对标库。返回统计。"""
    src = Path(src)
    stats = {"files": 0, "accounts": 0, "works": 0}
    for f in sorted(src.rglob("*.jsonl")):
        # 只导入内容文件（search_contents/creator_contents），跳过评论文件
        if "comment" in f.name.lower():
            continue
        stats["files"] += 1
        for line in f.read_text(encoding="utf-8").splitlines()[:limit_per_file]:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            platform = PLATFORM_MAP.get(str(rec.get("platform", "")).lower())
            if not platform:
                # MediaCrawler jsonl 不写 platform 字段，从路径推断（data/bili/jsonl/...）
                parts = {p.lower() for p in f.parts}
                for key, val in PLATFORM_MAP.items():
                    if key in parts:
                        platform = val
                        break
            if not platform:
                continue
            uid = str(rec.get("user_id") or rec.get("nickname") or "unknown")
            prev = len(db.list_accounts(platform))
            db.upsert_account(platform, uid, nickname=rec.get("nickname", ""))
            if len(db.list_accounts(platform)) > prev:
                stats["accounts"] += 1
            wid = rec.get("note_id") or rec.get("video_id") or rec.get("id")
            if not wid:
                continue
            db.upsert_work(
                platform, str(wid), uid=uid,
                title=rec.get("title") or rec.get("desc") or "",
                likes=_num(rec.get("liked_count")),
                comments=_num(rec.get("comment_count")),
                shares=_num(rec.get("share_count")),
                extra={"cover_url": rec.get("cover_url", ""), "media_url": rec.get("video_url", "")},
            )
            stats["works"] += 1
    return stats
