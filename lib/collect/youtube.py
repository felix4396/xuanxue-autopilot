"""YouTube 采集适配器（yt-dlp，走代理）。

无需登录态（公开数据）。搜索用 ytsearch，频道作品用频道 URL。
代理从环境变量 https_proxy 读取（默认 127.0.0.1:7897）。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .adapter import BaseAdapter

PROXY = "http://127.0.0.1:7897"


class YoutubeAdapter(BaseAdapter):
    platform = "youtube"

    def name(self) -> str:
        return "youtube"

    def _ytdlp(self, *args: str, timeout: int = 120) -> str:
        cmd = [
            "env", "-u", "all_proxy",
            f"https_proxy={PROXY}", f"http_proxy={PROXY}",
            str(Path(__file__).resolve().parents[1] / ".." / ".venv" / "bin" / "yt-dlp"),
            "--no-warnings", *args,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 and not r.stdout.strip():
            raise RuntimeError(f"yt-dlp 失败: {r.stderr.strip().splitlines()[-1][:120]}")
        return r.stdout

    def search(self, keyword: str, count: int = 10) -> list[dict]:
        out = self._ytdlp(
            f"ytsearch{count}:{keyword}", "--flat-playlist",
            "--print", "%(id)s\t%(title)s\t%(view_count)s\t%(channel)s\t%(channel_id)s",
        )
        results = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                results.append({
                    "wid": parts[0],
                    "title": parts[1],
                    "likes": 0,  # flat 模式无赞数，详情补
                    "views": int(parts[2] or 0),
                    "channel": parts[3] if len(parts) > 3 else "",
                    "channel_id": parts[4] if len(parts) > 4 else "",
                    "media_url": f"https://www.youtube.com/watch?v={parts[0]}",
                })
        return results

    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """uid: 频道 URL / @handle / channel_id / 视频ID（单个详情）。"""
        if uid.startswith(("http", "@")) or uid.startswith("UC"):
            url = uid if uid.startswith("http") else f"https://www.youtube.com/@{uid.lstrip('@')}/videos"
            out = self._ytdlp(url, "--flat-playlist", "--playlist-end", str(limit),
                              "--print", "%(id)s\t%(title)s\t%(view_count)s")
            return [
                {
                    "wid": p.split("\t")[0],
                    "title": p.split("\t")[1] if "\t" in p else "",
                    "views": int(p.split("\t")[2] or 0) if "\t" in p else 0,
                    "media_url": f"https://www.youtube.com/watch?v={p.split(chr(9))[0]}",
                }
                for p in out.splitlines() if "\t" in p
            ]
        # 单视频详情
        out = self._ytdlp(
            f"https://www.youtube.com/watch?v={uid}", "--print", "%(title)s\t%(view_count)s\t%(like_count)s",
        )
        t, v, l = (out.strip().split("\t") + ["", "", ""])[:3]
        return [{"wid": uid, "title": t, "views": int(v or 0), "likes": int(l or 0),
                 "media_url": f"https://www.youtube.com/watch?v={uid}"}]

    def download_media(self, wid: str, url: str) -> Path:
        """下载视频（≤480p 足够 ASR/抽帧用）。"""
        out = Path("output/media") / f"youtube-{wid}.mp4"
        if out.exists():
            return out
        self._ytdlp(
            "-f", "bv*[height<=480]+ba/b", "--merge-output-format", "mp4",
            "-o", str(out), url, timeout=600,
        )
        return out
