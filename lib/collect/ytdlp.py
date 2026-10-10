"""yt-dlp 适配器 — YouTube 侧批量下载（字幕/封面/视频）。

关键能力取自方案：`yt-dlp --write-sub --write-auto-sub --write-thumbnail`。
仅实现下载与元数据提取；不复刻 yt-dlp 已有功能。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lib.collect.adapter import BaseAdapter


class YtdlpAdapter(BaseAdapter):
    platform = "youtube"

    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """uid = 频道 URL 或 handle。用 --flat-playlist 拉作品列表，不下载媒体。"""
        out = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(view_count)s",
             "--playlist-end", str(limit), uid],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            raise RuntimeError(f"yt-dlp 失败: {out.stderr[:300]}")
        works = []
        for line in out.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                works.append({
                    "wid": parts[0], "title": parts[1],
                    "likes": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                })
        return works

    def download_media(self, wid: str, url: str) -> Path:
        """下载字幕+封面+视频到 output/media/youtube-{wid}.*。"""
        outtmpl = str(self.cache_dir / f"{self.platform}-{wid}.%(ext)s")
        out = subprocess.run(
            ["yt-dlp", "--write-sub", "--write-auto-sub", "--write-thumbnail",
             "--skip-download" if self._sub_only else "-f", "mp4",
             "-o", outtmpl, url],
            capture_output=True, text=True, timeout=600,
        )
        if out.returncode != 0:
            raise RuntimeError(f"yt-dlp 下载失败: {out.stderr[:300]}")
        self._sub_only = False
        return self.cache_dir / f"{self.platform}-{wid}.mp4"

    _sub_only = False
