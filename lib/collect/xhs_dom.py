"""小红书浏览器 DOM 采集适配器。

复用项目 data/browser-profile/xhs 的持久 Chrome 登录态，通过
playwright-cli 在真实有头浏览器里打开页面并解析 DOM，绕开纯接口
签名风控。**必须遵守 RateLimiter（>=30s/次）**；IP 风控（错误码
300012）触发后需冷却数小时。

依赖：
- playwright-cli（$HOME/.codex/skills/playwright/scripts/playwright_cli.sh）
- 首次运行前用该浏览器完成一次人工登录

页面结构基于 2026-10 实测：
- 首页/explore 卡片：section.note-item，链接 a[href*="/explore/"]，标题 a.title
- 笔记页：div#detail-title（标题）、div#detail-desc（正文）、span.count（互动数）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .adapter import BaseAdapter
from .ratelimit import RateLimiter

PWCLI = os.environ.get(
    "PWCLI", str(Path.home() / ".codex/skills/playwright/scripts/playwright_cli.sh")
)
PROFILE = os.environ.get(
    "XHS_PROFILE",
    str(Path(__file__).resolve().parents[2] / "data/browser-profile/xhs"),
)


class XhsDomAdapter(BaseAdapter):
    """用持久登录态浏览器解析小红书公开页面。"""

    def __init__(self, profile: str = PROFILE, headed: bool = True):
        self.profile = profile
        self.headed = headed
        self.limiter = RateLimiter(interval=45.0)  # 小红书风控严，45s 起
        self._opened = False

    def name(self) -> str:
        return "xhs"

    # -- 浏览器控制 --------------------------------------------------
    def _cli(self, *args: str) -> str:
        cmd = [PWCLI, *args]
        if not self._opened:
            cmd += ["--persistent", "--profile", self.profile]
            if self.headed:
                cmd.append("--headed")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode != 0:
            raise RuntimeError(f"playwright-cli 失败: {res.stderr[-300:]}")
        return res.stdout

    def _eval(self, js: str) -> Any:
        out = self._cli("eval", f"() => JSON.stringify({js})")
        m = re.search(r'"(\{.*\})"', out, re.S)
        if not m:
            raise RuntimeError(f"eval 无结果: {out[-200:]}")
        return json.loads(json.loads(m.group(1)))

    def _ensure_open(self) -> None:
        if not self._opened:
            self._cli("open", "https://www.xiaohongshu.com")
            time.sleep(4)
            self._opened = True
            title = str(self._eval("document.title"))
            if "安全限制" in title:
                raise RuntimeError(
                    "小红书 IP 风控（300012）。请冷却数小时后再试，或切换网络。"
                )

    # -- BaseAdapter -------------------------------------------------
    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """uid: 关键词或完整 URL；走搜索页/探索流抓笔记卡片。"""
        self._ensure_open()
        self.limiter.wait("xhs")
        url = (
            f"https://www.xiaohongshu.com/search_result?keyword={uid}"
            if uid and not uid.startswith("http")
            else uid or "https://www.xiaohongshu.com/explore"
        )
        self._cli("open", url)
        time.sleep(5)
        cards = self._eval(
            "[...document.querySelectorAll('section.note-item')].slice(0, "
            + str(limit)
            + ").map(s => {"
            "  const a = s.querySelector('a[href]');"
            "  const t = s.querySelector('a.title, .title');"
            "  const like = s.querySelector('.like-wrapper span.count, span.count');"
            "  return {"
            "    href: a ? a.getAttribute('href') : null,"
            "    title: t ? t.innerText : '',"
            "    likes: like ? like.innerText : null"
            "  };"
            "})"
        )
        out = []
        for c in cards or []:
            href = c.get("href") or ""
            m = re.search(r"/(?:explore|search_result)/([0-9a-f]{24})", href)
            if not m:
                continue
            out.append(
                {
                    "work_id": m.group(1),
                    "title": (c.get("title") or "").strip(),
                    "likes": c.get("likes"),
                    "url": f"https://www.xiaohongshu.com/explore/{m.group(1)}",
                }
            )
        return out

    def download_media(self, wid: str, url: str) -> Path:
        """下载笔记 media 到 output/media/，命名遵循 平台-作品ID 约定。

        小红书下载有专门的开源方案（xhs-toolkit / MediaCrawler 的下载器）；
        浏览器场景先用 playwright 抓图片资源，视频留待接入 MediaCrawler。
        """
        import urllib.request

        self._ensure_open()
        self.limiter.wait("xhs")
        self._cli("open", url or f"https://www.xiaohongshu.com/explore/{wid}")
        time.sleep(5)
        srcs = self._eval(
            "[...document.querySelectorAll('meta[property=\"og:image\"]')]"
            ".map(m => m.content).filter(Boolean)"
        )
        out = self.cache_dir / f"{self.platform}-{wid}.jpg"
        if srcs:
            req = urllib.request.Request(srcs[0], headers={"User-Agent": "Mozilla/5.0"})
            out.write_bytes(urllib.request.urlopen(req, timeout=60).read())
            return out
        raise RuntimeError(f"笔记 {wid} 未取到封面图（视频类请接 MediaCrawler 下载器）")
        d = self._eval(
            "{"
            "title: (document.querySelector('#detail-title')||{}).innerText,"
            "desc: (document.querySelector('#detail-desc')||{}).innerText,"
            "counts: [...document.querySelectorAll('.engage-bar-container .count, .interact-container .count')].map(e=>e.innerText),"
            "author: (document.querySelector('.username, .author-wrapper .name')||{}).innerText"
            "}"
        )
        return {
            "work_id": work_id,
            "title": (d.get("title") or "").strip(),
            "desc": (d.get("desc") or "").strip(),
            "author": (d.get("author") or "").strip(),
            "likes/comments/collects": d.get("counts"),
        }
