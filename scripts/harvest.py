#!/usr/bin/env python
"""跨平台批量采集：关键词 → 搜索 → 选头部账号 → 拉作品 → 入对标库。

用法:
  .venv/bin/python scripts/harvest.py --keywords "玄学,八字 命理" --per-key 3 --max-works 20
  .venv/bin/python scripts/harvest.py --platforms douyin,xhs --keywords "玄学"

平台通道:
  douyin   页面内 fetch（需 CDP Chrome 9222 + 登录态）
  xhs      搜索页 DOM（需 CDP + 登录态，控频 45s）
  x        时间线 DOM（需 CDP + 登录态）
  bilibili MediaCrawler 搜索（cookie 注入 config）→ import_mediacrawler_dir
  youtube  yt-dlp ytsearch（走代理，免登录）

B站无"账号维度搜索选头"能力（MC 搜索直接落作品），其结果直接入库。
抖音/小红书/X：搜索结果按作者聚合 → 选累计赞数最高的 N 个 → 逐号拉全作品。
YouTube：按频道聚合 → 选播放量最高的 N 个频道 → 拉频道最新作品。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.bench.store import BenchStore  # noqa: E402

DB_PATH = "data/bench.db"
MC_DIR = Path(__file__).resolve().parents[1] / "vendor" / "MediaCrawler"


def run_douyin(keywords: list[str], per_key: int, max_works: int, db: BenchStore) -> dict:
    from lib.collect.douyin_dom import DouyinDomAdapter
    ad = DouyinDomAdapter()
    stats = {"accounts": 0, "works": 0}
    for kw in keywords:
        print(f"[douyin] 搜索: {kw}")
        results = ad.search(kw, count=15)
        by_author = defaultdict(int)
        meta = {}
        for r in results:
            if r.get("sec_uid"):
                key = r["sec_uid"]
                by_author[key] += r.get("likes", 0)
                meta[key] = r["author"]
        top = sorted(by_author, key=by_author.get, reverse=True)[:per_key]
        for sec in top:
            nick = meta[sec]
            print(f"[douyin] 账号: {nick}")
            db.upsert_account("douyin", nick, nickname=nick)
            stats["accounts"] += 1
            try:
                works = ad.fetch_works(sec, limit=max_works)
            except Exception as e:
                print(f"  失败: {e}"); continue
            for w in works:
                db.upsert_work("douyin", w["wid"], uid=nick, title=w["title"],
                               likes=w["likes"], comments=w["comments"],
                               extra={"play_addr": w.get("play_addr") or ""})
            stats["works"] += len(works)
            print(f"  +{len(works)} works")
    return stats


def run_xhs(keywords: list[str], per_key: int, max_works: int, db: BenchStore) -> dict:
    from lib.collect.xhs_search import XhsSearchAdapter
    ad = XhsSearchAdapter()
    stats = {"accounts": 0, "works": 0}
    for kw in keywords:
        print(f"[xhs] 搜索: {kw}")
        results = ad.search(kw, count=20)
        by_author = defaultdict(int)
        for r in results:
            if r.get("author"):
                by_author[r["author"]] += r.get("likes", 0)
        top = sorted(by_author, key=by_author.get, reverse=True)[:per_key]
        # 小红书笔记粒度即作品：搜索结果全量入库（详情页控频45s太慢，列表数据已够拆解用）
        before = stats["works"]
        for r in results:
            if r.get("wid"):
                db.upsert_work("xhs", r["wid"], uid=r["author"], title=r["title"],
                               likes=r["likes"],
                               extra={"media_url": r.get("media_url", ""),
                                      "source_kw": kw})
                stats["works"] += 1
        for nick in top:
            db.upsert_account("xhs", nick, nickname=nick)
            stats["accounts"] += 1
        print(f"  +{stats['works']-before} works（标记头部{len(top)}号）")
    return stats


def run_x(keywords: list[str], per_key: int, max_works: int, db: BenchStore) -> dict:
    from lib.collect.x_dom import XDomAdapter
    ad = XDomAdapter()
    stats = {"accounts": 0, "works": 0}
    for kw in keywords:
        print(f"[x] 搜索: {kw}")
        results = ad.search(kw, count=15)
        # 聚合：handle → 累计赞，选头部账号拉时间线
        by_handle = {}
        meta = {}
        for r in results:
            h = (r.get("user_href") or "").lstrip("/").split("?")[0]
            if not h:
                continue
            by_handle[h] = by_handle.get(h, 0) + ad._parse_count(r.get("likes", "0"))
            meta[h] = (r.get("user") or h)
        top = sorted(by_handle, key=by_handle.get, reverse=True)[:per_key]
        for h in top:
            nick = meta[h]
            print(f"[x] 账号: {nick} (@{h})")
            db.upsert_account("x", h, nickname=nick)
            stats["accounts"] += 1
            try:
                works = ad.fetch_works(h, limit=max_works)
            except Exception as e:
                print(f"  时间线失败: {e}"); continue
            for w in works:
                db.upsert_work("x", w["wid"], uid=h, title=w["title"],
                               likes=w["likes"], comments=w["comments"],
                               extra={"nickname": nick,
                                      "media_url": w.get("media_url", "")})
            stats["works"] += len(works)
            print(f"  +{len(works)} works")
    return stats


def run_youtube(keywords: list[str], per_key: int, max_works: int, db: BenchStore) -> dict:
    from lib.collect.youtube import YoutubeAdapter
    ad = YoutubeAdapter()
    stats = {"accounts": 0, "works": 0}
    for kw in keywords:
        print(f"[youtube] 搜索: {kw}")
        results = ad.search(kw, count=10)
        by_ch = defaultdict(int)
        meta = {}
        for r in results:
            if r.get("channel_id"):
                by_ch[r["channel_id"]] += r.get("views", 0)
                meta[r["channel_id"]] = r["channel"]
        top = sorted(by_ch, key=by_ch.get, reverse=True)[:per_key]
        for ch in top:
            nick = meta[ch]
            print(f"[youtube] 频道: {nick} ({ch})")
            db.upsert_account("youtube", nick, nickname=nick)
            stats["accounts"] += 1
            try:
                works = ad.fetch_works(f"https://www.youtube.com/channel/{ch}/videos", limit=max_works)
            except Exception as e:
                print(f"  失败: {e}"); continue
            for w in works:
                db.upsert_work("youtube", w["wid"], uid=nick, title=w["title"],
                               extra={"views": w.get("views", 0),
                                      "media_url": w.get("media_url", "")})
            stats["works"] += len(works)
            print(f"  +{len(works)} works")
    return stats


def run_bilibili(keywords: list[str], per_key: int, max_works: int, db: BenchStore) -> dict:
    """MC 搜索落作品文件 → 导入对标库。"""
    import os
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("all_proxy", "http_proxy", "https_proxy", "no_proxy")}
    env["NO_PROXY"] = ""
    env["https_proxy"] = env["http_proxy"] = ""
    for kw in keywords:
        print(f"[bilibili] MC 搜索: {kw}")
        r = subprocess.run(
            [str(MC_DIR / ".venv" / "bin" / "python"), "main.py",
             "--platform", "bili", "--type", "search", "--keywords", kw, "--start", "1"],
            cwd=MC_DIR, env=env, capture_output=True, text=True, timeout=600)
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-120:]
        print(f"  MC: {tail[:80]}")
    from lib.collect.mediacrawler import import_mediacrawler_dir
    stats = import_mediacrawler_dir(MC_DIR / "data", db)
    return {"accounts": stats["accounts"], "works": stats["works"]}


RUNNERS = {"douyin": run_douyin, "xhs": run_xhs, "x": run_x,
           "youtube": run_youtube, "bilibili": run_bilibili}


def main():
    ap = argparse.ArgumentParser(description="跨平台批量采集入对标库")
    ap.add_argument("--keywords", required=True, help="逗号分隔")
    ap.add_argument("--platforms", default="douyin,xhs,youtube",
                    help="逗号分隔，默认 douyin,xhs,youtube（x/bilibili 需显式开）")
    ap.add_argument("--per-key", type=int, default=2, help="每关键词选头部账号数（默认2）")
    ap.add_argument("--max-works", type=int, default=20, help="每账号最多拉作品数（默认20）")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    db = BenchStore(DB_PATH)
    total = {"accounts": 0, "works": 0}
    for p in platforms:
        if p not in RUNNERS:
            print(f"未知平台 {p}，跳过"); continue
        try:
            s = RUNNERS[p](keywords, args.per_key, args.max_works, db)
            print(f"[{p}] 完成: 账号+{s['accounts']} 作品+{s['works']}")
            total["accounts"] += s["accounts"]; total["works"] += s["works"]
        except Exception as e:
            print(f"[{p}] 失败: {e}")
    print(f"\n=== 汇总: 账号+{total['accounts']} 作品+{total['works']} ===")
    for p in ("bilibili", "douyin", "xhs", "x", "youtube"):
        print(f"  {p}: {len(db.list_works(p))} works")


if __name__ == "__main__":
    main()
