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
        """带 xsec_token 直开 explore 页 → 从 __INITIAL_STATE__.note 解析详情+媒体直链。

        抓包逆向最终结论（2026-10-12）：
        - 真实点击链路 = GET /api/sns/h5/v1/note_info（只回 note_type，不含数据）
        - 详情数据是 explore 页 SSR + 前端 hydration 进 __INITIAL_STATE__.note.noteDetailMap
        - 直开 explore **必须带 xsec_token**（缺 token → 空渲染/404 sec_ 风控跳转）
        - 视频 masterUrl 在 noteDetailMap[id].note.video.media.stream.{EF4,...}[0].masterUrl（带 sign）
        - feed API 是移动端/旧版路径，PC web 点击根本不发 feed —— 之前复刻 feed 是错方向
        """
        import urllib.parse as _up
        self.limiter.wait("xhs")
        if not xsec_token:
            raise ValueError("xsec_token 必需：从 search() 结果取（state.feeds[].xsecToken）")
        url = (f"https://www.xiaohongshu.com/explore/{note_id}"
               f"?xsec_token={xsec_token}&xsec_source=pc_search")
        ws, tab = self._open(url, wait=10.0)
        try:
            # 等 SSR/hydration
            r = {}
            for attempt in range(4):
                time.sleep(3)
                r = self._ev(ws, """(() => {
                  const nm = (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.note
                    && window.__INITIAL_STATE__.note.noteDetailMap) || {};
                  for (const [nid, nd] of Object.entries(nm)) {
                    if (!nid.startsWith('""" + note_id[:16] + """')) continue;
                    const n = (nd||{}).note || {};
                    if (!n.noteId) continue;
                    const v = n.video || {};
                    const media = v.media || {};
                    const stream = media.stream || {};
                    let videoUrl = '';
                    for (const k of ['EF4', 'EF6', 'EF5', 'EF7', 'h264', 'av1']) {
                      if (stream[k] && stream[k][0] && stream[k][0].masterUrl) {
                        videoUrl = stream[k][0].masterUrl; break;
                      }
                    }
                    const interact = n.interactInfo || {};
                    return {
                      noteId: n.noteId,
                      type: n.type || '',
                      title: n.title || '',
                      desc: (n.desc || '').slice(0, 500),
                      author: ((n.user||{}).nickname) || '',
                      likes: interact.likedCount || '0',
                      comments: interact.commentCount || '0',
                      collects: interact.collectedCount || '0',
                      images: (n.imageList||[]).map(i => i.urlDefault || ((i.infoList||[{}])[-1].url) || ''),
                      videoUrl,
                      xsecToken: n.xsecToken || ''
                    };
                  }
                  return null;
                })()""")
                if r:
                    break
            if not r:
                raise RuntimeError(f"笔记 {note_id} 详情未渲染（token 失效或被风控）")
            return {
                "wid": r["noteId"], "type": r["type"],
                "title": r["title"], "desc": r["desc"],
                "author": r["author"],
                "likes": self._parse_count(r["likes"]),
                "comments": self._parse_count(r["comments"]),
                "collects": self._parse_count(r["collects"]),
                "images": r["images"], "video": r["videoUrl"],
            }
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def download_media(self, wid: str, url: str, xsec_token: str = "",
                       dest: str | Path = "output/media") -> Path:
        """下载笔记媒体（视频优先，否则首图）。xsec_token 从 search() 结果取。"""
        import requests as _rq
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        d = self.fetch_note_detail(wid, xsec_token)
        hdrs = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.xiaohongshu.com/"}
        if d["video"]:
            out = dest / f"xhs-{wid}.mp4"
            if out.exists():
                return out
            r = _rq.get(d["video"], timeout=120, headers=hdrs)
            r.raise_for_status()
            out.write_bytes(r.content)
            return out
        if d["images"]:
            outs = []
            for i, src in enumerate(d["images"], 1):
                out = dest / (f"xhs-{wid}.jpg" if len(d["images"]) == 1
                              else f"xhs-{wid}-{i}.jpg")
                if out.exists():
                    outs.append(out)
                    continue
                r = _rq.get(src, timeout=60, headers=hdrs)
                r.raise_for_status()
                out.write_bytes(r.content)
                outs.append(out)
            return outs[0]
        raise RuntimeError(f"笔记 {wid} 无可下载媒体")
