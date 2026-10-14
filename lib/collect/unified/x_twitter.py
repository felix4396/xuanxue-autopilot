"""X(Twitter) 统一采集：主页时间线 1 次 + 媒体直链下载。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests
import websocket

from .base import UnifiedAccountCollector, Work


class XCollector(UnifiedAccountCollector):
    platform = "x"
    _TAB = None

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.limiter_interval = 30.0
        self._last = 0.0

    def _wait(self):
        d = self.limiter_interval - (time.time() - self._last)
        if d > 0:
            time.sleep(d)
        self._last = time.time()

    def _ev(self, ws, expr, timeout=30.0):
        ws.settimeout(timeout)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m.get("result", {}).get("result", {}).get("value")
        raise TimeoutError

    def _resolve(self, name_or_uid: str) -> str:
        return name_or_uid.lstrip("@").split("/")[-1].split("?")[0]

    def _get_tab(self, url: str):
        if self._TAB:
            try:
                ws = websocket.create_connection(self._TAB["webSocketDebuggerUrl"], timeout=30)
                ws.settimeout(30)
                ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                    "params": {"expression": f"location.href='{url}'; 1", "returnByValue": True}}))
                ws.close()
                time.sleep(6)
                return self._TAB
            except Exception:
                self._TAB = None
        requests.put(f"{self.cdp}/json/new?{url}", timeout=10)
        time.sleep(10)
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page" and "x.com" in (t.get("url") or "")]
        if not tabs:
            raise RuntimeError("x tab open failed")
        self._TAB = tabs[-1]
        return self._TAB

    def _fetch_profile(self, uid: str, limit: int):
        self._wait()
        tab = self._get_tab(f"https://x.com/{uid}")
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        ws.settimeout(45)
        try:
            time.sleep(4)
            for _ in range(4):
                self._ev(ws, "window.scrollTo(0, document.body.scrollHeight); 1")
                time.sleep(3)
            r = self._ev(ws, f"""(() => {{
              const arts = [...document.querySelectorAll('article[data-testid="tweet"]')].slice(0, {limit});
              return JSON.stringify(arts.map(a => {{
                const link = (a.querySelector('a[href*="/status/"]')||{{}}).href || '';
                const m = link.match(/status\\/(\\d+)/);
                const txt = ((a.querySelector('[data-testid="tweetText"]')||{{}}).innerText||'').slice(0, 300);
                const likes = (a.querySelector('[data-testid="like"]')||{{}}).innerText || '0';
                const photos = [...a.querySelectorAll('[data-testid="tweetPhoto"] img')].map(i => i.src);
                const video = !!a.querySelector('video');
                return {{wid: m ? m[1] : null, text: txt, likes,
                        media: video ? 'video' : (photos.length ? 'image_text' : 'text'), photos}};
              }}).filter(x => x.wid));
            }})()""")
            items = json.loads(r or "[]")
            prof_r = self._ev(ws, """(() => {
              const n = document.querySelector('[data-testid="UserName"]');
              return JSON.stringify({nickname: n ? n.innerText.replace(/\\n.*/,'') : ''});
            })()""")
            prof = json.loads(prof_r or "{}")
            works = []
            for it in items:
                pc = lambda s: (lambda m: int(float(m[1]) * ({'K': 1000, 'M': 1000000}.get(m[2], 1))))(
                    re.match(r"([\d.]+)([KM]?)", (it.get("likes") or "0").strip()) or [0, "0", ""])
                works.append(Work(wid=it["wid"], title=it["text"][:60], desc=it["text"],
                                  content_type=it["media"], likes=pc(it.get("likes")),
                                  token=json.dumps(it.get("photos", []))))
            return prof, works
        finally:
            ws.close()

    def _fetch_work(self, w: Work) -> dict:
        return {}

    def _download(self, w: Work, detail: dict, dest: Path):
        # X 媒体下载走现有 x_dom 逻辑（需要登录态 tab嗅探），这里实现图片直下
        try:
            photos = json.loads(w.token or "[]")
        except Exception:
            photos = []
        if w.content_type == "video" or not photos:
            return None  # 视频走 ytdlp 附加通道，此处不阻塞
        outs = []
        for i, src in enumerate(photos[:6], 1):
            o = dest / (f"x-{w.wid}.jpg" if len(photos) == 1 else f"x-{w.wid}-{i}.jpg")
            if not o.exists():
                big = re.sub(r"\?format=", "?name=large&format=", src)
                resp = requests.get(big, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
                o.write_bytes(resp.content)
            outs.append(o)
        return outs[0] if outs else None
