"""小红书统一采集：主页 1 次（卡自带 token）+ batch 详情 1 次/条 + 详情页评论区。"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

import requests
import websocket

from .base import UnifiedAccountCollector, Work


class XhsCollector(UnifiedAccountCollector):
    platform = "xhs"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.limiter_interval = 45.0
        self._last_req = 0.0

    def _wait(self):
        d = self.limiter_interval - (time.time() - self._last_req)
        if d > 0:
            time.sleep(d)
        self._last_req = time.time()

    def _cdp_open(self, url: str, wait: float = 9.0):
        before = {t["id"] for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                  if isinstance(t, dict)}
        requests.put(f"{self.cdp}/json/new?{url}", timeout=10)
        time.sleep(wait)
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t["id"] not in before and t.get("type") == "page"]
        tab = tabs[-1]
        try:
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        except Exception:
            # ws 建连失败也要关 tab，否则泄漏
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass
            raise
        ws.settimeout(45)
        return ws, tab

    def _close_tab(self, tab):
        if not tab:
            return
        try:
            requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
        except Exception:
            pass

    def _cleanup_xhs_tabs(self):
        """兜底：清理所有 xhs explore/profile/search 泄漏 tab。"""
        try:
            for t in requests.get(f"{self.cdp}/json/list", timeout=5).json():
                if (isinstance(t, dict) and t.get("type") == "page"
                        and "xiaohongshu.com" in (t.get("url") or "")):
                    self._close_tab(t)
        except Exception:
            pass

    @staticmethod
    def _ev(ws, expr, timeout=30.0):
        ws.settimeout(timeout)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                rr = m.get("result", {}).get("result", {})
                if "exceptionDetails" in rr:
                    raise RuntimeError("eval_exception")
                return rr.get("value")
        raise TimeoutError

    def _resolve(self, name_or_uid: str) -> str:
        extra = self._account_extra(name_or_uid)
        if extra.get("hex_uid"):
            return extra["hex_uid"]
        # 搜索解析（仅首轮）
        self._wait()
        kw = urllib.parse.quote(name_or_uid)
        ws, tab = self._cdp_open(f"https://www.xiaohongshu.com/search_result?keyword={kw}&type=54")
        try:
            time.sleep(10)
            r = self._ev(ws, """(() => {
              const a = document.querySelector('a[href*="/user/profile/"]');
              return a ? (a.getAttribute('href').match(/user\\/profile\\/([0-9a-f]{24})/) || [])[1] : null;
            })()""")
            return r or ""
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def _fetch_profile(self, uid: str, limit: int):
        self._wait()
        ws, tab = self._cdp_open(f"https://www.xiaohongshu.com/user/profile/{uid}")
        try:
            time.sleep(10)
            if "website-login/error" in (self._ev(ws, "location.href") or ""):
                raise RuntimeError("account_blocked_300011")
            # 失效 uid 检测：页面渲染但无昵称 = hex 过期
            prof_r = self._ev(ws, """(() => {
              const st = (window.__INITIAL_STATE__ || {}).user || {};
              const u = st.userInfo || {};
              return JSON.stringify({nickname: u.nickname || '', fans: u.fans || 0});
            })()""")
            _p0 = json.loads(prof_r or "{}")
            if not _p0.get("nickname"):
                raise RuntimeError("hex_uid_stale")
            prof = {"nickname": _p0.get("nickname", ""),
                    "followers": _p0.get("fans", 0) or _p0.get("fansCount", 0)}
            # 主页卡：/user/profile/{hex}/{noteId}?xsec_token=..
            works = []
            for _ in range(8):
                time.sleep(3)
                r = self._ev(ws, """(() => {
                  const out = [];
                  document.querySelectorAll('a[href*="/user/profile/"], a[href*="/explore/"]').forEach(a => {
                    const href = a.getAttribute('href') || '';
                    let m = href.match(/\\/user\\/profile\\/[0-9a-f]{24}\\/([0-9a-f]{24})\\?xsec_token=([^&]+)/)
                            || href.match(/\\/explore\\/([0-9a-f]{24})\\?xsec_token=([^&]+)/);
                    if (m && !out.find(o => o.wid === m[1])) {
                      const card = a.closest('section') || a.parentElement;
                      const txt = (card ? card.innerText : '') || '';
                      out.push({wid: m[1], token: m[2], txt: txt.slice(0, 80)});
                    }
                  });
                  return JSON.stringify(out);
                })()""")
                cards = json.loads(r or "[]")
                for c in cards:
                    if not any(w.wid == c["wid"] for w in works):
                        works.append(Work(wid=c["wid"], title=c.get("txt", "").split("|")[0][:60],
                                          token=c["token"], content_type=""))
                if len(works) >= limit:
                    break
            return prof, works[:limit]
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def _fetch_work(self, w: Work) -> dict:
        if not w.token:
            return {}
        self._wait()
        url = (f"https://www.xiaohongshu.com/explore/{w.wid}"
               f"?xsec_token={w.token}&xsec_source=pc_search")
        ws, tab = self._cdp_open(url, wait=10.0)
        try:
            time.sleep(6)
            r = None
            for _ in range(4):
                time.sleep(3)
                try:
                    r = self._ev(ws, """(() => {
                      const nm = (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.note
                        && window.__INITIAL_STATE__.note.noteDetailMap) || {};
                      for (const [nid, nd] of Object.entries(nm)) {
                        const n = (nd||{}).note || {};
                        if (!n.noteId) continue;
                        const media = (((n.video||{}).media||{}).stream) || {};
                        let videoUrl = '';
                        for (const k of ['EF4','EF6','EF5','EF7']) {
                          if (media[k] && media[k][0] && media[k][0].masterUrl) { videoUrl = media[k][0].masterUrl; break; }
                        }
                        const it = n.interactInfo || {};
                        return JSON.stringify({
                          title: n.title || '', desc: (n.desc||'').slice(0,1000),
                          type: n.type || '',
                          images: (n.imageList||[]).map(i => i.urlDefault || ''),
                          videoUrl,
                          likes: it.likedCount || '0', comments: it.commentCount || '0',
                          shares: it.shareCount || '0', collects: it.collectedCount || '0'
                        });
                      }
                      return null;
                    })()""")
                except Exception:
                    r = None
                if r:
                    break
            if not r:
                return {}
            d = json.loads(r)
            pc = lambda s: (lambda m: int(float(m[1]) * (10000 if m[2] else 1)))(re.match(r"([\d.]+)([万wW]?)", (s or "0").strip().replace(",", "")) or [0, "0", ""])
            return {"title": d.get("title", ""), "desc": d.get("desc", ""),
                    "content_type": "video" if d.get("videoUrl") else ("image_text" if d.get("images") else "text"),
                    "media_urls": ([d["videoUrl"]] if d.get("videoUrl") else d.get("images", [])[:10]),
                    "likes": pc(d.get("likes")), "comments": pc(d.get("comments")),
                    "shares": pc(d.get("shares")), "collected": pc(d.get("collects"))}
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

    def _download(self, w: Work, detail: dict, dest: Path):
        urls = detail.get("media_urls") or []
        if not urls:
            return None
        hdrs = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.xiaohongshu.com/"}
        if detail.get("content_type") == "video":
            out = dest / f"xhs-{w.wid}.mp4"
            if not out.exists():
                resp = requests.get(urls[0], timeout=120, headers=hdrs)
                resp.raise_for_status()
                out.write_bytes(resp.content)
            return out
        # 图集
        outs = []
        for i, src in enumerate(urls, 1):
            o = dest / (f"xhs-{w.wid}.jpg" if len(urls) == 1 else f"xhs-{w.wid}-{i}.jpg")
            if not o.exists():
                resp = requests.get(src, timeout=60, headers=hdrs)
                o.write_bytes(resp.content)
            outs.append(o)
        return outs[0]

    def _fetch_comments(self, w: Work) -> list[dict]:
        # 评论在详情页 __INITIAL_STATE__（_fetch_work 已开过页，这里独立开——评论只抓爆款时才调用）
        if not w.token:
            return []
        self._wait()
        url = f"https://www.xiaohongshu.com/explore/{w.wid}?xsec_token={w.token}&xsec_source=pc_search"
        ws, tab = self._cdp_open(url, wait=10.0)
        try:
            time.sleep(6)
            out = []
            for _ in range(3):
                try:
                    r = self._ev(ws, """(() => {
                      const nm = (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.note
                        && window.__INITIAL_STATE__.note.noteDetailMap) || {};
                      for (const [nid, nd] of Object.entries(nm)) {
                        const cs = (nd||{}).comments || {};
                        const arr = cs.list || [];
                        return JSON.stringify(arr.slice(0, 30).map(c => ({
                          cid: c.id, uid: (c.userInfo||{}).userId || '',
                          nickname: (c.userInfo||{}).nickname || '',
                          content: (c.content||'').slice(0, 500),
                          likes: c.likeCount || 0,
                          parent_cid: c.targetComment ? c.targetComment.id : ''
                        })));
                      }
                      return null;
                    })()""")
                except Exception:
                    r = None
                if r:
                    out = json.loads(r)
                    break
                time.sleep(3)
            return out
        finally:
            ws.close()
            try:
                requests.get(f"{self.cdp}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass
