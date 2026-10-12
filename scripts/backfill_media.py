"""批量补抓媒体：库里作品 → 图文存图 / 视频存视频 → 更新 media_path → 重导语料库。

按平台路由：
  xhs:      重搜 keyword 拿 xsec_token → fetch_note_detail → 多图/视频
  x:        extra.media_url 提 handle → download_media（视频优先/退图）
  bilibili: yt-dlp（BV 号直链，小体积）
  youtube:  yt-dlp（代理）
  douyin:   yt-dlp 尝试（extractor 失效则跳过计数）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.bench.store import BenchStore
from lib.corpus import export_all

MEDIA_ROOT = Path("output/media")
PROXY = "http://127.0.0.1:7897"


def _save_path(db: BenchStore, platform: str, wid: str, path: Path) -> None:
    db.upsert_work(platform, wid, media_path=str(path))


def backfill_xhs(db, limit: int) -> int:
    from lib.collect.xhs_search import XhsSearchAdapter
    ad = XhsSearchAdapter()
    rows = [w for w in db.list_works("xhs") if not (w.get("media_path") or "")]
    rows = rows[:limit]
    ok = 0
    for w in rows:
        kw = (w.get("extra") or {}).get("source_kw") if isinstance(w.get("extra"), dict) else None
        if isinstance(w.get("extra"), str):
            try:
                kw = json.loads(w["extra"]).get("source_kw")
            except json.JSONDecodeError:
                kw = None
        kw = kw or "玄学"
        try:
            results = ad.search(kw, count=20)
            hit = next((r for r in results if r["wid"] == w["wid"]), None)
            if not hit or not hit.get("xsec_token"):
                print(f"  [xhs] {w['wid'][:16]} 搜索未命中/token空，跳过")
                continue
            out = ad.download_media(w["wid"], "", xsec_token=hit["xsec_token"])
            if isinstance(out, list):
                out = out[0]
            _save_path(db, "xhs", w["wid"], out)
            ok += 1
            print(f"  [xhs] ✅ {w['wid'][:16]} → {out.name}")
        except Exception as e:
            print(f"  [xhs] {w['wid'][:16]} 失败: {str(e)[:60]}")
    return ok


def backfill_x(db, limit: int) -> int:
    from lib.collect.x_dom import XDomAdapter
    ad = XDomAdapter()
    rows = [w for w in db.list_works("x") if not (w.get("media_path") or "")][:limit]
    ok = 0
    for w in rows:
        extra = w.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                extra = {}
        url = extra.get("media_url") or ""
        import re
        m = re.match(r"https://x\.com/(\w+)/status/", url)
        if not m:
            print(f"  [x] {w['wid'][:18]} 无 handle，跳过")
            continue
        try:
            out = ad.download_media(w["wid"], url, handle=m.group(1))
            _save_path(db, "x", w["wid"], out)
            ok += 1
            print(f"  [x] ✅ {w['wid'][:18]} → {out.name} ({out.stat().st_size // 1024}KB)")
        except Exception as e:
            print(f"  [x] {w['wid'][:18]} 失败: {str(e)[:60]}")
    return ok


def _ytdlp(url: str, out_tmpl: str, proxy: bool = False) -> Path | None:
    cmd = ["yt-dlp",
           "-f", "bv*[height<=720][vcodec^=avc1]+ba/bv*+ba/b[vcodec^=avc1]/b/w",
           "--merge-output-format", "mp4",
           "--js-runtimes", "node",
           "-o", out_tmpl, "--no-playlist", "-q"]
    if proxy:
        cmd += ["--proxy", PROXY]
    r = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return None
    return None  # 由调用方 glob


def backfill_bilibili(db, limit: int) -> int:
    rows = [w for w in db.list_works("bilibili") if not (w.get("media_path") or "")][:limit]
    ok = 0
    for w in rows:
        out_tmpl = str(MEDIA_ROOT / f"bilibili-{w['wid']}.%(ext)s")
        try:
            _ytdlp(f"https://www.bilibili.com/video/av{w['wid']}", out_tmpl)
            hits = [p for p in MEDIA_ROOT.glob(f"bilibili-{w['wid']}.*")
                    if p.suffix in (".mp4", ".webm", ".mkv")]
            if hits:
                _save_path(db, "bilibili", w["wid"], hits[0])
                ok += 1
                print(f"  [bili] ✅ {w['wid']} → {hits[0].name} ({hits[0].stat().st_size // 1024}KB)")
            else:
                print(f"  [bili] {w['wid']} yt-dlp 无产物")
        except Exception as e:
            print(f"  [bili] {w['wid']} 失败: {str(e)[:60]}")
    return ok


def backfill_youtube(db, limit: int) -> int:
    rows = [w for w in db.list_works("youtube") if not (w.get("media_path") or "")][:limit]
    ok = 0
    for w in rows:
        out_tmpl = str(MEDIA_ROOT / f"youtube-{w['wid']}.%(ext)s")
        try:
            _ytdlp(f"https://www.youtube.com/watch?v={w['wid']}", out_tmpl, proxy=True)
            hits = [p for p in MEDIA_ROOT.glob(f"youtube-{w['wid']}.*")
                    if p.suffix in (".mp4", ".webm", ".mkv")]
            if hits:
                _save_path(db, "youtube", w["wid"], hits[0])
                ok += 1
                print(f"  [yt] ✅ {w['wid']} → {hits[0].name}")
            else:
                print(f"  [yt] {w['wid']} 无产物")
        except Exception as e:
            print(f"  [yt] {w['wid']} 失败: {repr(e)[:80]}")
    return ok


def backfill_douyin(db, limit: int) -> int:
    rows = [w for w in db.list_works("douyin") if not (w.get("media_path") or "")][:limit]
    ok = 0
    for w in rows:
        out_tmpl = str(MEDIA_ROOT / f"douyin-{w['wid']}.%(ext)s")
        try:
            _ytdlp(f"https://www.douyin.com/video/{w['wid']}", out_tmpl)
            hits = [p for p in MEDIA_ROOT.glob(f"douyin-{w['wid']}.*")
                    if p.suffix in (".mp4", ".webm")]
            if hits:
                _save_path(db, "douyin", w["wid"], hits[0])
                ok += 1
                print(f"  [dy] ✅ {w['wid']} → {hits[0].name}")
            else:
                print(f"  [dy] {w['wid']} yt-dlp 无产物（extractor 失效）")
        except Exception as e:
            print(f"  [dy] {w['wid']} 失败: {str(e)[:50]}")
    return ok


BACKFILL = {
    "xhs": backfill_xhs,
    "x": backfill_x,
    "bilibili": backfill_bilibili,
    "youtube": backfill_youtube,
    "douyin": backfill_douyin,
}


def main():
    ap = argparse.ArgumentParser(description="批量补抓媒体")
    ap.add_argument("--platforms", default="bilibili,youtube,xhs,x,douyin")
    ap.add_argument("--limit", type=int, default=5, help="每平台最多补抓条数（默认5）")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()
    db = BenchStore("data/bench.db")
    total = {}
    for p in [x.strip() for x in args.platforms.split(",") if x.strip()]:
        if p not in BACKFILL:
            continue
        print(f"\n[{p}] 补抓（上限 {args.limit}）...")
        total[p] = BACKFILL[p](db, args.limit)
    print("\n=== 补抓汇总 ===")
    for p, n in total.items():
        print(f"  {p}: +{n}")
    if not args.no_export:
        print("\n[corpus] 重导语料库...")
        print(" ", export_all())


if __name__ == "__main__":
    main()
