"""X (Twitter) 采集适配器——CDP 页面上下文 DOM 解析。

前提：项目专用 Chrome（data/browser-profile/xhs）以调试端口 9222 运行，
且已在 X 登录（登录态存 data/browser-cookies/x_state.json）。

API 直调不可行（实测）：
- GraphQL SearchTimeline 重放 404——x-client-transaction-id 一次性绑定，
  与抖音 Argus 同级的动态签名，脱离页面 JS 无法伪造。
故走 DOM 解析：article[data-testid="tweet"] 稳定结构。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

try:
    import websocket
except ImportError as e:  # pragma: no cover
    raise RuntimeError("pip install websocket-client") from e

from .adapter import BaseAdapter
from .ratelimit import RateLimiter

CDP = "http://127.0.0.1:9222"


class XDomAdapter(BaseAdapter):
    platform = "x"

    def __init__(self, cdp_base: str = CDP):
        self.cdp = cdp_base.rstrip("/")
        self.limiter = RateLimiter(interval=30.0)

    def name(self) -> str:
        return "x"

    def _ev(self, ws: websocket.WebSocket, expr: str, wait_id: int = 1,
            timeout: float = 30.0) -> Any:
        ws.send(json.dumps({
            "id": wait_id, "method": "Runtime.evaluate",
            "params": {"expression": "JSON.stringify(" + expr + ")", "returnByValue": True},
        }))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = json.loads(ws.recv())
            if m.get("id") == wait_id:
                val = m["result"]["result"]["value"]
                return json.loads(val) if isinstance(val, str) else val
        raise TimeoutError("CDP evaluate 超时")

    def _open(self, url: str, wait: float = 8.0) -> tuple[websocket.WebSocket, dict]:
        before = {t["id"] for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                  if isinstance(t, dict)}
        requests.put(f"{self.cdp}/json/new?{url}", timeout=8)
        time.sleep(wait)
        for _ in range(5):
            tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                    if isinstance(t, dict) and t["id"] not in before and t.get("type") == "page"]
            if tabs and url.split("?")[0] in (tabs[-1].get("url") or ""):
                break
            time.sleep(2)
        tab = tabs[-1]
        return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60), tab

    def search(self, keyword: str, count: int = 15) -> list[dict]:
        """搜索推文。keyword 可带 X 高级语法（如 '玄学 min_faves:100'）。"""
        self.limiter.wait("x")
        import urllib.parse
        url = f"https://x.com/search?q={urllib.parse.quote(keyword)}&src=typed_query&f=live"
        ws, tab = self._open(url)
        try:
            # 滚动触发加载
            for _ in range(2):
                self._ev(ws, "window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(3)
            r = self._ev(ws, f"""(() => {{
              const arts = [...document.querySelectorAll('article[data-testid="tweet"]')].slice(0, {count});
              return arts.map(a => {{
                const link = (a.querySelector('a[href*="/status/"]')||{{}}).href || '';
                const m = link.match(/status\\/(\\d+)/);
                return {{
                  wid: m ? m[1] : null,
                  text: ((a.querySelector('[data-testid="tweetText"]')||{{}}).innerText||'').slice(0,200),
                  user: ((a.querySelector('a[href^="/"] span')||{{}}).innerText||''),
                  user_href: ((a.querySelector('a[href^="/"][role="link"]')||{{}}).getAttribute?.('href')||''),
                  likes: (a.querySelector('[data-testid="like"]')||{{}}).innerText || '0',
                  retweets: (a.querySelector('[data-testid="retweet"]')||{{}}).innerText || '0',
                  replies: (a.querySelector('[data-testid="reply"]')||{{}}).innerText || '0',
                }};
              }}).filter(x => x.wid);
            }})()""")
            return r
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """uid: 用户 handle（不含@）或主页 URL。拉该用户时间线推文。"""
        handle = uid.lstrip("@").split("/")[-1].split("?")[0]
        self.limiter.wait("x")
        ws, tab = self._open(f"https://x.com/{handle}", wait=9.0)
        try:
            for _ in range(2):
                self._ev(ws, "window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(3)
            r = self._ev(ws, f"""(() => {{
              const arts = [...document.querySelectorAll('article[data-testid="tweet"]')].slice(0, {limit});
              return arts.map(a => {{
                const link = (a.querySelector('a[href*="/status/"]')||{{}}).href || '';
                const m = link.match(/status\\/(\\d+)/);
                return {{
                  wid: m ? m[1] : null,
                  text: ((a.querySelector('[data-testid="tweetText"]')||{{}}).innerText||'').slice(0,200),
                  likes: (a.querySelector('[data-testid="like"]')||{{}}).innerText || '0',
                  media: !!a.querySelector('[data-testid="tweetPhoto"], video'),
                }};
              }}).filter(x => x.wid && x.user_href !== '/' + {json.dumps(handle)});
            }})()""")
            return [
                {
                    "wid": x["wid"],
                    "title": x["text"],
                    "likes": self._parse_count(x.get("likes")),
                    "comments": self._parse_count(x.get("replies", "0")),
                    "shares": self._parse_count(x.get("retweets", "0")),
                    "has_media": x.get("media", False),
                    "media_url": f"https://x.com/{handle}/status/{x['wid']}",
                }
                for x in r
            ]
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    @staticmethod
    def _parse_count(s: str) -> int:
        s = (s or "0").strip().replace(",", "")
        m = re.match(r"([\d.]+)([KkMm]?)", s)
        if not m:
            return 0
        num = float(m.group(1))
        mult = {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)
        return int(num * mult)

    def download_media(self, wid: str, url: str) -> Any:
        raise NotImplementedError("X 媒体下载需从推文详情页解析 pbs.twimg.com 直链，待接")
