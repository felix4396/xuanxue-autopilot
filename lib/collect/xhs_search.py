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
from pathlib import Path
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
        """搜索笔记。从 __INITIAL_STATE__.search.feeds 直取（id + xsec_token 全齐）。

        DOM 的 a.href 不带 token（点击时前端才从 state 拼），必须走 state 通道。
        """
        self.limiter.wait("xhs")
        kw = urllib.parse.quote(keyword)
        ws, tab = self._open(f"https://www.xiaohongshu.com/search_result?keyword={kw}")
        try:
            expr = f"""(() => {{
              const feeds = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.search
                && window.__INITIAL_STATE__.search.feeds;
              const arr = feeds && (feeds.value || feeds) || [];
              return arr.slice(0, {count}).map(n => ({{
                note_id: (n.id||'').slice(0,24),
                xsec_token: n.xsecToken || '',
                title: ((n.noteCard||{{}}).displayTitle || (n.noteCard||{{}}).title || '').slice(0,80),
                likes: (((n.noteCard||{{}}).interactInfo||{{}}).likedCount) || '0',
                author: (((n.noteCard||{{}}).user||{{}}).nickname) || '',
                type: (n.noteCard||{{}}).type || ''
              }})).filter(x => x.note_id.length === 24);
            }})()"""
            r = []
            for attempt in range(4):
                time.sleep(3)
                r = self._ev(ws, expr)
                if r:
                    break
                print(f"  [xhs] 第{attempt+1}次未取到结果，等待重试…")
            return [
                {
                    "wid": x["note_id"],
                    "title": x["title"],
                    "likes": self._parse_count(x.get("likes")),
                    "author": x.get("author", ""),
                    "media_url": f"https://www.xiaohongshu.com/explore/{x['note_id']}",
                    "source_kw": keyword,
                    "xsec_token": x.get("xsec_token", ""),
                }
                for x in r
            ]
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

    def _grab_session_headers(self) -> dict:
        """从页面任一 API 请求抓会话级头（X-S-Common/b3/xray——同会话内为常量，实测 8 接口同值）。"""
        ws, tab = self._open("https://www.xiaohongshu.com/", wait=6.0)
        try:
            ws.send(json.dumps({"id": 1, "method": "Network.enable"})); ws.recv()
            ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
                "expression": "location.reload(); 1", "returnByValue": True}}))
            ws.recv()
            deadline = time.time() + 12
            ws.settimeout(3)
            while time.time() < deadline:
                try:
                    m = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
                if m.get("method") == "Network.requestWillBeSent":
                    h = {k.lower(): v for k, v in m["params"]["request"]["headers"].items()}
                    if "x-s-common" in h:
                        return {"X-S-Common": h["x-s-common"],
                                "x-b3-traceid": h.get("x-b3-traceid", ""),
                                "x-xray-traceid": h.get("x-xray-traceid", "")}
            raise RuntimeError("未抓到会话头")
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def fetch_note_detail(self, note_id: str, xsec_token: str = "") -> dict:
        """用页面内 _webmsxyw 现签 X-s/X-t + 会话常量头调 /api/sns/web/v1/feed 拿完整详情。

        抓包逆向结论：
        - X-s/X-t 绑定 method+path+body，必须逐请求现签（window._webmsxyw(path, data)）
        - X-S-Common/x-b3-traceid/x-xray-traceid 会话内常量，不绑 URL
        - feed body 必须带 xsec_source=pc_search + xsec_token（搜索结果链接里带的防风控 token）
        """
        if not getattr(self, "_session_hdrs", None):
            self._session_hdrs = self._grab_session_headers()
        # 在搜索结果页上下文发（与真实点击行为一致：Referer 为搜索页）
        import urllib.parse as _up
        ws, tab = self._open("https://www.xiaohongshu.com/search_result?keyword=%E7%8E%84%E5%AD%A6", wait=8.0)
        try:
            body = {"source_note_id": note_id,
                    "image_formats": ["jpg", "webp", "avif"],
                    "extra": {"need_body_topic": "1"},
                    "xsec_source": "pc_search",
                    "xsec_token": xsec_token}
            expr = """(async () => {
              const path = '/api/sns/web/v1/feed';
              const body = __BODY__;
              const sign = await window._webmsxyw(path, body);
              const r = await fetch('https://edith.xiaohongshu.com' + path, {
                method: 'POST', credentials: 'include',
                headers: {'Content-Type': 'application/json',
                  'X-s': sign['X-s'], 'X-t': String(sign['X-t']),
                  'X-S-Common': __COMMON__, 'x-b3-traceid': __B3__, 'x-xray-traceid': __XRAY__},
                body: JSON.stringify(body)});
              return await r.text();
            })()"""
            expr = (expr.replace('__BODY__', json.dumps(body))
                        .replace('__COMMON__', json.dumps(self._session_hdrs["X-S-Common"]))
                        .replace('__B3__', json.dumps(self._session_hdrs["x-b3-traceid"]))
                        .replace('__XRAY__', json.dumps(self._session_hdrs["x-xray-traceid"])))
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}))
            t0 = time.time()
            text = ""
            while time.time() - t0 < 30:
                m = json.loads(ws.recv())
                if m.get("id") == 1:
                    if "exceptionDetails" in m.get("result", {}):
                        raise RuntimeError(f"页面执行失败: {json.dumps(m['result']['exceptionDetails'])[:150]}")
                    text = m["result"]["result"]["value"] or ""
                    break
            j = json.loads(text)
            if j.get("code") != 0:
                raise RuntimeError(f"feed 接口 code={j.get('code')} msg={j.get('msg','')[:60]}")
            items = (j.get("data") or {}).get("items") or []
            note = (items[0].get("noteCard") if items else {}) or {}
            user = (note.get("user") or {})
            interact = note.get("interactInfo") or {}
            video = ((note.get("video") or {}).get("media") or {}).get("streamUrl") or \
                    (((note.get("video") or {}).get("media") or {}).get("videoUrl")) or ""
            images = [i.get("urlDefault") or ((i.get("infoList") or [{}])[-1].get("url", ""))
                      for i in (note.get("imageList") or [])]
            return {"wid": note_id,
                    "title": note.get("title") or note.get("displayTitle") or "",
                    "desc": (note.get("desc") or "")[:500],
                    "author": user.get("nickname") or user.get("userId") or "",
                    "likes": self._parse_count(interact.get("likedCount", "0")),
                    "comments": self._parse_count(interact.get("commentCount", "0")),
                    "collects": self._parse_count(interact.get("collectedCount", "0")),
                    "images": images, "video": video,
                    "type": note.get("type", "")}
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def fetch_media_urls(self, keyword: str, note_id: str) -> dict:
        """搜索页点进笔记（真实用户路径）→ 解析媒体直链。

        直开 /explore/{id} 会空渲染（SSR noteDetailMap 为空，SPA 异步拉数据），
        从搜索结果点进最稳。返回 {images: [...], video: str}。
        """
        self.limiter.wait("xhs")
        kw = urllib.parse.quote(keyword)
        ws, tab = self._open(f"https://www.xiaohongshu.com/search_result?keyword={kw}", wait=10.0)
        try:
            time.sleep(2)
            hit = self._ev(ws, f"""(() => {{
              const a = [...document.querySelectorAll('a[href*="{note_id}"]')][0];
              if (!a) return {{found: false}};
              a.click(); return {{found: true}};
            }})()""")
            if not hit.get("found"):
                raise RuntimeError(f"搜索结果中未找到笔记 {note_id}")
            time.sleep(6)
            r = self._ev(ws, """(() => {
              const video = document.querySelector('video');
              const imgs = [...document.querySelectorAll('img')]
                .map(i => i.src).filter(s => s.includes('xhscdn') && !s.includes('avatar')
                  && !s.includes('formula-static') && !s.startsWith('data:'));
              return {hasVideo: !!video,
                      videoSrc: video ? (video.src || (video.querySelector('source')||{}).src || '') : '',
                      images: imgs};
            })()""")
            return {"images": r.get("images", []), "video": r.get("videoSrc", "")}
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def download_media(self, wid: str, url: str, keyword: str = "玄学",
                       dest: str | Path = "output/media") -> Path:
        """下载笔记媒体（视频优先，否则首图）。dest 目录。"""
        import requests as _rq
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        media = self.fetch_media_urls(keyword, wid)
        if media["video"]:
            out = dest / f"xhs-{wid}.mp4"
            src = media["video"]
        elif media["images"]:
            out = dest / f"xhs-{wid}.jpg"
            src = media["images"][0]
        else:
            raise RuntimeError(f"笔记 {wid} 无可下载媒体")
        if out.exists():
            return out
        r = _rq.get(src, timeout=30, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.xiaohongshu.com/"})
        r.raise_for_status()
        out.write_bytes(r.content)
        return out
