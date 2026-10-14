"""B站统一采集：space API 主页 1 次 + playurl API 下载 0 页面 + reply API 评论。"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import requests

from .base import UnifiedAccountCollector, Work

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_HDRS = {"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}


class BilibiliCollector(UnifiedAccountCollector):
    platform = "bilibili"

    def _resolve(self, name_or_uid: str) -> str:
        if name_or_uid.isdigit():
            return name_or_uid
        # upuser 搜索 API（免页面）
        u = ("https://api.bilibili.com/x/web-interface/search/type?"
             + urllib.parse.urlencode({"search_type": "bili_user", "keyword": name_or_uid}))
        r = requests.get(u, headers=_HDRS, timeout=15).json()
        for it in (r.get("data", {}) or {}).get("result", [])[:3]:
            if it.get("uname") == name_or_uid or name_or_uid in it.get("uname", ""):
                return str(it["mid"])
        return ""

    def _fetch_profile(self, uid: str, limit: int):
        # space API（免页面）：账号信息 + 视频列表分页；限流时退化为纯列表
        import time as _t
        name, followers = uid, 0
        for attempt in range(3):
            try:
                info = requests.get(f"https://api.bilibili.com/x/space/acc/info?mid={uid}",
                                    headers=_HDRS, timeout=15).json()
                name = ((info.get("data") or {}).get("name")) or uid
                followers = int(((info.get("data") or {}).get("follower")) or 0) if info.get("code") == 0 else 0
                break
            except Exception:
                if attempt == 2:
                    break
                _t.sleep(3 * (attempt + 1))
        works = []
        pn = 1
        while len(works) < limit and pn <= 3:
            u = ("https://api.bilibili.com/x/space/wbi/arc/search?"
                 + urllib.parse.urlencode({"mid": uid, "pn": pn, "ps": 25, "order": "pubdate"}))
            try:
                r = requests.get(u, headers=_HDRS, timeout=15,
                                 cookies={"buvid3": "x"}).json()
            except Exception:
                break
            vlist = ((r.get("data") or {}).get("list") or {}).get("vlist") or []
            if not vlist:
                break
            for v in vlist:
                works.append(Work(
                    wid=v["bvid"], title=v.get("title", ""), desc=v.get("description", ""),
                    content_type="video", likes=0, comments=int(v.get("comment", 0) or 0),
                    shares=0, collected_count=0,
                    ts=float(v.get("created", 0) or 0),
                    url=f"https://www.bilibili.com/video/{v['bvid']}"))
            pn += 1
        return {"nickname": name, "followers": followers}, works[:limit]

    def _fetch_work(self, w: Work) -> dict:
        r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={w.wid}",
                         headers=_HDRS, timeout=15).json()
        d = r.get("data") or {}
        st = d.get("stat") or {}
        return {"likes": st.get("like", 0), "comments": st.get("reply", 0),
                "shares": st.get("share", 0), "collected": st.get("favorite", 0),
                "duration": d.get("duration", 0), "desc": d.get("desc", "")[:500]}

    def _download(self, w: Work, detail: dict, dest: Path):
        out = dest / f"bilibili-{w.wid}.mp4"
        if out.exists():
            return out
        # playurl qn=16 durl（现有已验证通道）
        import websocket  # noqa: F401  保持依赖一致
        u = ("https://api.bilibili.com/x/player/playurl?"
             + urllib.parse.urlencode({"bvid": w.wid, "qn": 16, "type": "mp4"}))
        r = requests.get(u, headers=_HDRS, timeout=15).json()
        durl = ((r.get("data") or {}).get("durl") or [{}])[0].get("url")
        if not durl:
            raise RuntimeError("no durl")
        with requests.get(durl, headers={**_HDRS, "Referer": "https://www.bilibili.com/"},
                          stream=True, timeout=60) as resp:
            resp.raise_for_status()
            tmp = out.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
            tmp.rename(out)
        return out

    def _fetch_comments(self, w: Work) -> list[dict]:
        # 需要 aid：bv→aid
        r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={w.wid}",
                         headers=_HDRS, timeout=15).json()
        aid = (r.get("data") or {}).get("aid")
        if not aid:
            return []
        out = []
        for pn in (1, 2):
            cr = requests.get(
                "https://api.bilibili.com/x/v2/reply?"
                + urllib.parse.urlencode({"type": 1, "oid": aid, "pn": pn, "ps": 30, "sort": 1}),
                headers=_HDRS, timeout=15).json()
            for c in ((cr.get("data") or {}).get("replies") or []):
                out.append(dict(
                    cid=c.get("rpid"), uid=c.get("mid"),
                    nickname=(c.get("member") or {}).get("uname", ""),
                    content=((c.get("content") or {}).get("message", ""))[:500],
                    likes=c.get("like", 0),
                    parent_cid=str(c.get("parent", 0) or 0) if c.get("parent") else "",
                    extra={"ctime": c.get("ctime")}))
            time.sleep(0.5)
        return out
