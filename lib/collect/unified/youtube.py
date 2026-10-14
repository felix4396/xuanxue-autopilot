"""YouTube 统一采集：yt-dlp 频道页（免登录代理）+ API 下载。"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .base import UnifiedAccountCollector, Work

YTDLP = "/Users/shiro/.agent-reach/bin/yt-dlp"


class YoutubeCollector(UnifiedAccountCollector):
    platform = "youtube"

    def _resolve(self, name_or_uid: str) -> str:
        if name_or_uid.startswith(("UC", "@", "http")):
            return name_or_uid
        return f"@{name_or_uid}"

    def _fetch_profile(self, uid: str, limit: int):
        url = uid if uid.startswith("http") else f"https://www.youtube.com/{uid}/videos"
        out = subprocess.run(
            [YTDLP, url, "--flat-playlist", "--playlist-end", str(limit),
             "--print", "%(id)s\t%(title)s\t%(view_count)s\t%(duration)s"],
            capture_output=True, text=True, timeout=180).stdout
        works = []
        for line in out.splitlines():
            p = line.split("\t")
            if len(p) >= 3 and p[0]:
                works.append(Work(wid=p[0], title=p[1][:80], content_type="video",
                                  likes=0, comments=0,
                                  ts=0.0, url=f"https://www.youtube.com/watch?v={p[0]}"))
        return {"nickname": uid}, works

    def _fetch_work(self, w: Work) -> dict:
        out = subprocess.run(
            [YTDLP, "-J", "--skip-download", f"https://www.youtube.com/watch?v={w.wid}"],
            capture_output=True, text=True, timeout=180).stdout
        try:
            cands = [l for l in (out.stdout or '').strip().splitlines() if l.startswith("{")]
            d = json.loads(max(cands, key=len))
            return {"likes": int(d.get("like_count") or 0),
                    "comments": int(d.get("comment_count") or 0),
                    "duration": float(d.get("duration") or 0),
                    "desc": (d.get("description") or "")[:1000]}
        except Exception:
            return {}

    def _download(self, w: Work, detail: dict, dest: Path):
        out = dest / f"youtube-{w.wid}.mp4"
        if out.exists():
            return out
        r = subprocess.run([YTDLP, "-f", "best[height<=480]", "-o", str(out),
                            f"https://www.youtube.com/watch?v={w.wid}"],
                           capture_output=True, text=True, timeout=600)
        return out if out.exists() else None

    def _fetch_comments(self, w: Work) -> list[dict]:
        out = subprocess.run(
            [YTDLP, "-J", "--skip-download", "--write-comments",
             "--extractor-args", "youtube:max_comments=30,all,all,all",
             f"https://www.youtube.com/watch?v={w.wid}"],
            capture_output=True, text=True, timeout=300)
        try:
            # stdout 混有 WARNING 行，取最长 JSON 行
            cands = [l for l in (out.stdout or '').strip().splitlines() if l.startswith("{")]
            d = json.loads(max(cands, key=len))
            return [dict(cid=c.get("id"), uid=c.get("author_id"),
                         nickname=c.get("author", ""), content=(c.get("text") or "")[:500],
                         likes=int(c.get("like_count") or 0),
                         parent_id=c.get("parent", "") if c.get("parent") not in ("root", "", None) else "")
                    for c in (d.get("comments") or [])[:30]]
        except Exception:
            return []
