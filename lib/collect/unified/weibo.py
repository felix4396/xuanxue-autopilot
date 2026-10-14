"""微博统一采集：m.weibo.cn 用户页 API（containerid 分页，免登录）。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from .base import UnifiedAccountCollector, Work

_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"}
_sess = None


_cookies_str: str = ""


def _refresh_cookies(cdp: str = "http://127.0.0.1:9222"):
    """从 CDP Chrome（已登录/已过护栏）借 m.weibo.cn cookie。"""
    global _cookies_str, _sess
    import websocket as _ws
    import requests as _rq
    tab = _rq.put(f"{cdp}/json/new?https://m.weibo.cn", timeout=10)
    time.sleep(10)
    tabs = [t for t in _rq.get(f"{cdp}/json/list", timeout=5).json()
            if isinstance(t, dict) and t.get("type") == "page" and "weibo.cn" in (t.get("url") or "")]
    if not tabs:
        return ""
    tab_id = tabs[-1]["id"]
    ws = _ws.create_connection(tabs[-1]["webSocketDebuggerUrl"], timeout=30)
    ws.settimeout(30)
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.getCookies",
                            "params": {"urls": ["https://m.weibo.cn"]}}))
        m = json.loads(ws.recv())
        cookies = {c["name"]: c["value"] for c in m.get("result", {}).get("cookies", [])}
        _cookies_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    finally:
        ws.close()
        try:
            _rq.get(f"{cdp}/json/close/{tab_id}", timeout=5)
        except Exception:
            pass
    return _cookies_str


def _session(cdp: str = "http://127.0.0.1:9222"):
    """requests.Session 带 Chrome 借来的 cookie（护栏通道）。"""
    global _sess
    if _sess is None:
        _sess = requests.Session()
        _sess.headers.update(_UA)
        ck = _refresh_cookies(cdp)
        if ck:
            _sess.headers["Cookie"] = ck
            _sess.headers["Referer"] = "https://m.weibo.cn"
    return _sess


class WeiboCollector(UnifiedAccountCollector):
    platform = "weibo"

    def _resolve(self, name_or_uid: str) -> str:
        if name_or_uid.isdigit():
            return name_or_uid
        # 昵称搜索 API
        r = _session().get("https://m.weibo.cn/api/container/getIndex?"
                         + requests.models.urlencode({"containerid": f"100103type=3&q={name_or_uid}"}),
                         headers=_UA, timeout=15).json()
        for card in ((r.get("data") or {}).get("cards") or [])[:5]:
            for c in [card] + card.get("cards", []):
                u = ((c.get("mblog") or {}).get("user")) or (c.get("users") or [{}])[0] if isinstance(c.get("users"), list) else (c.get("mblog") or {}).get("user")
                if u and (u.get("screen_name") == name_or_uid):
                    return str(u.get("id"))
        return ""

    def _fetch_profile(self, uid: str, limit: int):
        info = _session().get(f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}",
                            headers=_UA, timeout=15).json()
        ui = (((info.get("data") or {}).get("userInfo")) or {})
        prof = {"nickname": ui.get("screen_name", ""), "followers": ui.get("followers_count", 0)}
        works = []
        for page in (1, 2):
            r = _session().get("https://m.weibo.cn/api/container/getIndex?"
                             + requests.models.urlencode(
                                 {"type": "uid", "value": uid, "containerid": f"107603{uid}", "page": page}),
                             headers=_UA, timeout=15).json()
            for card in (r.get("data") or {}).get("cards") or []:
                mb = card.get("mblog")
                if not mb:
                    continue
                pics = [p.get("large", {}).get("url", "") for p in (mb.get("pics") or [])]
                page_info = mb.get("page_info") or {}
                is_video = bool(page_info.get("media_info"))
                works.append(Work(
                    wid=mb.get("id") or mb.get("bid"), title=(mb.get("text") or "")[:60],
                    desc=re.sub(r"<[^>]+>", "", mb.get("text") or "")[:1000],
                    content_type="video" if is_video else ("image_text" if pics else "text"),
                    likes=int(mb.get("attitudes_count") or 0),
                    comments=int(mb.get("comments_count") or 0),
                    shares=int(mb.get("reposts_count") or 0),
                    ts=float(mb.get("created_at", 0) or 0) if isinstance(mb.get("created_at"), (int, float)) else 0,
                    token=json.dumps(pics[:9])))
                if len(works) >= limit:
                    return prof, works
        return prof, works

    def _fetch_work(self, w: Work) -> dict:
        return {}

    def _download(self, w: Work, detail: dict, dest: Path):
        try:
            pics = json.loads(w.token or "[]")
        except Exception:
            pics = []
        if not pics:
            return None
        outs = []
        for i, src in enumerate(pics, 1):
            o = dest / (f"weibo-{w.wid}.jpg" if len(pics) == 1 else f"weibo-{w.wid}-{i}.jpg")
            if not o.exists():
                resp = _session().get(src, timeout=60, headers=_UA)
                o.write_bytes(resp.content)
            outs.append(o)
        return outs[0] if outs else None

    def _fetch_comments(self, w: Work) -> list[dict]:
        r = _session().get("https://m.weibo.cn/comments/hotflow?"
                         + requests.models.urlencode({"id": w.wid, "mid": w.wid, "max_id_type": 0}),
                         headers=_UA, timeout=15).json()
        out = []
        for c in ((r.get("data") or {}).get("data") or [])[:30]:
            out.append(dict(cid=c.get("id"), uid=(c.get("user") or {}).get("id"),
                            nickname=(c.get("user") or {}).get("screen_name", ""),
                            content=re.sub(r"<[^>]+>", "", c.get("text", ""))[:500],
                            likes=c.get("like_count", 0)))
        return out
