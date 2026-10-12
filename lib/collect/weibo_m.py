"""微博采集器 — m.weibo.cn SPA + getIndex XHR 直读。

逆向结论（2026-10-12）：
- s.weibo.com（PC 搜索）强制登录；m.weibo.cn 游客态可用（正常浏览路径拿 cookie 后）
- m 站 API /api/container/getIndex 直开会 432（反爬校验 MWeibo cookie+签名），
  正路与 douyinvod 同款：正常 SPA 页面加载时监听 Network，取 getIndex 响应体
- 数据：data.cards[]，card_type=9 是普通微博（mblog: text/user/pics/page_info），
  card_type=11 是推荐/热区；mblog.text 是 HTML 需去标签
- 搜索翻页：page 参数（containerid 不变），滑动加载即 page+1

规格对齐（与 xhs_search 同款）：
- 高斯抖动限速（ratelimit.JITTER_RANGE["weibo"]）
- 拟人化浏览穿插（连续搜索≥4次）
- APM 风控上报拦截（微博 m 站有 /aj/f/entity 或 visitor 风控上报，同 hook 逻辑）
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
import websocket

from lib.collect.ratelimit import RateLimiter

CDP = "http://127.0.0.1:9222"
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def _num_cn(v) -> int:
    """"56.5万" → 565000；int 原样返回。"""
    try:
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).replace(",", "").strip()
        for unit, k in (("万", 10000), ("亿", 100000000)):
            if s.endswith(unit):
                return int(float(s[:-1]) * k)
        return int(float(s) or 0)
    except (ValueError, TypeError):
        return 0


class WeiboMAdapter:
    platform = "weibo"

    def __init__(self, cdp_base: str = CDP):
        self.cdp = cdp_base.rstrip("/")
        self.limiter = RateLimiter(interval=40.0)
        self.limiter.set_risk_callback(self._on_risk_detected)
        self.limiter.register_browse_hook("weibo", self._human_browse_interlude)

    def _on_risk_detected(self, platform: str, detail: str) -> None:
        print(f"  [weibo][风控预警] APM 检测到风控标记: {detail[:80]} → 限速器已自动降速 2.5x")

    def _human_browse_interlude(self) -> None:
        """拟人化穿插：打开一篇已采集微博停留+滚动。"""
        import random as _rd
        import sqlite3
        try:
            con = sqlite3.connect("data/bench.db")
            row = con.execute(
                "SELECT wid FROM works WHERE platform='weibo' ORDER BY RANDOM() LIMIT 1").fetchone()
            con.close()
            if not row:
                return
            dwell = _rd.uniform(5.0, 15.0)
            try:
                ws = self._ensure_tab(f"https://m.weibo.cn/status/{row[0]}")
                time.sleep(dwell)
                self._ev(ws, f"window.scrollTo(0, {int(_rd.uniform(200, 600))}); 1")
                time.sleep(_rd.uniform(1.0, 2.5))
            except Exception:
                pass
            print(f"  [weibo][拟人化] 浏览穿插完成（停留 {dwell:.0f}s）")
        except Exception:
            pass

    # -- 页面/tab 管理 ------------------------------------------------
    def _ev(self, ws, expr: str, timeout: float = 30.0) -> Any:
        ws.settimeout(timeout)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                r = m.get("result", {})
                if "exceptionDetails" in r:
                    raise RuntimeError(json.dumps(r["exceptionDetails"])[:120])
                return r.get("result", {}).get("value")
        raise TimeoutError("CDP evaluate 超时")

    def _ensure_tab(self, url: str, wait: float = 10.0):
        """确保有 weibo.cn tab（复用或新建），返回 ws。"""
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page"
                and "weibo.cn" in (t.get("url") or "")]
        if not tabs:
            requests.put(f"{self.cdp}/json/new?{url}", timeout=10)
            time.sleep(wait)
            tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                    if isinstance(t, dict) and t.get("type") == "page"
                    and "weibo.cn" in (t.get("url") or "")]
            if not tabs:
                raise RuntimeError("weibo.cn tab 打开失败")
        tab = tabs[0]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        ws.settimeout(60)
        return ws, tab

    # -- APM 风控上报拦截 ---------------------------------------------
    RISK_PROBE_JS = """
    (function() {
      if (window.__DSH_RISK_HOOK__) return 'ALREADY';
      window.__DSH_RISK_HOOK__ = true;
      window.__DSH_RISK__ = false;
      window.__DSH_RISK_DETAIL__ = '';
      const patterns = ['isRiskUser', 'risk_level', '"code":-352', '"code":-100', 'st darin'];
      function scan(body) {
        try {
          if (!body || typeof body !== 'string') return;
          for (const p of ['isRiskUser', '"code":-352', '"code":-100']) {
            if (body.includes(p)) {
              window.__DSH_RISK_DETAIL__ = body.slice(0, 200);
              if (p !== 'isRiskUser' || body.includes('true') || body.includes('1'))
                window.__DSH_RISK__ = true;
            }
          }
        } catch (e) {}
      }
      const origFetch = window.fetch;
      window.fetch = function() {
        try { scan(arguments[1] && arguments[1].body); } catch (e) {}
        return origFetch.apply(this, arguments);
      };
      const origSend = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.send = function(body) {
        try { scan(body); } catch (e) {}
        return origSend.apply(this, arguments);
      };
      return 'HOOKED';
    })()
    """

    def _inject_risk_probe(self, ws) -> None:
        try:
            self._ev(ws, self.RISK_PROBE_JS)
        except Exception:
            pass

    def _check_risk_flag(self, ws) -> bool:
        try:
            flag = self._ev(ws, "!!window.__DSH_RISK__")
            if flag:
                detail = ""
                try:
                    detail = self._ev(ws, "window.__DSH_RISK_DETAIL__ || ''") or ""
                except Exception:
                    pass
                self.limiter.mark_risk("weibo", detail)
            return bool(flag)
        except Exception:
            return False

    # -- 核心：SPA 导航 + getIndex XHR 响应直读 -----------------------
    def _search_via_spa(self, keyword: str, page: int = 1,
                        collect_timeout: float = 18.0) -> list[dict]:
        """m.weibo.cn 搜索页 SPA 加载 → 监听 getIndex XHR → 解析 cards。

        游客 cookie 是浏览路径给的（直开 API 432），所以导航到搜索页本身
        就是"正路"，Network 域抓响应体不需要自己拼签名。
        """
        from urllib.parse import quote
        self.limiter.wait("weibo")
        ws, tab = self._ensure_tab(
            f"https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{quote(keyword)}"
            + (f"%26page%3D{page}" if page > 1 else ""))
        # Network.enable 必须在导航前开启（抓 getIndex XHR 的前提）
        ws.send(json.dumps({"id": 50, "method": "Network.enable"}))
        try:
            ws.recv()
        except Exception:
            pass
        self._inject_risk_probe(ws)
        try:
            self._ev(ws, "1")  # 探活
            self._ev(ws, f"location.href='https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{quote(keyword)}"
                         f"{'%26page%3D' + str(page) if page > 1 else ''}'; 1")
        except Exception:
            pass
        time.sleep(4)
        req_ids = []
        deadline = time.time() + collect_timeout
        ws.settimeout(2)
        while time.time() < deadline:
            try:
                m = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if m.get("method") == "Network.responseReceived":
                u = m["params"]["response"]["url"]
                if "getIndex" in u or ("/api/container/" in u and "search" not in u):
                    req_ids.append(m["params"]["requestId"])
        # 统一取 body
        items = []
        for rid in req_ids:
            try:
                ws.send(json.dumps({"id": 99, "method": "Network.getResponseBody",
                                    "params": {"requestId": rid}}))
                mm = json.loads(ws.recv())
                b = mm.get("result", {}).get("body", "")
                if not b:
                    continue
                items.extend(self._parse_cards(b, keyword))
            except Exception:
                continue
        self._check_risk_flag(ws)
        return items

    def _parse_cards(self, body: str, keyword: str) -> list[dict]:
        """getIndex 响应 → 统一作品条目（与 xhs search 返回同构）。"""
        out = []
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            return out
        for c in d.get("data", {}).get("cards", []):
            mb = c.get("mblog") or {}
            if not mb.get("id"):
                # card_type=11 嵌套 cards（热区）——展开一层
                for sub in c.get("cards", []) or []:
                    smb = sub.get("mblog") or {}
                    if smb.get("id"):
                        out.append(self._fmt_mblog(smb, keyword))
                continue
            out.append(self._fmt_mblog(mb, keyword))
        return out

    def _fmt_mblog(self, mb: dict, keyword: str) -> dict:
        text = re.sub(r"<br\s*/?>", "\n", mb.get("text", ""))
        text = _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        pics = [p.get("large", {}).get("url") or p.get("url")
                for p in (mb.get("pics") or []) if p.get("url")]
        video = ""
        pi = mb.get("page_info") or {}
        if pi.get("type") == "video":
            video = ((pi.get("page_url") or ""))
        return {
            "wid": str(mb.get("id")),
            "title": text[:60],
            "desc": text,
            "likes": mb.get("attitudes_count", 0),
            "comments": mb.get("comments_count", 0),
            "shares": mb.get("reposts_count", 0),
            "user": (mb.get("user") or {}).get("screen_name", ""),
            "uid": str((mb.get("user") or {}).get("id", "")),
            "created_at": mb.get("created_at", ""),
            "pics": pics,
            "video_url": video,
            "source_kw": keyword,
            "media_url": f"https://m.weibo.cn/status/{mb.get('id')}",
        }

    # -- 公开 API（harvest / collect 接口）---------------------------
    def name(self) -> str:
        return "weibo"

    def search(self, keyword: str, count: int = 20) -> list[dict]:
        """关键词搜索（含翻页，page 由 count/10 推）。"""
        out = []
        page = 1
        while len(out) < count and page <= 5:
            batch = self._search_via_spa(keyword, page=page)
            if not batch:
                break
            out.extend(batch)
            page += 1
        return out[:count]

    def fetch_user_profile(self, uid: str) -> dict:
        """用户信息：SPA 打开用户页 m.weibo.cn/u/{uid} → /api/container 用户卡 XHR。

        用户页第一个 getIndex 响应的 userInfo + followers_count。
        """
        self.limiter.wait("weibo")
        ws, tab = self._ensure_tab(f"https://m.weibo.cn/u/{uid}")
        ws.send(json.dumps({"id": 50, "method": "Network.enable"}))
        try:
            ws.recv()
        except Exception:
            pass
        self._inject_risk_probe(ws)
        try:
            self._ev(ws, f"location.href='https://m.weibo.cn/u/{uid}'; 1")
        except Exception:
            pass
        time.sleep(4)
        req_ids = []
        deadline = time.time() + 15
        ws.settimeout(2)
        while time.time() < deadline:
            try:
                m = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if m.get("method") == "Network.responseReceived":
                u = m["params"]["response"]["url"]
                if "getIndex" in u or "/api/container/" in u:
                    req_ids.append(m["params"]["requestId"])
        prof = {}
        for rid in req_ids:
            try:
                ws.send(json.dumps({"id": 99, "method": "Network.getResponseBody",
                                    "params": {"requestId": rid}}))
                mm = json.loads(ws.recv())
                b = mm.get("result", {}).get("body", "")
                if not b:
                    continue
                d = json.loads(b)
                ui = (d.get("data") or {}).get("userInfo") or {}
                if ui.get("screen_name"):
                    prof = {
                        "platform": "weibo", "uid": uid,
                        "nickname": ui.get("screen_name", ""),
                        "desc": ui.get("description", ""),
                        "followers": _num_cn(ui.get("followers_count", 0)),
                        "follows": ui.get("follow_count", 0),
                        "works": ui.get("statuses_count", 0),
                        "verified": bool(ui.get("verified")),
                    }
                    break
            except Exception:
                continue
        self._check_risk_flag(ws)
        return prof

    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """用户作品：m.weibo.cn/u/{uid} 用户页 getIndex（containerid=107603uid）分页。"""
        out = []
        page = 1
        while len(out) < limit and page <= 5:
            self.limiter.wait("weibo")
            ws, tab = self._ensure_tab(f"https://m.weibo.cn/u/{uid}")
            ws.send(json.dumps({"id": 50, "method": "Network.enable"}))
            try:
                ws.recv()
            except Exception:
                pass
            try:
                self._ev(ws, f"location.href='https://m.weibo.cn/u/{uid}?page={page}'; 1")
            except Exception:
                pass
            time.sleep(4)
            req_ids = []
            deadline = time.time() + 14
            ws.settimeout(2)
            while time.time() < deadline:
                try:
                    m = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
                if m.get("method") == "Network.responseReceived":
                    u = m["params"]["response"]["url"]
                    if "getIndex" in u or "107603" in u:
                        req_ids.append(m["params"]["requestId"])
            batch = []
            for rid in req_ids:
                try:
                    ws.send(json.dumps({"id": 99, "method": "Network.getResponseBody",
                                        "params": {"requestId": rid}}))
                    mm = json.loads(ws.recv())
                    b = mm.get("result", {}).get("body", "")
                    if b:
                        batch.extend(self._parse_cards(b, ""))
                except Exception:
                    continue
            if not batch:
                break
            out.extend(batch)
            page += 1
        self._check_risk_flag(ws)
        return out[:limit]

    def download_media(self, wid: str, url: str,
                       dest: str | Path = "output/media",
                       pics: list[str] | None = None) -> Path | None:
        """下载微博媒体：图片列表存多图；视频走 page_info 直链或 status 页抓取。"""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        # sinaimg 防盗链实测：Referer=m.weibo.cn 403，weibo.com 200
        hdrs = {"User-Agent": UA_MOBILE, "Referer": "https://weibo.com/"}
        if pics:
            outs = []
            for i, src in enumerate(pics, 1):
                ext = ".jpg"
                if ".gif" in src:
                    ext = ".gif"
                out = dest / (f"weibo-{wid}.jpg" if len(pics) == 1
                              else f"weibo-{wid}-{i}{ext}")
                if not out.exists():
                    r = requests.get(src, timeout=60, headers=hdrs)
                    r.raise_for_status()
                    out.write_bytes(r.content)
                outs.append(out)
            return outs[0]
        if url and "video" in url:
            # page_info.urls 是播放页；真正 mp4 需要 status 页 SPA 抓 mp4 XHR —— 先存占位
            out = dest / f"weibo-{wid}.mp4"
            if url.startswith("http") and (".mp4" in url or "f.video.weibocdn" in url):
                if not out.exists():
                    r = requests.get(url, timeout=120, headers=hdrs)
                    r.raise_for_status()
                    out.write_bytes(r.content)
                return out
        return None
