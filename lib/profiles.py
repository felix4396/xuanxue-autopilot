"""账号资料采集：昵称/粉丝数/作品数，各平台用户页 state 直读。"""
from __future__ import annotations

import json
import time
import requests
from typing import Any
import websocket


def _ev(ws, expr: str, timeout: float = 30.0):
    ws.settimeout(timeout)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    m = json.loads(ws.recv())
    r = m.get("result", {})
    if "exceptionDetails" in r:
        raise RuntimeError(json.dumps(r["exceptionDetails"])[:120])
    return r.get("result", {}).get("value")


def _num(s) -> int:
    try:
        s = str(s).replace(",", "").strip()
        for unit, k in (("万", 10000), ("w", 10000), ("亿", 100000000),
                        ("K", 1000), ("M", 1000000), ("B", 1000000000)):
            if s.endswith(unit):
                return int(float(s[:-1]) * k)
        return int(float(s) or 0)
    except (ValueError, TypeError):
        return 0


def fetch_xhs_profile(adapter, uid_or_name: str) -> dict:
    """昵称或 user_id。搜索命中用户时页面顶部卡片：昵称/小红书号/粉丝・N/笔记・N。"""
    import re
    import urllib.parse
    adapter.limiter.wait("xhs")
    ws, tab = adapter._open(
        "https://www.xiaohongshu.com/search_result?keyword="
        + urllib.parse.quote(uid_or_name) + "&type=54", wait=12.0)
    try:
        time.sleep(4)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": """(() => {
              const t = document.body.innerText;
              const i = t.indexOf('小红书号');
              return JSON.stringify({seg: i >= 0 ? t.slice(i - 120, i + 100) : '',
                uid: (() => { const a = document.querySelector('a[href*="/user/profile/"]');
                  return a ? (a.getAttribute('href').match(/profile\\/([0-9a-f]{24})/) || [])[1] || '' : ''; })()});
            })()""",
            "returnByValue": True}}))
        m = json.loads(ws.recv())
        val = m.get('result', {}).get('result', {}).get('value')
        if not val:
            return {"platform": "xhs", "uid": uid_or_name, "error": "eval_failed"}
        d = json.loads(val)
        seg = d.get("seg", "")
        if not seg:
            return {"platform": "xhs", "uid": uid_or_name, "error": "no_user_card"}
        # 分隔符是全角点・(U+30FB) 或 ·；Python 端解析更稳
        fans = re.search(r"粉丝[・·\s]*(\d+[万w]?)", seg)
        notes = re.search(r"笔记[・·\s]*(\d+)", seg)
        nick = re.search(r"([^\n]+)\n小红书号", seg)
        return {"platform": "xhs", "uid": d.get("uid") or uid_or_name,
                "nickname": nick.group(1) if nick else uid_or_name,
                "red_id": (re.search(r"小红书号[：:\s]*(\d+)", seg) or [None, ""])[1],
                "followers": _num(fans.group(1)) if fans else 0,
                "works": _num(notes.group(1)) if notes else 0}
    finally:
        ws.close()
        try:
            import requests
            requests.get(f"{adapter.cdp}/json/close/{tab['id']}", timeout=5)
        except Exception:
            pass


def fetch_x_profile(adapter, handle: str) -> dict:
    """handle: 不含@。用户页文本解析（Following/Followers 计数）。"""
    import re
    adapter.limiter.wait("x")
    ws, tab = adapter._open(f"https://x.com/{handle.lstrip('@')}", wait=10.0)
    try:
        time.sleep(4)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": """(() => {
              const t = document.body.innerText;
              const i = t.indexOf('Followers');
              const bio = document.querySelector('[data-testid="UserDescription"]');
              const name = document.querySelector('[data-testid="UserName"]');
              return JSON.stringify({seg: i >= 0 ? t.slice(i - 80, i + 30) : '',
                name: name ? name.innerText.split('\\n')[0] : '',
                bio: bio ? bio.innerText.slice(0, 150) : ''});
            })()""",
            "returnByValue": True}}))
        m = json.loads(ws.recv())
        val = m.get('result', {}).get('result', {}).get('value')
        if not val:
            return {"platform": "x", "uid": handle, "error": "eval_failed"}
        d = json.loads(val)
        seg = d.get("seg", "")
        fm = re.search(r"([\d,.]+[KMB]?)\s*Followers", seg)
        return {"platform": "x", "uid": handle.lstrip("@"),
                "nickname": d.get("name", ""),
                "followers": _num(fm.group(1)) if fm else 0,
                "desc": d.get("bio", "")}
    finally:
        ws.close()
        try:
            import requests
            requests.get(f"{adapter.cdp}/json/close/{tab['id']}", timeout=5)
        except Exception:
            pass


def fetch_douyin_profile(adapter, uid_or_name: str) -> dict:
    """昵称或 sec_uid。内部搜索框 → 结果卡片"获赞/粉丝"文本解析。

    逆向发现：/search 直开 502（签名缺）、用户页直链 SSR 降级（空壳）；
    正路是任意 douyin 页的搜索框输入（内部导航带完整签名），结果卡片
    文本"N万获赞N万粉丝"最可靠。
    """
    import re
    import urllib.parse
    import requests as _rq
    adapter.limiter.wait("douyin")
    kw = uid_or_name
    # 确保有活的 douyin tab 作宿主（_open 依赖已有 tab 做 SPA）
    tabs = [t for t in _rq.get(f"{adapter.cdp}/json/list", timeout=5).json()
            if isinstance(t, dict) and t.get("type") == "page"
            and "douyin.com" in (t.get("url") or "")]
    if not tabs:
        _rq.put(f"{adapter.cdp}/json/new?https://www.douyin.com/jingxuan", timeout=10)
        time.sleep(8)
        tabs = [t for t in _rq.get(f"{adapter.cdp}/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page"
                and "douyin.com" in (t.get("url") or "")]
    # 优先 jingxuan 主页（残留在旧 search 结果的 tab 是 502 空壳）
    tabs.sort(key=lambda t: 0 if "/search/" not in (t.get("url") or "") else 1)
    tab = tabs[0]
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
    ws.settimeout(60)
    time.sleep(2)
    try:
        time.sleep(3)
        # 三步合一：focus+清空+输入+回车（中途分步会因联想框遮挡而失焦）
        ws.send(json.dumps({"id": 7, "method": "Runtime.evaluate", "params": {
            "expression": """(() => {
              const inp = document.querySelector('input[data-e2e="searchbar-input"], input[type="search"], .search-input input');
              if (!inp) return 'NO-INPUT';
              inp.focus();
              document.execCommand('selectAll', false, null);
              document.execCommand('insertText', false, '""" + json.dumps(kw)[1:-1] + """');
              inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
              return 'DONE';
            })()""",
            "returnByValue": True}}))
        m = json.loads(ws.recv())
        v7 = m.get('result', {}).get('result', {}).get('value')
        if v7 != 'DONE':
            return {"platform": "douyin", "uid": uid_or_name, "error": v7 or "no_input"}
        time.sleep(10)
        ws.send(json.dumps({"id": 9, "method": "Runtime.evaluate", "params": {
            "expression": """(() => {
              const t = document.body.innerText;
              const i = t.indexOf('粉丝');
              return JSON.stringify({seg: i >= 0 ? t.slice(Math.max(0, i - 90), i + 40).replace(/[\\n\\r]+/g, '|') : ''});
            })()""",
            "returnByValue": True}}))
        m = json.loads(ws.recv())
        val = m.get('result', {}).get('result', {}).get('value')
        seg = json.loads(val).get("seg", "") if val else ""
        if not seg:
            return {"platform": "douyin", "uid": uid_or_name, "error": "no_result"}
        # 两种卡片格式："N万粉丝" 与 "粉丝：N万"
        fans = re.search(r"粉丝[：:\s]*([\d.]+[万亿]?)", seg) or re.search(r"([\d.]+[万亿]?)粉丝", seg)
        likes = re.search(r"获赞[：:\s]*([\d.]+[万亿]?)", seg) or re.search(r"([\d.]+[万亿]?)获赞", seg)
        douyin_id = re.search(r"抖音号[：:\s]*([\w]+)", seg)
        nick = re.search(r"\|\|([^|]+)\|\|抖音号", seg)
        return {"platform": "douyin", "uid": uid_or_name,
                "nickname": (nick.group(1).strip() if nick else uid_or_name),
                "douyin_id": douyin_id.group(1) if douyin_id else "",
                "likes": _num(likes.group(1)) if likes else 0,
                "followers": _num(fans.group(1)) if fans else 0}
    finally:
        ws.close()



def resolve_xhs_uid(adapter, red_id_or_name: str) -> dict | None:
    """小红书号/昵称 → hex uid（搜索页 DOM 用户卡，精确匹配小红书号）。

    返回 {uid, nickname, red_id, followers, works} 或 None。
    """
    import re as _re
    import urllib.parse as _up
    import time as _t
    adapter.limiter.wait("xhs")
    kw = _up.quote(red_id_or_name)
    ws, tab = adapter._open(
        f"https://www.xiaohongshu.com/search_result?keyword={kw}&type=54")
    try:
        expr = """(() => {
          const out = [];
          document.querySelectorAll('a[href*="user/profile/"]').forEach(a => {
            let el = a, txt = '', depth = 0;
            while (el && depth < 5) {
              if ((el.innerText||'').includes('小红书号')) { txt = el.innerText.slice(0, 120); break; }
              el = el.parentElement; depth++;
            }
            if (txt) out.push({
              uid: (a.getAttribute('href').match(/profile\\/([0-9a-f]{24})/) || [])[1] || '',
              txt: txt});
          });
          return JSON.stringify(out.slice(0, 5));
        })()"""
        cards = []
        for _ in range(12):
            _t.sleep(3)
            try:
                r = adapter._ev(ws, expr)
                cards = json.loads(r or "[]")
            except Exception:
                cards = []
            if cards:
                break
        target = red_id_or_name.replace('*', '').lower()
        for c in cards:
            m = _re.search(r"小红书号：([A-Za-z0-9_]+)", c.get("txt", ""))
            if m and m.group(1).lower() == target.lower():
                txt = c["txt"]
                followers = 0
                fm = _re.search(r"粉丝[・·\s]*([\d.]+[万w]?)", txt)
                if fm:
                    s = fm.group(1)
                    followers = int(float(s[:-1]) * 10000) if s[-1] in "万w" else int(s)
                wm = _re.search(r"笔记[・·\s]*(\d+)", txt)
                nickm = _re.search(r"^([^|\n]+)", txt)
                return {"uid": c["uid"], "nickname": nickm.group(1) if nickm else "",
                        "red_id": m.group(1), "followers": followers,
                        "works": int(wm.group(1)) if wm else 0}
        if cards:
            c = cards[0]
            return {"uid": c.get("uid", ""), "nickname": "", "red_id": "",
                    "followers": 0, "works": 0, "matched": False}
        return None
    finally:
        try:
            ws.close()
            requests.get(f"{adapter.cdp}/json/close/{tab['id']}", timeout=5)
        except Exception:
            pass


def scrape_xhs_user_works(adapter, red_id_or_name: str, limit: int = 25,
                          skip_wids: set | None = None, prof: dict | None = None) -> dict:
    """抓 xhs 用户作品：搜索用户 → 该用户的笔记卡（带 xsec_token）→ 详情。"""
    import time as _t
    import urllib.parse as _up
    if prof is None or not prof.get("uid"):
        prof = resolve_xhs_uid(adapter, red_id_or_name)
    if not prof or not prof.get("uid"):
        return {"profile": None, "works": []}
    uid = prof["uid"]
    adapter.limiter.wait("xhs")
    ws, tab = adapter._open(f"https://www.xiaohongshu.com/user/profile/{uid}")
    try:
        expr = """(() => {
          const out = [];
          document.querySelectorAll('a[href*="/explore/"], a[href*="/user/profile/"]').forEach(a => {
            const href = a.getAttribute('href') || '';
            let m = href.match(/\\/user\\/profile\\/[0-9a-f]{24}\\/([0-9a-f]{24})\\?xsec_token=([^&]+)/)
                    || href.match(/\\/explore\\/([0-9a-f]{24})\\?xsec_token=([^&]+)/);
            if (!m) return;
            if (out.find(o => o.wid === m[1])) return;
            const card = a.closest('section') || a.parentElement;
            const txt = (card ? card.innerText : '') || '';
            out.push({wid: m[1], token: m[2], txt: txt.slice(0, 80).replace(/\\n/g, '|')});
          });
          return JSON.stringify(out);
        })()"""
        cards = []
        target_n = limit
        login_err = False
        for _ in range(14):
            _t.sleep(3)
            try:
                r = adapter._ev(ws, expr)
                cards = json.loads(r or "[]")
            except Exception:
                cards = []
            # 账号级风控检测（300011 → website-login/error 重定向）
            try:
                u = adapter._ev(ws, "location.href")
                if "website-login/error" in (u or ""):
                    login_err = True
                    break
            except Exception:
                pass
            if cards:
                if len(cards) >= target_n:
                    break
                # 未抓满：滚动加载更多（SPA 懒加载）
                try:
                    adapter._ev(ws, "window.scrollBy(0, 2500); 1")
                except Exception:
                    pass
        if login_err:
            return {"profile": prof, "works": [], "account_blocked": True}
        seen = set()
        items = []
        for c in cards:
            if c["wid"] in seen:
                continue
            seen.add(c["wid"])
            if skip_wids and c["wid"] in skip_wids:
                continue
            items.append((c["wid"], c["token"]))
        from lib.collect.xhs_search import XhsSearchAdapter
        if isinstance(adapter, XhsSearchAdapter) and items:
            details = adapter.fetch_note_detail_batch(items[:limit])
            works = []
            for (wid, token), (wid2, media, err) in zip(items[:limit], details):
                works.append({"wid": wid, "xsec_token": token,
                              "media_path": media, "err": err})
            return {"profile": prof, "works": works}
        return {"profile": prof, "works": [{"wid": w, "xsec_token": t} for w, t in items[:limit]]}
    finally:
        try:
            ws.close()
            import requests as _rq
            _rq.get(f"{adapter.cdp}/json/close/{tab['id']}", timeout=5)
        except Exception:
            pass

            _rq.get(f"{adapter.cdp}/json/close/{tab['id']}", timeout=5)
        except Exception:
            pass


# ===================== bilibili =====================

_CDP = "http://127.0.0.1:9222"


def _cdp_open(url: str, wait: float = 12.0):
    """开新 tab 并返回 (ws, tab)。"""
    import requests as _rq
    import websocket as _w
    import time as _t
    before = {t["id"] for t in _rq.get(f"{_CDP}/json/list", timeout=8).json()
              if isinstance(t, dict)}
    _rq.put(f"{_CDP}/json/new?{url}", timeout=10)
    _t.sleep(wait)
    tabs = [t for t in _rq.get(f"{_CDP}/json/list", timeout=8).json()
            if isinstance(t, dict) and t.get("type") == "page"
            and t["id"] not in before]
    if not tabs:
        # 兜底：按 URL 前缀找
        tabs = [t for t in _rq.get(f"{_CDP}/json/list", timeout=8).json()
                if isinstance(t, dict) and t.get("type") == "page"
                and url.split("?")[0] in (t.get("url") or "")]
    tab = tabs[-1]
    ws = _w.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
    ws.settimeout(60)
    return ws, tab


def _cdp_ev(ws, expr: str):
    import json as _j
    ws.send(_j.dumps({"id": 1, "method": "Runtime.evaluate",
                      "params": {"expression": "JSON.stringify(" + expr + ")",
                                 "returnByValue": True}}))
    m = _j.loads(ws.recv())
    res = m.get("result", {})
    if "exceptionDetails" in res:
        raise RuntimeError("eval_exception")
    val = (res.get("result") or {}).get("value")
    return _j.loads(val) if isinstance(val, str) else val


def _cdp_close(ws, tab):
    import requests as _rq
    try:
        ws.close()
    except Exception:
        pass
    try:
        _rq.get(f"{_CDP}/json/close/{tab['id']}", timeout=5)
    except Exception:
        pass


def resolve_bili_mid(name: str) -> dict | None:
    """B站昵称 → mid（upuser 搜索页 DOM）。"""
    import urllib.parse as _up
    kw = _up.quote(name)
    ws, tab = _cdp_open(f"https://search.bilibili.com/upuser?keyword={kw}")
    try:
        expr = """(() => {
          const out = [];
          document.querySelectorAll('a[href*="space.bilibili.com/"]').forEach(a => {
            const m = (a.href||'').match(/space\\.bilibili\\.com\\/(\\d+)/);
            if (!m) return;
            const card = a.closest('div[class*="user"], li, .user-item') || a.parentElement;
            const txt = (card ? card.innerText : a.innerText) || '';
            if (txt.includes('粉丝') && !out.find(o => o.mid === m[1]))
              out.push({mid: m[1], txt: txt.slice(0, 120).replace(/\\n/g, '|')});
          });
          return out.slice(0, 3);
        })()"""
        import time as _t
        cards = []
        for _ in range(8):
            _t.sleep(3)
            try:
                cards = _cdp_ev(ws, expr) or []
            except Exception:
                cards = []
            if cards:
                break
        if not cards:
            return None
        c = cards[0]
        import re as _re
        nm = _re.match(r"^([^+|]+)", c["txt"])
        fm = _re.search(r"([\d.]+万?)粉丝", c["txt"])
        fans = 0
        if fm:
            s = fm.group(1)
            fans = int(float(s[:-1]) * 10000) if s.endswith("万") else int(s)
        vm = _re.search(r"(\d+)个视频", c["txt"])
        return {"mid": c["mid"], "nickname": nm.group(1).strip() if nm else "",
                "followers": fans, "videos": int(vm.group(1)) if vm else 0}
    finally:
        _cdp_close(ws, tab)


def scrape_bili_user_works(mid: str, limit: int = 25) -> list[dict]:
    """B站用户视频列表（space DOM 小卡片）。返回 [{bv, title, date, plays}]。"""
    ws, tab = _cdp_open(f"https://space.bilibili.com/{mid}/video")
    try:
        expr = """(() => {
          const out = [];
          document.querySelectorAll('a[href*="/video/BV"]').forEach(a => {
            const bv = ((a.getAttribute('href')||'').match(/video\\/(BV[0-9A-Za-z]+)/) || [])[1] || '';
            if (!bv || out.find(o => o.bv === bv)) return;
            const card = a.closest('.small-item, .video-card, li') || a.parentElement;
            const txt = (card ? card.innerText : a.innerText) || '';
            out.push({bv, txt: txt.slice(0, 100).replace(/\\n/g, '|')});
          });
          return out.slice(0, """ + str(limit) + """);
        })()"""
        import time as _t
        cards = []
        for _ in range(8):
            _t.sleep(3)
            try:
                cards = _cdp_ev(ws, expr) or []
            except Exception:
                cards = []
            if cards:
                break
        import re as _re
        works = []
        for c in cards:
            # txt: 播放量|时长|标题(可含|)|日期
            parts = c["txt"].split("|")
            plays = parts[0] if parts else ""
            date = parts[-1] if len(parts) >= 2 and _re.match(r"^\d{1,2}月\d{1,2}日$|^\d{4}-\d{2}", parts[-1]) else ""
            dur = parts[1] if len(parts) >= 2 and _re.match(r"^\d{1,2}:\d{2}", parts[1]) else ""
            title_end = len(parts) - 1 if date else len(parts)
            title = "|".join(parts[2:title_end]) if dur else "|".join(parts[1:title_end])
            works.append({"bv": c["bv"], "title": title, "date": date,
                          "plays": plays, "duration": dur})
        return works
    finally:
        _cdp_close(ws, tab)


def download_bili_video(bvid: str, out_path: str) -> Path:
    """B站视频下载：qn=16 durl 正规 CDN 完整 mp4，Range 分段续传（绕 PCDN SSL 断连）。"""
    import requests as _rq
    from pathlib import Path as _P
    out = _P(out_path)
    if out.exists() and out.stat().st_size > 0:
        return out
    s = _rq.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0",
                      "Referer": "https://www.bilibili.com"})
    s.get("https://www.bilibili.com", timeout=10)
    r = s.get("https://api.bilibili.com/x/web-interface/view",
              params={"bvid": bvid}, timeout=15)
    cid = r.json()["data"]["cid"]
    r2 = s.get("https://api.bilibili.com/x/player/playurl",
               params={"bvid": bvid, "cid": cid, "fnval": 1, "qn": 32}, timeout=15)
    dd = r2.json()["data"]
    item = dd["durl"][0]
    url, total = item["url"], item["size"]
    tmp = out.with_suffix(".part")
    pos = tmp.stat().st_size if tmp.exists() else 0
    import time as _t
    for _ in range(60):
        if pos >= total:
            break
        end = min(pos + 4 * 1024 * 1024 - 1, total - 1)
        try:
            rr = s.get(url, headers={"Range": f"bytes={pos}-{end}"}, timeout=60)
            if rr.status_code not in (200, 206):
                _t.sleep(3)
                continue
            with open(tmp, "ab") as f:
                f.write(rr.content)
            pos = tmp.stat().st_size
        except Exception:
            _t.sleep(3)
            continue
    if tmp.stat().st_size >= total:
        tmp.rename(out)
        return out
    raise RuntimeError(f"分段下载不完整 {tmp.stat().st_size}/{total}")


def resolve_douyin_sec_uid(name: str) -> dict | None:
    """douyin 昵称 → sec_uid（搜索页切"用户"tab，点用户卡拿 href）。"""
    import requests as _rq
    import websocket as _w
    import time as _t
    tabs = [t for t in _rq.get("http://127.0.0.1:9222/json/list", timeout=5).json()
            if isinstance(t, dict) and t.get("type") == "page"
            and "douyin.com" in (t.get("url") or "")]
    if not tabs:
        _rq.put("http://127.0.0.1:9222/json/new?https://www.douyin.com/jingxuan", timeout=10)
        _t.sleep(10)
        tabs = [t for t in _rq.get("http://127.0.0.1:9222/json/list", timeout=5).json()
                if isinstance(t, dict) and t.get("type") == "page"
                and "douyin.com" in (t.get("url") or "")]
    tabs.sort(key=lambda t: 0 if "/search/" not in (t.get("url") or "") else 1)
    tab = tabs[0]
    ws = _w.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
    ws.settimeout(60)
    try:
        _t.sleep(3)
        ws.send(json.dumps({"id": 7, "method": "Runtime.evaluate", "params": {
            "expression": """(() => {
              const inp = document.querySelector('input[data-e2e="searchbar-input"], input[type="search"], .search-input input');
              if (!inp) return 'NO-INPUT';
              inp.focus();
              document.execCommand('selectAll', false, null);
              document.execCommand('insertText', false, '""" + json.dumps(name)[1:-1] + """');
              inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
              return 'DONE';
            })()""",
            "returnByValue": True}}))
        v = json.loads(ws.recv()).get("result", {}).get("result", {}).get("value")
        if v != "DONE":
            return None
        _t.sleep(10)
        # 切"用户"tab
        ws.send(json.dumps({"id": 9, "method": "Runtime.evaluate", "params": {
            "expression": """(() => {
              const cands = [...document.querySelectorAll('a, span, div')].filter(e =>
                (e.innerText || '').trim() === '用户' && e.children.length === 0);
              if (!cands.length) return 'NO-TAB';
              cands[0].click();
              return 'OK';
            })()""",
            "returnByValue": True}}))
        v2 = json.loads(ws.recv()).get("result", {}).get("result", {}).get("value")
        if v2 != "OK":
            return None
        _t.sleep(8)
        # 拿用户卡 href + 文本
        ws.send(json.dumps({"id": 11, "method": "Runtime.evaluate", "params": {
            "expression": """(() => {
              const cards = [...document.querySelectorAll('a')].filter(a =>
                (a.innerText || '').includes('粉丝') && (a.innerText || '').includes('获赞'));
              if (!cards.length) return '';
              return JSON.stringify({href: cards[0].getAttribute('href') || '',
                                     txt: (cards[0].innerText || '').slice(0, 100)});
            })()""",
            "returnByValue": True}}))
        val = json.loads(ws.recv()).get("result", {}).get("result", {}).get("value")
        if not val:
            return None
        d = json.loads(val)
        import re as _re
        m = _re.search(r"/user/(MS4w[\w-]+)", d["href"])
        if not m:
            return None
        txt = d["txt"].replace("\n", "|")
        fans = _re.search(r"([\d.]+[万亿]?)粉丝", txt)
        likes = _re.search(r"([\d.]+[万亿]?)获赞", txt)
        return {"sec_uid": m.group(1),
                "nickname": name, "followers": _num(fans.group(1)) if fans else 0,
                "likes": _num(likes.group(1)) if likes else 0}
    finally:
        ws.close()


def scrape_douyin_user_works(sec_uid: str, limit: int = 25) -> list[dict]:
    """douyin user 页作品卡（滚动加载）。返回 [{id, title, plays}]。"""
    import requests as _rq
    import websocket as _w
    import time as _t
    url = f"https://www.douyin.com/user/{sec_uid}"
    _rq.put(f"http://127.0.0.1:9222/json/new?{url}", timeout=10)
    _t.sleep(15)
    tabs = [t for t in _rq.get("http://127.0.0.1:9222/json/list", timeout=8).json()
            if isinstance(t, dict) and t.get("type") == "page"
            and f"/user/{sec_uid[:20]}" in (t.get("url") or "")]
    if not tabs:
        return []
    tab = tabs[-1]
    ws = _w.create_connection(tab["webSocketDebuggerUrl"], timeout=60)
    ws.settimeout(60)
    try:
        expr = """(() => {
          const out = [];
          document.querySelectorAll('a[href*="/video/"]').forEach(a => {
            const m = (a.getAttribute('href')||'').match(/video\\/(\\d+)/);
            if (m && !out.find(o => o.id === m[1])) {
              const card = a.closest('li') || a.parentElement;
              out.push({id: m[1], txt: ((card ? card.innerText : '') || '').slice(0, 90)});
            }
          });
          return JSON.stringify(out.slice(0, 60));
        })()"""
        cards = []
        for _ in range(16):
            _t.sleep(3)
            try:
                ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                    "expression": expr, "returnByValue": True}}))
                while True:
                    m = json.loads(ws.recv())
                    if m.get("id") == 1:
                        break
                val = m.get("result", {}).get("result", {}).get("value")
                if isinstance(val, str) and val.startswith("["):
                    cards = json.loads(val)
            except Exception:
                cards = cards
            if cards and len(cards) >= limit:
                break
            if cards:
                try:
                    ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate", "params": {
                        "expression": "window.scrollBy(0, 3000); 1", "returnByValue": True}}))
                    while True:
                        m2 = json.loads(ws.recv())
                        if m2.get("id") == 2:
                            break
                except Exception:
                    pass
        import re as _re
        works = []
        for c in cards[:limit]:
            txt = c["txt"].replace("\n", "|")
            plays = ""
            pm = _re.search(r"\|([\d.]+万?)\|", txt)
            if pm:
                plays = pm.group(1)
            title = "|".join(txt.split("|")[2:4])[:60]
            works.append({"id": c["id"], "title": title, "plays": plays})
        return works
    finally:
        try:
            ws.close()
            _rq.get(f"http://127.0.0.1:9222/json/close/{tab['id']}", timeout=5)
        except Exception:
            pass


def download_douyin_video(video_id: str, out_path: str) -> Path:
    """douyin 视频下载：Chrome 开视频页，Network 监听抓 douyinvod 流直链（带签名+Referer 可直下）。"""
    import requests as _rq
    import websocket as _w
    import time as _t
    from pathlib import Path as _P
    out = _P(out_path)
    if out.exists() and out.stat().st_size > 10000:
        return out
    # 复用全局 tab（同一轮内多条视频共用一个页面，避免反复开关 tab）
    global _DY_TAB
    tab = None
    if "_DY_TAB" in globals() and _DY_TAB:
        tabs = [t for t in _rq.get("http://127.0.0.1:9222/json/list", timeout=8).json()
                if isinstance(t, dict) and t.get("type") == "page"
                and t.get("id") == _DY_TAB]
        if tabs:
            tab = tabs[0]
    if tab is None:
        _rq.put(f"http://127.0.0.1:9222/json/new?https://www.douyin.com/video/{video_id}", timeout=10)
        _t.sleep(15)
        tabs = [t for t in _rq.get("http://127.0.0.1:9222/json/list", timeout=8).json()
                if isinstance(t, dict) and t.get("type") == "page"
                and f"/video/{video_id}" in (t.get("url") or "")]
        if not tabs:
            raise RuntimeError("douyin 视频页未打开")
        tab = tabs[-1]
        _DY_TAB = tab["id"]
    ws = _w.create_connection(tab["webSocketDebuggerUrl"], timeout=90)
    ws.settimeout(60)
    url = ""
    try:
        ws.send(json.dumps({"id": 0, "method": "Network.enable"}))
        ws.recv()
        # SPA 导航（复用 tab）或首次 reload
        if f"/video/{video_id}" not in (tab.get("url") or ""):
            ws.send(json.dumps({"id": 9, "method": "Runtime.evaluate", "params": {
                "expression": f"location.href='https://www.douyin.com/video/{video_id}'; 1",
                "returnByValue": True}}))
            ws.recv()
            _t.sleep(12)
        else:
            ws.send(json.dumps({"id": 1, "method": "Page.reload"}))
            ws.recv()
        vbest, abest, t0 = (None, 0), (None, 0), _t.time()
        _settle = 0  # 大流捕获后观察 6s 无更大流即提前退出
        while _t.time() - t0 < 45:
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
                if not u or "image" in u or ("douyinvod" not in u and "bytecdn" not in u):
                    continue
                is_audio = ("media-audio" in u or mt.startswith("audio") or "audio" in mt)
                if is_audio:
                    if cl > abest[1]:
                        abest = (u, cl)
                        _settle = 0
                else:
                    if cl > vbest[1]:
                        vbest = (u, cl)
                        _settle = 0
            if vbest[1] > 200000 and _settle == 0:
                _settle = _t.time()  # 记录捕获时间
            elif vbest[1] > 200000 and _t.time() - _settle > 8:
                break
        url = vbest[0]
        aurl = abest[0]
    finally:
        try:
            ws.close()
        except Exception:
            pass
        # 复用模式不关 tab（下条视频 SPA 导航复用）
    if not url:
        raise RuntimeError("未捕获 douyinvod 流")
    # Range 探真实总长（分段响应的 content-length 不可靠）
    s = _rq.Session()
    s.headers.update({"Referer": "https://www.douyin.com/", "User-Agent": "Mozilla/5.0"})
    rh = s.get(url, headers={"Range": "bytes=0-1"}, timeout=30)
    cr = rh.headers.get("Content-Range", "")
    total = int(cr.split("/")[-1]) if "/" in cr else int(rh.headers.get("Content-Length", 0))
    tmp = out.with_suffix(".part")
    pos = tmp.stat().st_size if tmp.exists() else 0
    import time as _t2
    for _ in range(60):
        if pos >= total:
            break
        end = min(pos + 8 * 1024 * 1024 - 1, total - 1)
        try:
            rr = s.get(url, headers={"Range": f"bytes={pos}-{end}"}, timeout=120)
            if rr.status_code in (200, 206):
                with open(tmp, "ab") as f:
                    f.write(rr.content)
                pos = tmp.stat().st_size
        except Exception:
            _t2.sleep(3)
            continue
    if not (tmp.exists() and tmp.stat().st_size >= total):
        raise RuntimeError(f"douyin 下载不完整 {tmp.stat().st_size if tmp.exists() else 0}/{total}")
    tmp.rename(out)
    # 有独立音频流 → 下载音频 + ffmpeg mux 成含音轨 mp4
    if aurl:
        atmp = out.with_suffix(".apart")
        rh2 = s.get(aurl, headers={"Range": "bytes=0-1"}, timeout=30)
        cr2 = rh2.headers.get("Content-Range", "")
        atotal = int(cr2.split("/")[-1]) if "/" in cr2 else int(rh2.headers.get("Content-Length", 0))
        apos = atmp.stat().st_size if atmp.exists() else 0
        for _ in range(40):
            if apos >= atotal:
                break
            aend = min(apos + 4 * 1024 * 1024 - 1, atotal - 1)
            try:
                rr2 = s.get(aurl, headers={"Range": f"bytes={apos}-{aend}"}, timeout=120)
                if rr2.status_code in (200, 206):
                    with open(atmp, "ab") as f:
                        f.write(rr2.content)
                    apos = atmp.stat().st_size
            except Exception:
                _t2.sleep(3)
                continue
        if atmp.exists() and atmp.stat().st_size >= atotal:
            import subprocess as _sp
            muxed = out.with_suffix(".mux.mp4")
            _FFMPEG = "/opt/homebrew/bin/ffmpeg"
            r3 = _sp.run([_FFMPEG, "-y", "-i", str(out), "-i", str(atmp),
                          "-c", "copy", str(muxed)],
                         capture_output=True, timeout=300)
            atmp.unlink(missing_ok=True)
            if r3.returncode == 0 and muxed.exists():
                out.unlink()
                muxed.rename(out)
        else:
            atmp.unlink(missing_ok=True)
    return out
