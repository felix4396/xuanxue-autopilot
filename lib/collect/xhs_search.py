"""小红书搜索/作品采集适配器——CDP 页面上下文 DOM 解析。

前提：项目专用 Chrome（调试端口 9222）+ 登录态（data/browser-cookies/xhs_state.json）。

实测（2026-10-12）：
- IP 风控已解除（此前 300012 冷却完毕）
- 直接 fetch /api/sns/web/v1/search/notes 500（X-s/X-t 签名由页面 JS 拦截器注入，
  裸 fetch 拿不到）→ DOM 解析搜索结果页
- 搜索结果页 section.note-item 含 标题/赞数/作者/笔记ID，可继续开笔记详情
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any

import requests

try:
    import websocket
except ImportError as e:  # pragma: no cover
    raise RuntimeError("pip install websocket-client") from e

from .adapter import BaseAdapter
from .ratelimit import RateLimiter

CDP = "http://127.0.0.1:9222"


class XhsSearchAdapter(BaseAdapter):
    platform = "xhs"

    def __init__(self, cdp_base: str = CDP):
        self.cdp = cdp_base.rstrip("/")
        # 小红书风控史：控频要保守
        self.limiter = RateLimiter(interval=45.0)

    def name(self) -> str:
        return "xhs"

    def _ev(self, ws: websocket.WebSocket, expr: str, timeout: float = 30.0) -> Any:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": "JSON.stringify(" + expr + ")", "returnByValue": True},
        }))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                val = m["result"]["result"]["value"]
                return json.loads(val) if isinstance(val, str) else val
        raise TimeoutError("CDP evaluate 超时")

    def _open(self, url: str, wait: float = 9.0) -> tuple[websocket.WebSocket, dict]:
        before = {t["id"] for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                  if isinstance(t, dict)}
        requests.put(f"{self.cdp}/json/new?{url}", timeout=8)
        time.sleep(wait)
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t["id"] not in before and t.get("type") == "page"]
        tab = tabs[-1]
        return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60), tab

    @staticmethod
    def _parse_count(s: str) -> int:
        s = (s or "0").strip().replace(",", "")
        m = re.match(r"([\d.]+)\s*([万wW]?)", s)
        if not m:
            return 0
        num = float(m.group(1))
        mult = 10_000 if m.group(2) else 1
        return int(num * mult)

    def search(self, keyword: str, count: int = 20) -> list[dict]:
        """搜索笔记。返回 title/likes/author/note_id（可继续 fetch_detail）。"""
        self.limiter.wait("xhs")
        kw = urllib.parse.quote(keyword)
        ws, tab = self._open(f"https://www.xiaohongshu.com/search_result?keyword={kw}")
        try:
            # 页面卡片懒渲染：0 张时等待重取（冷启动首跑渲染可能慢于 9s）
            expr = f"""(() => {{
              const cards = [...document.querySelectorAll('section.note-item')].slice(0, {count});
              return cards.map(s => {{
                const a = s.querySelector('a[href*="/explore/"], a[href*="/search_result/"]');
                const href = (a||{{}}).href || '';
                const m = href.match(/(?:explore|search_result)\\/([0-9a-f]{{24}})/);
                return {{
                  note_id: m ? m[1] : null,
                  title: ((s.querySelector('.title, a.title')||{{}}).innerText||'').slice(0,80),
                  likes: (s.querySelector('.count, [class*=count]')||{{}}).innerText || '0',
                  author: ((s.querySelector('.author .name, [class*=author] .name')||{{}}).innerText||''),
                }};
              }}).filter(x => x.note_id);
            }})()"""
            r = []
            for attempt in range(4):
                time.sleep(3)
                r = self._ev(ws, expr)
                if r:
                    break
                print(f"  [xhs] 第{attempt+1}次未取到卡片，等待重试…")
            return [
                {
                    "wid": x["note_id"],
                    "title": x["title"],
                    "likes": self._parse_count(x.get("likes")),
                    "author": x.get("author", ""),
                    "media_url": f"https://www.xiaohongshu.com/explore/{x['note_id']}",
                }
                for x in r
            ]
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def fetch_detail(self, note_id: str) -> dict:
        """笔记详情（正文/图片数/互动数据）。控频 45s。"""
        self.limiter.wait("xhs")
        ws, tab = self._open(f"https://www.xiaohongshu.com/explore/{note_id}", wait=8.0)
        try:
            r = self._ev(ws, """(() => {
              const title = (document.querySelector('#detail-title, [class*=title]')||{}).innerText || '';
              const desc = (document.querySelector('#detail-desc, [class*=desc]')||{}).innerText || '';
              const author = (document.querySelector('.username, [class*=author] [class*=name]')||{}).innerText || '';
              const likeEl = document.querySelector('[class*=like] .count, [class*=like-wrapper] .count');
              const imgs = document.querySelectorAll('img[class*=note], [class*=slide] img').length;
              const video = !!document.querySelector('video');
              return {title: title.slice(0,60), desc: desc.slice(0,300), author,
                      likes: (likeEl||{}).innerText || '0', imgs, video};
            })()""")
            return {"wid": note_id, **r}
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """uid: 用户主页 URL 或 user_id hex。拉该用户笔记列表。"""
        self.limiter.wait("xhs")
        user_url = uid if uid.startswith("http") else f"https://www.xiaohongshu.com/user/profile/{uid}"
        ws, tab = self._open(user_url)
        try:
            time.sleep(3)
            r = self._ev(ws, f"""(() => {{
              const cards = [...document.querySelectorAll('section.note-item, [class*=note-item]')].slice(0, {limit});
              return cards.map(s => {{
                const a = s.querySelector('a[href*="/explore/"], a[href*="/user/"]');
                const href = (a||{{}}).href || '';
                const m = href.match(/(?:explore|search_result)\\/([0-9a-f]{{24}})/);
                return {{
                  note_id: m ? m[1] : null,
                  title: ((s.querySelector('.title, a.title')||{{}}).innerText||'').slice(0,60),
                  likes: (s.querySelector('.count, [class*=count]')||{{}}).innerText || '0',
                }};
              }}).filter(x => x.note_id);
            }})()""")
            return [
                {
                    "wid": x["note_id"],
                    "title": x["title"],
                    "likes": self._parse_count(x.get("likes")),
                    "media_url": f"https://www.xiaohongshu.com/explore/{x['note_id']}",
                }
                for x in r
            ]
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def download_media(self, wid: str, url: str) -> Any:
        raise NotImplementedError("小红书图片/视频下载待接（需从详情页解析 CDN 直链）")
