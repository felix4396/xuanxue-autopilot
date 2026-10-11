"""抖音浏览器采集适配器（CDP 模式）。

前提：用项目登录态 profile 启动带调试端口的 Chrome（绝不动用户日常浏览器）：

    nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
      --remote-debugging-port=9222 --remote-allow-origins='*' \\
      --user-data-dir="<项目>/data/browser-profile/xhs" --no-first-run &

实测结论（2026-10-11）：
- 搜索接口被 IP 级软风控（返回空，不弹验证码）
- 用户主页可打开，但作品列表接口同样被软风控（'服务异常，重新刷新拉取数据'占位）
- 视频详情页正常（标题/作者可解析）→ fetch_detail 可用
- 解析严格限定 [data-e2e="user-post-list"] 容器，避免混入页面底部推荐流
- sec_uid 来源：TikHub 用户搜索（单次 0.001 刀）或人工提供
- yt-dlp 抖音 extractor 已失效（签名对抗），媒体下载暂缺
- 适配器已就绪，IP 冷却或更换网络后即可使用
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

try:
    import websocket  # websocket-client
except ImportError as e:  # pragma: no cover
    raise RuntimeError("pip install websocket-client") from e

from .adapter import BaseAdapter
from .ratelimit import RateLimiter

CDP = "http://127.0.0.1:9222"


class DouyinDomAdapter(BaseAdapter):
    """通过 CDP 控制带登录态的真实 Chrome，解析抖音页面 DOM。"""

    def __init__(self, cdp_base: str = CDP):
        self.cdp = cdp_base.rstrip("/")
        self.limiter = RateLimiter(interval=40.0)

    def name(self) -> str:
        return "douyin"

    # -- CDP 基础 ----------------------------------------------------
    def _ev(self, ws: websocket.WebSocket, expr: str) -> Any:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": "JSON.stringify(" + expr + ")", "returnByValue": True},
        }))
        val = json.loads(ws.recv())["result"]["result"]["value"]
        return json.loads(val) if isinstance(val, str) else val

    def _open(self, url: str, wait: float = 6.0) -> websocket.WebSocket:
        # 记录打开前已存在的 tab，新建后取差集精确定位（避免撞残留 tab 串数据）
        before = {t["id"] for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                  if isinstance(t, dict)}
        requests.put(f"{self.cdp}/json/new?{url}", timeout=8)
        time.sleep(wait)
        for _ in range(5):
            tabs = requests.get(f"{self.cdp}/json/list", timeout=5).json()
            new_tabs = [t for t in tabs if isinstance(t, dict) and t["id"] not in before
                        and t.get("type") == "page"]
            if new_tabs:
                tab = new_tabs[-1]
                if url.split("?")[0].rstrip("/") in (tab.get("url") or ""):
                    break
            time.sleep(2)
        else:
            # 兜底：按 URL 前缀匹配
            tab = next(t for t in tabs if isinstance(t, dict)
                       and url.split("?")[0].rstrip("/") in (t.get("url") or ""))
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=20)
        self._tab = tab
        return ws

    def _close(self, ws: websocket.WebSocket) -> None:
        try:
            requests.get(f"{self.cdp}/json/close/{self._tab['id']}", timeout=5)
        finally:
            ws.close()

    # -- BaseAdapter -------------------------------------------------
    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """uid: 抖音 sec_user_id 或用户主页 URL。解析用户主页作品列表。"""
        url = uid if uid.startswith("http") else f"https://www.douyin.com/user/{uid}"
        self.limiter.wait("douyin")
        ws = self._open(url, wait=8.0)
        try:
            # 滚动触发懒加载
            for _ in range(2):
                self._ev(ws, "window.scrollTo(0, document.body.scrollHeight/2)")
                time.sleep(3)
            info = self._ev(ws, """({
              nickname: (document.querySelector('[data-e2e="user-info"] .j5WZzJdp') || {}).innerText || '',
              fans: ((document.querySelector('[data-e2e="user-info-fans"]') || {}).innerText || '').replace(/[^0-9.万千wW]/g, '')
            })""")
            # 只从 user-post-list 容器提取，避免混入页面底部"推荐"流
            posts = self._ev(ws, """(() => {
              const root = document.querySelector('[data-e2e="user-post-list"] ul[data-e2e="scroll-list"]');
              if (!root) return {err: 'no list'};
              if (root.querySelectorAll('a[href*="/video/"]').length === 0) {
                const fail = document.querySelector('.Zj5Fgmnv');
                return {err: fail ? 'rate_limited' : 'empty'};
              }
              return {vids: [...new Set(
                [...root.querySelectorAll('a')]
                  .filter(a => /video\\/(\\d+)/.test(a.href))
                  .map(a => a.href.match(/video\\/(\\d+)/)[1])
              )].slice(0, """ + str(limit) + """)};
            })()""")
            if isinstance(posts, dict) and "err" in posts:
                if posts["err"] == "rate_limited":
                    raise RuntimeError(
                        "抖音用户作品列表被 IP 级软风控（'服务异常'占位）。"
                        "等待冷却/更换网络后重试；视频详情页通常仍可用。")
                return []  # 无作品或列表未加载
            vids = posts["vids"]
            return [
                {
                    "wid": v,
                    "nickname": info.get("nickname", ""),
                    "fans": info.get("fans", ""),
                    "url": f"https://www.douyin.com/video/{v}",
                    "title": "",  # 详情页补全
                }
                for v in vids
            ]
        finally:
            self._close(ws)

    def fetch_detail(self, wid: str) -> dict:
        """打开视频详情页取标题等元数据。"""
        self.limiter.wait("douyin")
        ws = self._open(f"https://www.douyin.com/video/{wid}", wait=6.0)
        try:
            d = self._ev(ws, """({
              title: document.title.replace(/ - 抖音(精选)?.*/, '').slice(0, 80),
              author: (document.querySelector('a[href*="/user/"] span') || {}).innerText || ''
            })""")
            return {"wid": wid, **d}
        finally:
            self._close(ws)

    def download_media(self, wid: str, url: str) -> Path:
        raise NotImplementedError(
            "抖音视频下载需新 cookies 方案（yt-dlp extractor 已失效）；"
            "元数据采集不受影响，媒体下载待接 MediaCrawler 下载器或 TikHub"
        )
