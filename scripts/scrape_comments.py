"""批量抓评论 → 语料库 comments.json。

策略：按互动数排序，优先爆款（评论是选题金矿）；已有 comments.json 跳过。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.collect.comments import CommentScraper

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="douyin,weibo")
    ap.add_argument("--limit", type=int, default=10, help="每平台条数（按互动数 top）")
    ap.add_argument("--per-work", type=int, default=30)
    args = ap.parse_args()

    con = sqlite3.connect("data/bench.db")
    cs = CommentScraper()
    for p in [x.strip() for x in args.platforms.split(",") if x.strip()]:
        fn = cs.scrape_douyin if p == "douyin" else (cs.scrape_weibo if p == "weibo" else None)
        if not fn:
            print(f"[{p}] 暂不支持评论抓取，跳过")
            continue
        rows = con.execute(
            f"SELECT wid, likes FROM works WHERE platform='{p}' "
            f"ORDER BY likes DESC LIMIT {args.limit * 3}").fetchall()
        done = 0
        for wid, likes in rows:
            if done >= args.limit:
                break
            out = ROOT / "output/corpus" / p / wid / "comments.json"
            if out.exists():
                continue
            try:
                comments = fn(wid, max_comments=args.per_work)
                if comments:
                    cs.save(p, wid, comments)
                    print(f"  ✅ {p}/{wid[:20]} {len(comments)}条（原likes {likes}）")
                    done += 1
                else:
                    print(f"  {p}/{wid[:20]} 无评论（跳过不占名额）")
            except Exception as e:
                print(f"  {p}/{wid[:20]} 失败: {str(e)[:45]}")
    print("done")


if __name__ == "__main__":
    main()
