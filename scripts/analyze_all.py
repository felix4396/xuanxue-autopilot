"""批量拆解语料库 → 资产库。

只拆纯文本路径（content.md）；有 asr.json 的视频作品走 analyze_one 原路径。
可断点续跑：assets 已有 analysis 的自动跳过。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.analyze.assets import AssetStore
from lib.bench.store import BenchStore
from lib.llm import chat_json
from lib.analyze.llm_analyze import analyze_corpus_note

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def main():
    ap = argparse.ArgumentParser(description="批量拆解语料库")
    ap.add_argument("--platforms", default="weibo,xhs,douyin,x,bilibili,youtube")
    ap.add_argument("--limit", type=int, default=0, help="0=全部")
    args = ap.parse_args()

    assets = AssetStore(OUT / "assets" / "assets.json")
    db = BenchStore("data/bench.db")
    # 已拆解集合（assets.store 结构: {platform: {wid: analysis}})
    store = getattr(assets, "store", {}) or {}
    done = set()
    for plat, items in (store.items() if isinstance(store, dict) else []):
        if isinstance(items, dict):
            done.update((plat, w) for w in items)
        elif isinstance(items, list):
            done.update((plat, it.get("wid")) for it in items if isinstance(it, dict) and it.get("wid"))

    ok = skip = fail = 0
    for p in [x.strip() for x in args.platforms.split(",") if x.strip()]:
        for md in (OUT / "corpus" / p).glob("*/content.md"):
            wid = md.parent.name
            if (p, wid) in done:
                skip += 1
                continue
            meta_p = md.parent / "meta.json"
            try:
                meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            except json.JSONDecodeError:
                meta = {}
            content = md.read_text()
            if not content.strip():
                fail += 1
                continue
            title = meta.get("title") or content.splitlines()[0].lstrip("# ") if content else wid
            try:
                r = analyze_corpus_note(p, title, content, stats=meta.get("metrics") or {})
                assets.add_analysis(p, wid, r)
                db.mark(p, wid, "analyzed")
                ok += 1
                print(f"  ✅ {p}/{wid[:20]} [{r.get('structure','?')}] hook {r.get('score',{}).get('hook','?')}/10")
            except Exception as e:
                fail += 1
                print(f"  {p}/{wid[:20]} 失败: {str(e)[:55]}")
            if args.limit and ok >= args.limit:
                break
        if args.limit and ok >= args.limit:
            break
    print(f"\n拆解 {ok}，跳过(已拆) {skip}，失败 {fail}")


if __name__ == "__main__":
    main()
