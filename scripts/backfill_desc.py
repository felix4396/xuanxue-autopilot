"""补抓作品详情：回填 title/desc/likes/comments（采集当时被风控吞掉的正文与指标）。"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.collect.unified.xhs import XhsCollector
from lib.collect.unified.bilibili import BilibiliCollector
from lib.collect.unified.douyin import DouyinCollector
from lib.collect.unified.base import Work
from lib.bench.store import BenchStore

def main(limit: int, platforms: str):
    db = BenchStore(ROOT / "data" / "bench.db")
    cols = {"xhs": XhsCollector, "bilibili": BilibiliCollector, "douyin": DouyinCollector}
    for p in [x.strip() for x in platforms.split(",") if x.strip() in cols]:
        col = cols[p]()
        rows = db.conn.execute(
            f"""SELECT wid, extra FROM works w
                WHERE platform='{p}' AND (title='' OR likes=0) LIMIT {limit}""").fetchall()
        print(f"[{p}] 待补 {len(rows)} 条")
        ok = fail = 0
        for wid, extra in rows:
            import json
            token = (json.loads(extra or "{}").get("xsec_token") or "") if p == "xhs" else ""
            try:
                d = col._fetch_work(Work(wid=wid, token=token))
                if not d or (not d.get("title") and not d.get("desc")):
                    fail += 1
                    continue
                db.upsert_work(p, wid,
                    title=d.get("title", ""), desc=d.get("desc", ""),
                    likes=int(d.get("likes") or 0), comments=int(d.get("comments_count") or 0))
                ok += 1
                print(f"  ✅ {wid[:16]} | {d.get('title','')[:30]} | 👍{d.get('likes')}")
            except Exception as e:
                fail += 1
                print(f"  ❌ {wid[:16]} | {str(e)[:60]}")
            time.sleep(col.rate_limit if hasattr(col, "rate_limit") else 3)
        print(f"[{p}] 补齐 {ok} / 失败 {fail}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--platforms", default="xhs")
    a = ap.parse_args()
    main(a.limit, a.platforms)
