"""抖音统一采集：user 页 1 次 + 视频页复用 tab（Network 嗅探下载）+ 评论嗅探。"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

import requests
import websocket

from .base import UnifiedAccountCollector, Work

_FFMPEG = "/opt/homebrew/bin/ffmpeg"


class DouyinCollector(UnifiedAccountCollector):
    platform = "douyin"
    _TAB = None  # 全局复用视频 tab

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.limiter_interval = 20.0
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
        extra = self._account_extra(name_or_uid)
        if extra.get("sec_uid"):
            return extra["sec_uid"]
        self._wait()
        kw = urllib.parse.quote(name_or_uid)
        requests.put(f"{self.cdp}/json/new?https://www.douyin.com/jingxuan/search/{kw}", timeout=10)
        time.sleep(12)
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page" and "douyin.com" in (t.get("url") or "")]
        tab = tabs[-1]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        ws.settimeout(45)
        try:
            self._ev(ws, "document.querySelector('[data-e2e=\\'user-link\\']')?.click(); 1")
            time.sleep(8)
            u = self._ev(ws, "location.href")
            m = re.search(r"/user/(MS4wLjABAAAA[0-9A-Za-z_-]+)", u or "")
            return m.group(1) if m else ""
        finally:
            ws.close()

    def _fetch_profile(self, uid: str, limit: int):
        self._wait()
        requests.put(f"{self.cdp}/json/new?https://www.douyin.com/user/{uid}", timeout=10)
        time.sleep(12)
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page" and f"/user/{uid}" in (t.get("url") or "")]
        tab = tabs[-1]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        ws.settimeout(45)
        try:
            time.sleep(4)
            prof = self._ev(ws, """(() => {
              const nm = document.querySelector('[data-e2e="user-info"] [data-e2e="user-info-nickname"]');
              const fs = document.querySelector('[data-e2e="user-info"] [data-e2e="follow-count"] span');
              return JSON.stringify({nickname: nm ? nm.innerText : '', followers: fs ? fs.innerText : '0'});
            })()""")
            prof = json.loads(prof or "{}")
            works = []
            for _ in range(10):
                self._ev(ws, "window.scrollTo(0, document.body.scrollHeight); 1")
                time.sleep(3)
                r = self._ev(ws, """(() => {
                  const out = [];
                  document.querySelectorAll('a[href*="/video/"]').forEach(a => {
                    const m = (a.getAttribute('href')||'').match(/video\\/(\\d+)/);
                    if (m && !out.find(o => o.wid === m[1])) {
                      const card = a.closest('li') || a.parentElement;
                      const txt = (card ? card.innerText : '') || '';
                      out.push({wid: m[1], txt: txt.slice(0, 80)});
                    }
                  });
                  return JSON.stringify(out);
                })()""")
                cards = json.loads(r or "[]")
                works = [Work(wid=c["wid"], title=c.get("txt", "").split("\n")[0][:60],
                              content_type="video") for c in cards]
                if len(works) >= limit:
                    break
            pc = lambda s: (lambda m: int(float(m[1]) * ({'万': 10000, 'w': 10000}.get(m[2], 1))))(
                re.match(r"([\d.]+)([万w]?)", (prof.get("followers") or "0").strip()) or [0, "0", ""])
            prof["followers"] = pc(prof.get("followers"))
            return prof, works[:limit]
        finally:
            ws.close()

    def _get_tab(self):
        if self._TAB:
            try:
                requests.get(f"{self.cdp}/json/list", timeout=5)
                return self._TAB
            except Exception:
                self._TAB = None
        return None

    def _fetch_work(self, w: Work) -> dict:
        return {"content_type": "video"}

    def _download(self, w: Work, detail: dict, dest: Path):
        out = dest / f"douyin-{w.wid}.mp4"
        if out.exists():
            return out
        self._wait()
        # 复用全局 tab，SPA 导航 + Network 嗅探双流（已验证通道）
        if self._TAB is None:
            url = f"https://www.douyin.com/video/{w.wid}"
            requests.put(f"{self.cdp}/json/new?{url}", timeout=10)
            time.sleep(10)
            tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                    if isinstance(t, dict) and t.get("type") == "page" and "douyin.com" in (t.get("url") or "")]
            tab = tabs[-1]
            self._TAB = tab
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        else:
            tab = self._TAB
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
            ws.settimeout(30)
            try:
                ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                    "params": {"expression": f"location.href='https://www.douyin.com/video/{w.wid}'; 1",
                                               "returnByValue": True}}))
            except Exception:
                pass
            time.sleep(8)
            # 重连（context 已死）
            tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                    if isinstance(t, dict) and t.get("type") == "page" and w.wid in (t.get("url") or "")]
            if not tabs:
                return None
            tab = tabs[-1]
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        ws.settimeout(30)
        try:
            ws.send(json.dumps({"id": 9, "method": "Network.enable"}))
            try:
                ws.recv()
            except Exception:
                pass
            vbest, abest, t0 = (None, 0), (None, 0), time.time()
            settle = 0.0
            while time.time() - t0 < 45:
                try:
                    m = json.loads(ws.recv())
                except Exception:
                    break
                if m.get("method") == "Network.responseReceived":
                    resp = m["params"].get("response", {})
                    u = resp.get("url", "")
                    mt = resp.get("mimeType", "")
                    hdrs = resp.get("headers", {})
                    cl = int(hdrs.get("content-length") or hdrs.get("Content-Length") or 0)
                    if not u or "douyinvod" not in u:
                        continue
                    if "media-audio" in u or mt.startswith("audio") or "audio" in mt:
                        if cl > abest[1]:
                            abest, settle = (u, cl), 0.0
                    elif cl > vbest[1]:
                        vbest, settle = (u, cl), 0.0
                if vbest[1] > 200000 and settle == 0:
                    settle = time.time()
                elif vbest[1] > 200000 and time.time() - settle > 8:
                    break
            if not vbest[0]:
                return None
            tmp_v, tmp_a = out.with_suffix(".v.part"), out.with_suffix(".a.part")
            for u, f, sz in [(vbest[0], tmp_v, 8 << 20), (abest[0], tmp_a, 4 << 20) if abest[0] else (None, None, 0)]:
                if not u:
                    continue
                pos = 0
                for retry in range(60):
                    try:
                        rr = requests.get(u, headers={"Range": f"bytes={pos}-", "User-Agent": "Mozilla/5.0",
                                                      "Referer": "https://www.douyin.com/"},
                                          timeout=30, stream=True)
                        for ch in rr.iter_content(sz):
                            with open(f, "ab") as fh:
                                fh.write(ch)
                            pos += len(ch)
                        break
                    except Exception:
                        time.sleep(1)
            if abest[0] and tmp_a.exists():
                subprocess = __import__("subprocess")
                subprocess.run([_FFMPEG, "-y", "-i", str(tmp_v), "-i", str(tmp_a),
                                "-c", "copy", str(out)], capture_output=True, timeout=180)
                tmp_v.unlink(missing_ok=True)
                tmp_a.unlink(missing_ok=True)
            else:
                tmp_v.rename(out)
            return out if out.exists() else None
        finally:
            ws.close()

    def _fetch_comments(self, w: Work) -> list[dict]:
        # 视频页评论区 DOM（复用全局 tab）
        if self._TAB is None:
            return []
        tab = self._TAB
        try:
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
            ws.settimeout(30)
            out = self._ev(ws, """(() => {
              const out = [];
              document.querySelectorAll('[data-e2e=comment-item]').forEach(c => {
                const txt = c.querySelector('[data-e2e=comment-item-content]') || c;
                const nick = c.querySelector('[data-e2e=comment-item-nickname]') || {};
                const like = c.querySelector('[data-e2e=comment-item-like-count]') || {};
                const full = (txt.innerText || '').replace(/\n/g, ' ');
                // 内容首段（去掉"回复/分享/时间"噪音行）
                const lines = full.split(' ').filter(s => s && !/^(回复|分享|展开|收起)$/.test(s.trim()));
                out.push({cid: Math.random().toString(36).slice(2), nickname: (nick.innerText || '').trim(),
                          content: lines.slice(0, 2).join(' ').slice(0, 500),
                          likes: parseInt((like.innerText || '0').replace(/[^\d]/g, '')) || 0});
              });
              return JSON.stringify(out.slice(0, 30));
            })()""")
            ws.close()
            return json.loads(out or "[]")
        except Exception:
            return []
