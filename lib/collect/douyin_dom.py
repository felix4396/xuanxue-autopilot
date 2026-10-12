"""抖音浏览器采集适配器（CDP 模式）。

前提：用项目登录态 profile 启动带调试端口的 Chrome（绝不动用户日常浏览器）：

    nohup "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
      --remote-debugging-port=9222 --remote-allow-origins='*' \\
      --user-data-dir="<项目>/data/browser-profile/xhs" --no-first-run &

实测结论（2026-10-11，抓包+逆向验证）：
- **正解已找到：页面内 fetch**。在 douyin.com 页面上下文里用 CDP Runtime.evaluate
  发 fetch(credentials:'include')，页面 JS 拦截器自动注入 a_bogus/msToken/Argus 全套签名，
  环境一致 → 服务端全放行。实测用户作品接口 /aweme/v1/web/aweme/post/ 返回
  200 + 17条作品含赞/评数据。
- 外部直调（requests + MC 的 a_bogus JS 签名）不可行：
  * 搜索接口：即使签名正确也返回 search_nil_type=verify_check（服务端要求人机验证）
  * post 接口：403 ArgusSecurityPlugin "Uifid Not Found"/"Signature Not Found"
    （Argus 校验 a_bogus 与 TLS指纹/JS环境一致性，脱离浏览器环境无法伪造）
- 搜索接口在页面内 fetch 也被 verify_check 拦（前端验证中心组件拦截），但
  **用户作品列表接口完全可用** → 对标账号发现走 TikHub 用户搜索（0.001刀/次），
  作品采集走本 adapter。
- yt-dlp 抖音 extractor 已失效；媒体下载待接（可从接口响应的 video.play_addr 直取）
- cookie: data/browser-cookies/douyin.txt（58条含httpOnly sessionid）；msToken 取
  localStorage.xmst（184字符），fetch 方案下无需手动传
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

    # -- 页面内 fetch（正解：借用页面 JS 签名环境） ---------------------
    COMMON_PARAMS = (
        "device_platform=webapp&aid=6383&channel=channel_pc_web"
        "&version_code=190600&version_name=19.6.0&update_version_code=170400"
        "&pc_client_type=1&cookie_enabled=true&browser_language=zh-CN"
        "&browser_platform=MacIntel&browser_name=Chrome&browser_version=125.0.0.0"
        "&browser_online=true&engine_name=Blink&os_name=Mac OS&os_version=10.15.7"
        "&cpu_core_num=8&device_memory=8&platform=PC&screen_width=2560"
        "&screen_height=1440&effective_type=4g&round_trip_time=50"
        "&webid=7378810571505847586"
    )

    def _get_home_ws(self) -> websocket.WebSocket:
        """拿一个已打开的 douyin.com 页面上下文（没有则新开）。"""
        tabs = requests.get(f"{self.cdp}/json/list", timeout=5).json()
        tab = next((t for t in tabs if isinstance(t, dict)
                    and "www.douyin.com" in (t.get("url") or "")), None)
        if not tab:
            requests.put(f"{self.cdp}/json/new?https://www.douyin.com", timeout=8)
            time.sleep(6)
            tabs = requests.get(f"{self.cdp}/json/list", timeout=5).json()
            tab = next(t for t in tabs if isinstance(t, dict)
                       and "www.douyin.com" in (t.get("url") or ""))
        return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)

    def _page_fetch(self, path_qs: str) -> dict:
        """在 douyin.com 页面上下文内 fetch 抖音 API（页面 JS 自动完成签名）。"""
        self.limiter.wait("douyin")
        ws = self._get_home_ws()
        try:
            expr = (
                "(async () => { const r = await fetch('" + path_qs + "',"
                " {credentials:'include'}); const d = await r.json();"
                " return JSON.stringify(d); })()"
            )
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": expr, "awaitPromise": True, "returnByValue": True},
            }))
            val = json.loads(ws.recv())["result"]["result"]["value"]
            return json.loads(val) if isinstance(val, str) else val
        finally:
            ws.close()

    # -- BaseAdapter -------------------------------------------------
    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """uid: 抖音 sec_user_id。页面内 fetch 用户作品接口（含赞/评数据）。

        实测可用（绕过 verify_check 风控），has_more 分页由 max_cursor 翻页。
        """
        sec_uid = uid.split("user/")[-1].split("?")[0] if uid.startswith("http") else uid
        data = self._page_fetch(
            "/aweme/v1/web/aweme/post/?"
            + self.COMMON_PARAMS
            + f"&sec_user_id={sec_uid}&max_cursor=0&locate_query=false"
              "&show_live_replay_strategy=1&need_module_list=1&list_type=multi"
              f"&count={min(limit, 20)}"
        )
        if data.get("status_code") != 0 and not data.get("aweme_list"):
            raise RuntimeError(f"抖音接口异常: {json.dumps(data, ensure_ascii=False)[:120]}")
        return [
            {
                "wid": a.get("aweme_id"),
                "title": a.get("desc", ""),
                "likes": (a.get("statistics") or {}).get("digg_count", 0),
                "comments": (a.get("statistics") or {}).get("comment_count", 0),
                "shares": (a.get("statistics") or {}).get("share_count", 0),
                "create_time": a.get("create_time"),
                "media_url": f"https://www.douyin.com/video/{a.get('aweme_id')}",
                # 视频直链（无水印）—— 下载用
                "play_addr": (((a.get("video") or {}).get("play_addr") or {}).get("url_list") or [None])[0],
            }
            for a in (data.get("aweme_list") or [])[:limit]
        ]

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
