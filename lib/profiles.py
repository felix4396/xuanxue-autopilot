"""账号资料采集：昵称/粉丝数/作品数，各平台用户页 state 直读。"""
from __future__ import annotations

import json
import time
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

