#!/usr/bin/env python3
"""统一账号级采集入口（六平台）。

用法：
  .venv/bin/python -u scripts/collect_accounts.py --platforms xhs,douyin,bilibili,x,youtube,weibo
  .venv/bin/python -u scripts/collect_accounts.py --platforms all --want-comments
  .venv/bin/python -u scripts/collect_accounts.py --platforms xhs --limit 10 --min-works 10

契约：每账号 = 主页 1 次 + 缺口作品各 1 次 +（可选）爆款评论。
已满量账号（media >= limit）零请求跳过。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.collect.unified.base import UnifiedAccountCollector
from lib.collect.unified.bilibili import BilibiliCollector
from lib.collect.unified.douyin import DouyinCollector
from lib.collect.unified.weibo import WeiboCollector
from lib.collect.unified.x_twitter import XCollector
from lib.collect.unified.xhs import XhsCollector
from lib.collect.unified.youtube import YoutubeCollector

COLLECTORS: dict[str, type[UnifiedAccountCollector]] = {
    "xhs": XhsCollector, "douyin": DouyinCollector, "bilibili": BilibiliCollector,
    "x": XCollector, "youtube": YoutubeCollector, "weibo": WeiboCollector,
}


def cleanup_tabs(cdp: str = "http://127.0.0.1:9222"):
    """轮开始前清扫所有泄漏 tab（xhs/douyin/x/weibo 页面），只留非平台页面。"""
    import requests as _rq
    try:
        tabs = _rq.get(f"{cdp}/json/list", timeout=8).json()
    except Exception:
        return 0
    n = 0
    for t in tabs:
        if not (isinstance(t, dict) and t.get("type") == "page"):
            continue
        u = t.get("url") or ""
        if any(k in u for k in ("xiaohongshu.com", "douyin.com", "x.com", "weibo.cn", "bilibili.com")):
            try:
                _rq.get(f"{cdp}/json/close/{t['id']}", timeout=5)
                n += 1
            except Exception:
                pass
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="all")
    ap.add_argument("--count", default="10",
                    help="抓取数量：数字（如 10/5）或 all=主页全部；latest 模式取最近 N 条")
    ap.add_argument("--order", choices=["latest", "top"], default="latest",
                    help="latest=最近优先（主页时间线前 N）；top=热度优先（未抓的按点赞排前 N）")
    ap.add_argument("--mode", choices=["latest", "monitor", "top"], default="latest",
                    help="latest=按数量抓；monitor=每日盯新；top=热度优先")
    ap.add_argument("--want-comments", dest="want_comments", action="store_true", default=True,
                    help="抓评论（默认开：爆款/零成本通道）")
    ap.add_argument("--no-comments", dest="want_comments", action="store_false",
                    help="关闭评论采集")
    ap.add_argument("--comment-threshold", type=int, default=200, help="评论数阈值才算爆款")
    ap.add_argument("--no-cleanup", action="store_true", help="跳过 tab 清扫")
    args = ap.parse_args()
    if not args.no_cleanup:
        n = cleanup_tabs()
        if n:
            print(f"[cleanup] 清扫 {n} 个泄漏 tab")

    plats = list(COLLECTORS) if args.platforms == "all" else \
        [p.strip() for p in args.platforms.split(",") if p.strip() in COLLECTORS]

    import sqlite3
    con = sqlite3.connect("data/bench.db")
    con.row_factory = sqlite3.Row
    for p in plats:
        cls = COLLECTORS[p]
        col = cls()
        accounts = [dict(r) for r in con.execute(
            "SELECT platform, uid, nickname, followers, extra FROM accounts WHERE platform=?", (p,))]
        if not accounts:
            print(f"[{p}] 无账号，跳过")
            continue
        print(f"\n=== [{p}] {len(accounts)} 账号 ===")
        for acc in accounts:
            uid = acc["uid"]
            t0 = time.time()
            try:
                want_c = args.want_comments
                count = args.count if args.count == "all" else int(args.count)
                r = col.collect_account(uid, count=count, want_comments=want_c, mode=args.mode,
                                        order=args.order)
            except Exception as e:
                msg = str(e)[:80]
                if "hex_uid_stale" in msg:
                    # 清缓存 → 下轮走搜索重新 resolve
                    import json as _j
                    extra = _j.loads(acc["extra"] or "{}")
                    extra.pop("hex_uid", None)
                    con.execute("UPDATE accounts SET extra=? WHERE platform=? AND uid=?",
                                (_j.dumps(extra, ensure_ascii=False), p, uid))
                    con.commit()
                    print(f"  ⚠️ {uid} hex_uid 失效，已清缓存待重解析")
                else:
                    print(f"  ❌ {uid}: {msg}")
                continue
            dur = time.time() - t0
            if r.get("skipped"):
                print(f"  ⏭ {uid} {r.get('reason', '')}（count={args.count} mode={args.mode}）")
            else:
                print(f"  ✅ {uid} +{r['works_new']} 作品 / +{r['media_new']} 媒体"
                      + (f" / +{r['comments_new']} 评论" if r.get("comments_new") else "")
                      + f"（{dur:.0f}s）")


if __name__ == "__main__":
    main()
