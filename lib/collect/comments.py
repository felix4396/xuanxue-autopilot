"""评论抓取器 — 抖音 feed 评论面板 + 微博 m 站评论页。

抖音（逆向结论 2026-10-12）：
- /video/{wid} 直开被重定向回 jingxuan feed 模式，评论面板不默认打开
- 评论按钮 = [data-e2e=feed-comment-icon]（digg 兄弟节点，层级1容器）
- 点击后 comment/list XHR 响应体取不到（CDP getResponseBody 竞态），
  但评论 DOM（[data-e2e=comment-item]）完整：昵称|内容|时间·地点|赞|回复数
- 走 DOM 解析，一次 10 条，滚动加载更多

微博 m 站：
- 评论页 https://m.weibo.cn/statuses/show?id={wid} 内评论走
  /api/comments/hotflow?id={wid}&mid={wid}&max_id_type=0 —— 游客可用（SPA 路径）
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests
import websocket

from lib.collect.ratelimit import RateLimiter

CDP = "http://127.0.0.1:9222"


class CommentScraper:
    def __init__(self, cdp_base: str = CDP):
        self.cdp = cdp_base.rstrip("/")
        self.limiter = RateLimiter(interval=40.0)

    def _ev(self, ws, expr: str, timeout: float = 30.0) -> Any:
        ws.settimeout(timeout)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True}}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m.get("result", {}).get("result", {}).get("value")
        raise TimeoutError("CDP evaluate 超时")

    def _connect(self, url_kw: str) -> tuple[websocket.WebSocket, dict]:
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page" and url_kw in (t.get("url") or "")]
        if not tabs:
            raise RuntimeError(f"无 {url_kw} tab")
        tab = tabs[0]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        ws.settimeout(60)
        return ws, tab

    def _nav_and_reconnect(self, url: str, url_kw: str, wait: float = 12.0):
        """导航 + 无条件重连（SPA context 死亡是已知坑）。"""
        tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page" and url_kw in (t.get("url") or "")]
        if not tabs:
            requests.put(f"{self.cdp}/json/new?{url}", timeout=10)
            time.sleep(wait)
            tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                    if isinstance(t, dict) and t.get("type") == "page" and url_kw in (t.get("url") or "")]
            tab = tabs[0]
        else:
            tab = tabs[0]
            try:
                ws0 = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
                ws0.settimeout(30)
                ws0.send(json.dumps({"id": 1, "method": "Page.navigate",
                                     "params": {"url": url}}))
                try:
                    ws0.recv()
                except Exception:
                    pass
                ws0.close()
            except Exception:
                pass
            time.sleep(wait)
            tabs = [t for t in requests.get(f"{self.cdp}/json/list", timeout=5).json()
                    if isinstance(t, dict) and t.get("type") == "page" and url_kw in (t.get("url") or "")]
            tab = tabs[0]
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
        ws.settimeout(60)
        return ws, tab

    # ---- 抖音评论 ----------------------------------------------------
    DOUYIN_PARSE_JS = """(() => {
      const items = document.querySelectorAll('[data-e2e=comment-item]');
      const out = [];
      for (const it of [...items].slice(0, %(limit)d)) {
        const nick = it.querySelector('a[class*="name"], [class*="nickname"]');
        const t = (it.innerText || '').replace(/\\n+/g, '|');
        // 结构: 昵称|...|内容|时间·地点|赞|分享|回复|展开N条回复
        out.push(t.slice(0, 150));
      }
      return JSON.stringify({n: items.length, list: out});
    })()"""

    def scrape_douyin(self, wid: str, max_comments: int = 30) -> list[dict]:
        """抖音评论：feed 页点开评论面板 → DOM 解析。返回 [{user, content, meta}]。"""
        self.limiter.wait("douyin")
        ws, tab = self._nav_and_reconnect(
            f"https://www.douyin.com/video/{wid}", "douyin", wait=12.0)
        try:
            time.sleep(3)
            # feed 模式下找到激活视频的评论按钮并点击
            click_js = """(() => {
              // 纯视频页
              let btn = document.querySelector('[data-e2e="comment-icon"]');
              if (!btn) {
                // feed 模式：digg 兄弟
                const digg = document.querySelector('[data-e2e="video-player-digg"]');
                if (digg) {
                  let p = digg.parentElement;
                  for (let i = 0; i < 4 && p; i++) {
                    const c = p.querySelector('[data-e2e="feed-comment-icon"]');
                    if (c) { btn = c; break; }
                    p = p.parentElement;
                  }
                }
              }
              if (!btn) return 'no-btn';
              btn.click();
              return 'clicked';
            })()"""
            r = self._ev(ws, click_js)
            if r != "clicked":
                return []
            time.sleep(6)
            comments = []
            seen = set()
            rounds = max(1, max_comments // 10 + 1)
            for _ in range(rounds):
                expr = self.DOUYIN_PARSE_JS % {"limit": 50}
                val = self._ev(ws, expr)
                d = json.loads(val or "{}")
                for raw in d.get("list", []):
                    parsed = self._parse_douyin_comment(raw)
                    if parsed and parsed["content"] and parsed["content"] not in seen:
                        seen.add(parsed["content"])
                        comments.append(parsed)
                if len(comments) >= max_comments:
                    break
                # 滚动评论面板加载更多
                try:
                    self._ev(ws, """(() => {
                      const panel = document.querySelector('[data-e2e=comment-list]') ||
                                    document.querySelector('[class*="comment-list"]');
                      if (panel) panel.scrollTop = panel.scrollHeight;
                      else window.scrollBy(0, 600);
                      return 1;
                    })()""")
                except Exception:
                    pass
                time.sleep(4)
            return comments[:max_comments]
        finally:
            ws.close()

    def _parse_douyin_comment(self, raw: str) -> dict:
        """评论面板 innerText（|分隔）→ 结构化。"""
        parts = [p.strip() for p in raw.split("|") if p.strip() and p.strip() != "..."]
        if not parts:
            return {}
        user = parts[0] if len(parts) > 1 else ""
        # 时间·地点 形如 "1月前·四川" / "6小时前·福建"
        time_idx = -1
        for i, p in enumerate(parts):
            if re.search(r"(\d+秒|\d+分钟|\d+小时|\d+天|月前|周前|年前)·?", p):
                time_idx = i
                break
        content = ""
        meta_time = ""
        location = ""
        likes = 0
        if time_idx > 0:
            content = "".join(parts[1:time_idx])
            m = re.match(r"(.+?)·(.+)", parts[time_idx])
            if m:
                meta_time, location = m.group(1), m.group(2)
            # 时间后第一个纯数字是赞
            for p in parts[time_idx + 1:]:
                p2 = p.replace("赞", "").strip()
                if re.fullmatch(r"[\d.]+[万w]?", p2):
                    likes = self._num(p2)
                    break
        else:
            content = "".join(parts[1:])
        return {"user": user, "content": content, "time": meta_time,
                "location": location, "likes": likes}

    @staticmethod
    def _num(s: str) -> int:
        try:
            s = str(s).replace(",", "").strip()
            if s.endswith("万") or s.endswith("w"):
                return int(float(s[:-1]) * 10000)
            return int(float(s) or 0)
        except (ValueError, TypeError):
            return 0

    # ---- 微博评论 ----------------------------------------------------
    def scrape_weibo(self, wid: str, max_comments: int = 30) -> list[dict]:
        """微博评论：m.weibo.cn/detail/{wid} 页自动发 comments/hotflow XHR。

        （statuses/show?id= 对部分 wid 返回"不合法的请求"，detail 页稳定）
        """
        self.limiter.wait("weibo")
        ws, tab = self._nav_and_reconnect(
            f"https://m.weibo.cn/detail/{wid}", "weibo.cn", wait=6.0)
        try:
            # Network.enable 必须在导航前（hotflow XHR 在页面加载时立即发出）
            ws.send(json.dumps({"id": 50, "method": "Network.enable"}))
            try:
                ws.recv()
            except Exception:
                pass
            try:
                self._ev(ws, f"location.href='https://m.weibo.cn/detail/{wid}'; 1")
            except Exception:
                pass
            req_ids = []
            ws.settimeout(2)
            deadline = time.time() + 15
            while time.time() < deadline:
                try:
                    m = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break
                if m.get("method") == "Network.responseReceived":
                    u = m["params"]["response"]["url"]
                    if "comments/hotflow" in u or "/comments/" in u:
                        req_ids.append(m["params"]["requestId"])
            out = []
            for rid in req_ids:
                try:
                    ws.send(json.dumps({"id": 99, "method": "Network.getResponseBody",
                                        "params": {"requestId": rid}}))
                    mm = json.loads(ws.recv())
                    b = mm.get("result", {}).get("body", "")
                    if not b:
                        continue
                    d = json.loads(b)
                    for c in d.get("data", {}).get("data", []) or []:
                        _t = c.get("text", "")
                        _t = re.sub(r"<[^>]+>", "", _t).strip()
                        out.append({
                            "user": (c.get("user") or {}).get("screen_name", ""),
                            "content": _t,
                            "likes": c.get("like_count", 0),
                            "time": c.get("created_at", ""),
                            "location": (c.get("source") or "").lstrip("来自"),
                        })
                        if len(out) >= max_comments:
                            return out
                except Exception:
                    continue
            return out
        finally:
            ws.close()

    def save(self, platform: str, wid: str, comments: list[dict],
             corpus_dir: str | Path = "output/corpus") -> Path | None:
        """评论写进语料目录 comments.json。"""
        if not comments:
            return None
        d = Path(corpus_dir) / platform / wid
        d.mkdir(parents=True, exist_ok=True)
        out = d / "comments.json"
        out.write_text(json.dumps(comments, ensure_ascii=False, indent=1), encoding="utf-8")
        return out
