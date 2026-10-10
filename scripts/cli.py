#!/usr/bin/env python3
"""玄学 Autopilot CLI 入口。

    python3 scripts/cli.py bench add <platform> <uid> [--nickname N --followers F]
    python3 scripts/cli.py bench list
    python3 scripts/cli.py collect <platform> <uid> [--limit 20]   # 适配器接入后可用
    python3 scripts/cli.py analyze <platform> <wid> --hook-text ... # 写入拆解结论
    python3 scripts/cli.py compose <topic> --analysis <json文件> [--platform xhs]
    python3 scripts/cli.py publish enqueue/review/schedule/due/list
    python3 scripts/cli.py review record/due
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.bench.store import BenchStore          # noqa: E402
from lib.analyze.assets import AssetStore        # noqa: E402
from lib.compose.draft import compose_from_analysis, adapt_platform  # noqa: E402
from lib.publish.queue import PublishQueue       # noqa: E402
from lib.review.metrics import MetricsStore      # noqa: E402

BENCH_DB = ROOT / "data" / "bench.db"
PUBLISH_DB = ROOT / "data" / "publish.db"
METRICS_DB = ROOT / "data" / "metrics.db"
ASSETS_FILE = ROOT / "output" / "assets" / "assets.json"
DRAFTS_DIR = ROOT / "output" / "reports"


def cmd_bench(args):
    db = BenchStore(BENCH_DB)
    if args.action == "add":
        db.upsert_account(args.platform, args.uid, nickname=args.nickname or "", followers=args.followers or 0)
        print(f"已添加对标账号: {args.platform}/{args.uid}")
    elif args.action == "list":
        for a in db.list_accounts(args.platform):
            print(f"{a['platform']:8} {a['uid']:20} {a['nickname']:12} 粉丝={a['followers']}")


def cmd_analyze(args):
    st = AssetStore(ASSETS_FILE)
    result = json.loads(args.file.read_text(encoding="utf-8")) if args.file else {
        "hook": {"second": args.hook_second or 0, "text": args.hook_text or ""},
        "structure": args.structure or "list",
        "cover_template": args.cover or "",
        "title": args.title or "",
        "score": {"hook": args.score or 0},
    }
    st.add_analysis(args.platform, args.wid, result)
    print(f"拆解结论已入库: {args.platform}-{args.wid}; 资产统计: {st.stats()}")


def cmd_compose(args):
    analysis = json.loads(args.analysis.read_text(encoding="utf-8")) if args.analysis else {}
    d = compose_from_analysis(args.topic, analysis, angle=args.angle or "")
    adapt_platform(d, args.platform)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    out = DRAFTS_DIR / f"draft-{int(time.time())}-{args.platform}.md"
    out.write_text(d.to_markdown(), encoding="utf-8")
    print(f"初稿已产出: {out}")
    # 产出即入人审队列
    q = PublishQueue(PUBLISH_DB)
    qid = q.enqueue(args.platform, d.titles[0], d.to_markdown())
    print(f"已入人审队列 qid={qid}（状态 pending_review）")


def cmd_publish(args):
    q = PublishQueue(PUBLISH_DB)
    if args.action == "list":
        for r in q.list(args.status):
            print(f"#{r['id']} [{r['status']}] {r['platform']} {r['title']}")
    elif args.action == "review":
        q.transition(args.qid, "approved" if args.approve else "rejected")
        print(f"#{args.qid} -> {'approved' if args.approve else 'rejected'}")
    elif args.action == "schedule":
        q.schedule(args.qid, when=args.when or time.time())
        print(f"#{args.qid} 已定时")
    elif args.action == "due":
        for r in q.due():
            print(f"到期待发布: #{r['id']} {r['title']}（实际发布由平台适配器执行，接入后启用）")


def cmd_review(args):
    m = MetricsStore(METRICS_DB)
    if args.action == "record":
        m.record(args.platform, args.remote_id, args.checkpoint_h,
                 views=args.views or 0, likes=args.likes or 0,
                 comments=args.comments or 0, shares=args.shares or 0)
        e = m.engagement(args.platform, args.remote_id)
        print(f"已记录; 互动率 like={e['like_rate']:.3f} comment={e['comment_rate']:.3f}")
    elif args.action == "engagement":
        print(json.dumps(m.engagement(args.platform, args.remote_id), ensure_ascii=False, indent=2, default=str))


def main():
    p = argparse.ArgumentParser(description="玄学 Autopilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bench"); b.add_argument("action", choices=["add", "list"])
    b.add_argument("platform", nargs="?"); b.add_argument("uid", nargs="?")
    b.add_argument("--nickname"); b.add_argument("--followers", type=int)
    b.set_defaults(fn=cmd_bench)

    a = sub.add_parser("analyze")
    a.add_argument("platform"); a.add_argument("wid")
    a.add_argument("--file", type=Path); a.add_argument("--hook-text"); a.add_argument("--hook-second", type=int)
    a.add_argument("--structure", choices=["list", "story", "tutorial"]); a.add_argument("--cover")
    a.add_argument("--title"); a.add_argument("--score", type=int)
    a.set_defaults(fn=cmd_analyze)

    c = sub.add_parser("compose")
    c.add_argument("topic"); c.add_argument("--analysis", type=Path); c.add_argument("--angle")
    c.add_argument("--platform", default="xhs")
    c.set_defaults(fn=cmd_compose)

    pu = sub.add_parser("publish"); pu.add_argument("action", choices=["list", "review", "schedule", "due"])
    pu.add_argument("--qid", type=int); pu.add_argument("--status"); pu.add_argument("--approve", action="store_true")
    pu.add_argument("--when", type=float)
    pu.set_defaults(fn=cmd_publish)

    r = sub.add_parser("review"); r.add_argument("action", choices=["record", "engagement"])
    r.add_argument("platform"); r.add_argument("remote_id"); r.add_argument("--checkpoint-h", type=float, default=24)
    r.add_argument("--views", type=int); r.add_argument("--likes", type=int)
    r.add_argument("--comments", type=int); r.add_argument("--shares", type=int)
    r.set_defaults(fn=cmd_review)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
