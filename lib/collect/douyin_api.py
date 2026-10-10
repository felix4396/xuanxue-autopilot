"""抖音采集适配器：对接 Douyin_TikTok_Download_API (dtk v4) 本机服务。

服务启动方式（已在本机 8080 部署）：
    cd /tmp/dtk_v4 && .venv/bin/python start.py
配置：crawlers/douyin/web/config.yaml 的 Cookie / User-Agent
（用浏览器登录态注入，见 docs/devlog.md 第三轮记录）

接口：GET /api/douyin/web/fetch_user_post_videos?sec_user_id=...&count=..
      GET /api/douyin/web/fetch_one_video?aweme_id=...
"""
from __future__ import annotations

import os
from typing import Any

import requests

from .adapter import BaseAdapter


class DouyinApiAdapter(BaseAdapter):
    """通过自托管 dtk 服务采集抖音用户作品与视频详情。"""

    def __init__(self, base_url: str | None = None, timeout: int = 40):
        self.base = (base_url or os.environ.get("DTK_BASE_URL", "http://127.0.0.1:8080")).rstrip("/")
        self.timeout = timeout

    # -- BaseAdapter -------------------------------------------------
    def name(self) -> str:
        return "douyin"

    def fetch_works(self, target: str, limit: int = 10) -> list[dict[str, Any]]:
        """target: 抖音 sec_user_id（形如 MS4wLjAB...）。"""
        r = requests.get(
            f"{self.base}/api/douyin/web/fetch_user_post_videos",
            params={"sec_user_id": target, "max_cursor": 0, "count": limit},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json().get("data") or {}
        out: list[dict[str, Any]] = []
        for a in data.get("aweme_list") or []:
            stats = a.get("statistics") or {}
            out.append(
                {
                    "work_id": a.get("aweme_id") or a.get("aweme_id_str"),
                    "title": a.get("desc") or "",
                    "likes": stats.get("digg_count"),
                    "comments": stats.get("comment_count"),
                    "collects": stats.get("collect_count"),
                    "shares": stats.get("share_count"),
                    "author": (a.get("author") or {}).get("nickname"),
                    "url": f"https://www.douyin.com/video/{a.get('aweme_id')}",
                }
            )
        return out

    def fetch_detail(self, work_id: str) -> dict[str, Any]:
        r = requests.get(
            f"{self.base}/api/douyin/web/fetch_one_video",
            params={"aweme_id": work_id},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def download_media(self, work_id: str, dest_dir: str) -> str:
        """用 yt-dlp 下载无水印视频（dtk 也提供下载端点，yt-dlp 更稳）。"""
        import subprocess

        url = f"https://www.douyin.com/video/{work_id}"
        out = os.path.join(dest_dir, f"douyin-{work_id}.mp4")
        env = {k: v for k, v in os.environ.items() if k.lower() != "all_proxy"}
        subprocess.run(
            ["yt-dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
             "-o", out, url],
            check=True, env=env, capture_output=True,
        )
        return out
