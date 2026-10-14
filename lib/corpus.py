"""统一产出格式：把五平台采集数据归集为标准化语料目录。

目录结构：
  output/corpus/{platform}/{wid}/
    content.md      1. 正文文案（标题+正文，frontmatter 含基础信息）
    media.*         2. 图片或视频（media.mp4 / media-1.jpg ...）
    comments.json   3. 评论列表（[{user, content, likes, time}]）
    meta.json       4. 元数据（来源、用户、点赞/转发/评论/收藏/播放等）
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from lib.bench.store import BenchStore

CORPUS_DIR = Path("output/corpus")


def _load_mc_records() -> dict[str, dict]:
    """扫 MediaCrawler jsonl：wid → 完整记录（含 MC 独有互动字段）。"""
    out: dict[str, dict] = {}
    mc = Path("vendor/MediaCrawler/data")
    if not mc.exists():
        return out
    for f in mc.rglob("search_contents*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            wid = str(rec.get("video_id") or rec.get("note_id") or rec.get("tweet_id")
                      or rec.get("aweme_id") or "")
            if wid:
                out[wid] = rec
    return out


def _load_mc_comments() -> dict[str, list[dict]]:
    """扫 MediaCrawler 评论 jsonl：wid → 评论列表。"""
    out: dict[str, list[dict]] = {}
    mc = Path("vendor/MediaCrawler/data")
    if not mc.exists():
        return out
    for f in mc.rglob("*comment*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            wid = str(rec.get("video_id") or rec.get("note_id") or rec.get("tweet_id")
                      or rec.get("aweme_id") or "")
            if not wid:
                continue
            out.setdefault(wid, []).append({
                "user": rec.get("nickname") or "",
                "content": rec.get("content") or "",
                "likes": rec.get("like_count") or 0,
                "time": rec.get("create_time") or "",
                "sub_comments": rec.get("sub_comment_count") or 0,
            })
    return out


def _fmt_count(v) -> int:
    try:
        s = str(v).replace(",", "").strip()
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        return int(float(s)) if s else 0
    except (ValueError, TypeError):
        return 0


def export_work(platform: str, work: dict, mc_rec: dict | None,
                comments: list[dict], media_src: Path | None,
                dest: Path) -> bool:
    """导出单作品到语料目录。返回是否成功。"""
    wid = work["wid"]
    dest.mkdir(parents=True, exist_ok=True)
    extra = work.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            extra = {}
    mc = mc_rec or {}

    # ---- 1. 正文文案 content.md ----
    title = work.get("title") or mc.get("title") or ""
    desc = mc.get("desc") or extra.get("desc") or ""
    if platform == "youtube" and not desc:
        desc = extra.get("description") or ""
    lines = [f"# {title}", ""]
    if desc and desc != title:
        lines += [desc, ""]
    content = "\n".join(lines).strip() + "\n"
    (dest / "content.md").write_text(content, encoding="utf-8")

    # ---- 2. 媒体（轻量资产：帧图 + 文字稿；原视频不进语料库）----
    if media_src and media_src.exists():
        if media_src.is_file():
            ext = media_src.suffix or ".bin"
            if ext.lower() in (".mp4", ".m4a", ".webm", ".mov"):
                # 原视频不拷贝；从同名素材包取帧做封面
                pack = media_src.parent / media_src.stem
                frames = sorted(pack.glob("frames/*.jpg")) or sorted(pack.glob("frames/*.png"))
                if frames:
                    shutil.copy2(frames[0], dest / "media-cover.jpg")
            else:
                shutil.copy2(media_src, dest / f"media{ext}")  # 图片类直接拷
        elif media_src.is_dir():
            # 素材包目录：封面帧 + 精简帧（首/中/尾）+ asr 文字稿
            frames = sorted(media_src.glob("frames/*.jpg")) or sorted(media_src.glob("frames/*.png"))
            if frames:
                shutil.copy2(frames[0], dest / "media-cover.jpg")
                for tag, f in (("first", frames[0]),
                               ("mid", frames[len(frames) // 2]),
                               ("last", frames[-1])):
                    shutil.copy2(f, dest / f"frame-{tag}{f.suffix}")
            asr = media_src / "asr.json"
            if asr.exists():
                shutil.copy2(asr, dest / "asr.json")

    # ---- 3. 评论 comments.json ----
    if comments:
        (dest / "comments.json").write_text(
            json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 4. 元数据 meta.json ----
    # 媒体状态：可选件——有就存，没有就标注原因（兼容纯文本内容）
    media_files = sorted([p.name for p in dest.glob("media*")])
    if (dest / "asr.json").exists() or (dest / "frame-first.jpg").exists():
        media_meta = {"status": "ok", "files": media_files,
                      "type": "video_frames" if (dest / "frame-first.jpg").exists() else "image"}
    elif media_files:
        media_meta = {"status": "ok", "files": media_files, "type": "image"}
    elif media_src is None:
        media_meta = {"status": "no_media", "files": [], "type": None,
                      "reason": "该作品为纯文本内容或平台未提供媒体"}
    else:
        media_meta = {"status": "failed", "files": [], "type": None,
                      "reason": "媒体文件下载失败（源失效/风控/格式不支持）"}
    meta = {
        "wid": wid,
        "platform": platform,
        "source_url": work.get("source_url") or extra.get("media_url") or mc.get("video_url") or "",
        "user": {
            "uid": work.get("uid") or "",
            "nickname": mc.get("nickname") or extra.get("nickname") or extra.get("user") or "",
        },
        "title": title,
        "metrics": {
            "likes": work.get("likes") or _fmt_count(mc.get("liked_count")),
            "comments": work.get("comments") or _fmt_count(mc.get("video_comment")),
            "shares": work.get("shares") or _fmt_count(mc.get("video_share_count")),
            "collects": _fmt_count(mc.get("video_favorite_count")),
            "views": work.get("views") or _fmt_count(mc.get("video_play_count")),
            "danmaku": _fmt_count(mc.get("video_danmaku")),
            "coins": _fmt_count(mc.get("video_coin_count")),
        },
        "media": media_meta,
        "keyword": mc.get("source_keyword") or extra.get("source_kw") or "",
        "cover_url": extra.get("cover_url") or mc.get("video_cover_url") or "",
        "media_url": extra.get("media_url") or mc.get("video_url") or "",
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    # youtube views 在 extra
    if platform == "youtube" and extra.get("views"):
        meta["metrics"]["views"] = _fmt_count(extra["views"])
    (dest / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def export_all(db_path: str = "data/bench.db", platforms: list[str] | None = None) -> dict:
    """全量导出。返回统计。"""
    db = BenchStore(db_path)
    mc_recs = _load_mc_records()
    mc_comments = _load_mc_comments()
    media_root = Path("output/media")
    platforms = platforms or ["bilibili", "douyin", "xhs", "x", "youtube", "weibo"]
    stats = {"exported": 0, "with_media": 0, "with_comments": 0, "skipped": 0}
    for p in platforms:
        for w in db.list_works(p):
            wid = w["wid"]
            dest = CORPUS_DIR / p / wid
            mc_rec = mc_recs.get(wid)
            comments = mc_comments.get(wid, [])
            # 找媒体：文件 output/media/{p}-{wid}.ext 或目录 output/media/{p}-{wid}/
            media_src = None
            cands = sorted(media_root.glob(f"{p}-{wid}*"))
            # 文件优先（.mp4/.jpg），目录次之
            files = [c for c in cands if c.is_file()]
            vids = [c for c in files if c.suffix.lower() == ".mp4"]
            media_src = (vids[0] if vids
                         else (files[0] if files else (cands[0] if cands else None)))
            try:
                if export_work(p, w, mc_rec, comments, media_src, dest):
                    stats["exported"] += 1
                    if list(dest.glob("media.*")) or list(dest.glob("media-*")):
                        stats["with_media"] += 1
                    if (dest / "comments.json").exists():
                        stats["with_comments"] += 1
            except Exception as e:
                print(f"  skip {p}/{wid}: {e}")
                stats["skipped"] += 1
    return stats
