"""YouTube 真实采集与视频原料包打通。

使用项目内 .venv 的 yt-dlp / faster-whisper；落盘遵循 平台-作品ID 约定。
用法:
    python3 scripts/youtube_collect.py "https://www.youtube.com/@某频道" --limit 5
    python3 scripts/youtube_collect.py --transcribe <视频路径> --platform youtube --wid <id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.bench.store import BenchStore          # noqa: E402
from lib.collect.ytdlp import YtdlpAdapter      # noqa: E402
from lib.analyze.video import VideoPrep         # noqa: E402

BENCH_DB = ROOT / "data" / "bench.db"


def cmd_fetch(args):
    ad = YtdlpAdapter(cache_dir=ROOT / "output" / "media")
    works = ad.fetch_works(args.channel, limit=args.limit)
    db = BenchStore(BENCH_DB)
    for w in works:
        db.upsert_work("youtube", w["wid"], uid=args.channel, title=w["title"], likes=w["likes"])
        print(f"入库: youtube/{w['wid']} {w['title'][:40]}")
    print(f"共 {len(works)} 条作品入对标库")


def cmd_transcribe(args):
    vp = VideoPrep(ROOT / "output" / "media")
    video = Path(args.video)
    frames = vp.extract_frames(video, args.platform, args.wid)
    asr = vp.transcribe(video, args.platform, args.wid, model_size=args.model)
    meta = vp.meta(video, args.platform, args.wid)
    print(f"原料包就绪: frames={len(frames)} asr={asr} meta={meta}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("channel"); f.add_argument("--limit", type=int, default=10)
    f.set_defaults(fn=cmd_fetch)
    t = sub.add_parser("transcribe"); t.add_argument("video"); t.add_argument("--platform", default="youtube")
    t.add_argument("--wid", required=True); t.add_argument("--model", default="small")
    t.set_defaults(fn=cmd_transcribe)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
