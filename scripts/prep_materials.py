"""批量视频原料包：抽帧 + faster-whisper 转写 + ffprobe 元数据。

对 bench.db 中有 mp4 媒体的作品，产出到 output/media/{platform}-{wid}/：
    frames/*.jpg, asr.json, meta.json
断点续跑：已有 asr.json 的跳过。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.analyze.video import VideoPrep

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platforms", default="xhs,douyin,bilibili,youtube,weibo")
    ap.add_argument("--model", default="small")
    ap.add_argument("--fps", type=float, default=0.5)
    ap.add_argument("--frames", type=int, default=10,
                    help="目标帧数（按视频时长算 fps，长视频≈N帧）；0=用 --fps")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-asr", action="store_true", help="只抽帧不转写")
    ap.add_argument("--engine", choices=["local", "cloud"], default="local",
                    help="cloud=阿里云百炼 filetrans（快、带字级时间戳）")
    args = ap.parse_args()

    vp = VideoPrep(ROOT / "output" / "media")
    con = sqlite3.connect(ROOT / "data" / "bench.db")
    ok = skip = fail = 0
    for p in [x.strip() for x in args.platforms.split(",") if x.strip()]:
        rows = con.execute(
            "SELECT wid, media_path FROM works WHERE platform=? AND media_path LIKE '%.mp4'",
            (p,)).fetchall()
        print(f"[{p}] 视频 {len(rows)} 条")
        for wid, mpath in rows:
            v = Path(mpath)
            if not v.is_absolute():
                v = ROOT / v
            if not v.exists():
                fail += 1
                continue
            d = ROOT / "output" / "media" / f"{p}-{wid}"
            if (d / "asr.json").exists() or (args.skip_asr and (d / "frames").exists()
                                             and any((d / "frames").iterdir())):
                skip += 1
                continue
            try:
                fps = args.fps
                if args.frames:
                    import subprocess as _sp
                    dur = float(_sp.check_output(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", str(v)]).decode().strip() or 0)
                    if dur > 0:
                        fps = round(args.frames / dur, 5)
                frames = vp.extract_frames(v, p, wid, fps=fps)
                audio = vp.extract_audio(v, p, wid)
                if args.skip_asr:
                    print(f"  ✅ {p}/{wid[:22]} 帧{len(frames)} + audio")
                    ok += 1
                    continue
                if args.engine == "cloud":
                    asr = vp.transcribe_cloud(v, p, wid)
                else:
                    asr = vp.transcribe(v, p, wid, model_size=args.model)
                vp.meta(v, p, wid)
                n_seg = len(json.loads(asr.read_text()))
                print(f"  ✅ {p}/{wid[:22]} 帧{len(frames)} asr段{n_seg} audio✓")
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  {p}/{wid[:22]} 失败: {str(e)[:55]}")
            if args.limit and ok >= args.limit:
                break
        if args.limit and ok >= args.limit:
            break
    print(f"\n完成 {ok}，跳过 {skip}，失败 {fail}")


if __name__ == "__main__":
    main()
