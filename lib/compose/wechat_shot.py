"""微信聊天截图生成：账号资产文案 → 对话式内容 → 微信聊天截图 PNG。

用途：塔罗/玄学账号常见的"塔罗师和客户的聊天实录"型内容。
流程：
  1. 用账号资产（画像+内容资产+persona）生成一段对话（塔罗师 vs 客户）
  2. 渲染微信聊天样式 HTML（复刻 iOS 微信气泡排版）
  3. Chromium headless 截图 → output/shots/{ts}.png
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..llm import chat_json
from .generate import load_persona
from ..analyze.profile import get_profile
from ..analyze.content_assets import get_account_assets

ROOT = Path(__file__).resolve().parent.parent.parent
SHOTS_DIR = ROOT / "output" / "shots"

CHAT_SYSTEM = (
    "你是「{creator}」（{role_creator}）和客户的微信聊天编剧。"
    "根据账号画像写出一段真实感强的微信对话：客户提问，{creator}以该账号的"
    "风格解读（用账号的口头禅、话术、牌面语言）。对话要口语、有停顿感、"
    "客户消息短、塔罗师消息可稍长但别大段。输出严格 JSON。"
)
CHAT_PROMPT = """{gen_brief}

【塔罗师风格参考】
口头禅：{phrases}
金句风格：{golden}
话题：{topic}

写出 {n_round} 轮对话（客户问 → 塔罗师答），塔罗师说话要用上面的风格。
输出 JSON：
- scenario: 场景一句话（如"客户问复合"）
- messages: 数组，每条 {{"role": "client"|"tarot", "text": "..."}}
  （client 先开口；共 {n_msg} 条左右；客户用「宝子」等称呼塔罗师）"""

HTML_TMPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;
       -webkit-font-smoothing:antialiased; }}
  html,body {{ width:402px; }}
  body {{ background:#f7f7f7; }}

  /* ── 状态栏 ── */
  .statusbar {{ height:54px; background:#fff; display:flex; align-items:flex-end;
                justify-content:space-between; padding:0 28px 8px; }}
  .statusbar .time {{ font-size:17px; font-weight:600; color:#000; display:flex; align-items:center; gap:6px; }}
  .statusbar .time .mute {{ font-size:13px; color:#000; }}
  .statusbar .icons {{ display:flex; align-items:center; gap:7px; }}
  .sig {{ display:flex; align-items:flex-end; gap:2px; }}
  .sig i {{ width:3px; background:#000; border-radius:1px; display:block; }}
  .sig i:nth-child(1) {{ height:4px; }} .sig i:nth-child(2) {{ height:6px; }}
  .sig i:nth-child(3) {{ height:8px; }} .sig i:nth-child(4) {{ height:11px; }}
  .wifi {{ width:17px; height:13px; }}
  .batt {{ width:27px; height:13px; border:1px solid rgba(0,0,0,.4); border-radius:4px; padding:1.5px; position:relative; }}
  .batt::after {{ content:""; position:absolute; right:-3.5px; top:3.5px; width:2px; height:5px;
                  background:rgba(0,0,0,.4); border-radius:0 2px 2px 0; }}
  .batt i {{ display:block; height:100%; width:97%; background:#34c759; border-radius:2px; }}

  /* ── 导航栏 ── */
  .nav {{ height:44px; background:#fff; border-bottom:.5px solid #e5e5e5;
          display:flex; align-items:center; padding:0 12px; position:relative; }}
  .nav .back {{ display:flex; align-items:center; gap:8px; }}
  .nav .chevron {{ width:11px; height:19px; border-left:2.5px solid #000; border-bottom:2.5px solid #000;
                   transform:rotate(45deg); margin-left:6px; }}
  .nav .unread {{ min-width:22px; height:22px; background:#e0e0e0; border-radius:11px; color:#666;
                  font-size:13.5px; display:flex; align-items:center; justify-content:center; padding:0 6px; }}
  .nav .title {{ position:absolute; left:50%; transform:translateX(-50%);
                 font-size:17.5px; font-weight:600; color:#000; }}
  .nav .more {{ margin-left:auto; color:#000; font-size:19px; font-weight:700; letter-spacing:1px; padding-right:6px; }}

  /* ── 聊天区 ── */
  .chat {{ background:#f7f7f7; padding:0 14px; min-height:200px; }}
  .time-sep {{ text-align:center; color:#9b9b9b; font-size:13px; margin:18px 0 6px; }}
  .row {{ display:flex; margin:11px 0; align-items:flex-start; }}
  .row.me {{ flex-direction:row-reverse; }}
  .avatar {{ width:48px; height:48px; border-radius:6px; flex-shrink:0; overflow:hidden;
             display:flex; align-items:center; justify-content:center; color:#fff; font-size:20px; font-weight:500; }}
  .row.client .avatar {{ background:#b48c9e; }}
  .row.tarot .avatar {{ background:#7ecfd4; }}
  .bubble {{ max-width:246px; padding:10px 12px; border-radius:6px; font-size:17px;
             line-height:1.42; color:#000; position:relative; word-break:break-word; }}
  .row.client .bubble {{ background:#fff; margin-left:12px; }}
  .row.tarot .bubble {{ background:#fff; margin-left:12px; }}
  .row.me .bubble {{ background:#95ec69; margin-right:12px; }}
  .bubble::before {{ content:""; position:absolute; top:15px; border:7px solid transparent; }}
  .row.client .bubble::before {{ left:-13px; border-right-color:#fff; }}
  .row.tarot .bubble::before {{ left:-13px; border-right-color:#fff; }}
  .row.me .bubble::before {{ right:-13px; border-left-color:#95ec69; }}
  .bubble.img {{ padding:0; background:transparent; overflow:hidden; border-radius:4px; }}
  .bubble.img::before {{ display:none; }}
  .bubble.img img {{ display:block; max-width:170px; border-radius:4px; }}

  /* ── 输入栏 ── */
  .inputbar {{ width:402px; height:84px; background:#f7f7f7;
               border-top:.5px solid #e0e0e0; display:flex; align-items:center; padding:0 16px; gap:18px; }}
  .inputbar .voice {{ width:30px; height:30px; }}
  .inputbar .field {{ flex:1; height:38px; background:#fff; border:.5px solid #dcdcdc; border-radius:5px; }}
  .inputbar .emoji, .inputbar .plus {{ width:30px; height:30px; }}
  .pad {{ height:84px; }}
  svg {{ display:block; }}
</style></head>
<body>
  <div class="statusbar">
    <div class="time">{status_time} <span class="mute">🔔</span></div>
    <div class="icons">
      <div class="sig"><i></i><i></i><i></i><i></i></div>
      <svg class="wifi" viewBox="0 0 17 13"><path d="M8.5 11.5 L5.5 8.7 A4.4 4.4 0 0 1 11.5 8.7 Z M3.4 6.6 A7.4 7.4 0 0 1 13.6 6.6 L12 8.2 A5.2 5.2 0 0 0 5 8.2 Z M1.3 4.4 A10.4 10.4 0 0 1 15.7 4.4 L14.1 6 A8.2 8.2 0 0 0 2.9 6 Z" fill="#000"/></svg>
      <div class="batt"><i></i></div>
    </div>
  </div>
  <div class="nav">
    <div class="back"><div class="chevron"></div><div class="unread">{unread}</div></div>
    <div class="title">{title}</div>
    <div class="more">···</div>
  </div>
  <div class="chat">
    {rows}
  </div>
  <div class="pad"></div>
  <div class="inputbar">
    <svg class="voice" viewBox="0 0 30 30"><circle cx="15" cy="15" r="13.5" fill="none" stroke="#000" stroke-width="1.6"/><circle cx="15" cy="15" r="3.2" fill="#000"/><path d="M10 11 a6.5 6.5 0 0 0 0 8 M20 11 a6.5 6.5 0 0 1 0 8" fill="none" stroke="#000" stroke-width="1.6" stroke-linecap="round"/></svg>
    <div class="field"></div>
    <svg class="emoji" viewBox="0 0 30 30"><circle cx="15" cy="15" r="13.5" fill="none" stroke="#000" stroke-width="1.6"/><circle cx="10.5" cy="12" r="1.8" fill="#000"/><circle cx="19.5" cy="12" r="1.8" fill="#000"/><path d="M9 18 a7.5 7.5 0 0 0 12 0" fill="none" stroke="#000" stroke-width="1.6" stroke-linecap="round"/></svg>
    <svg class="plus" viewBox="0 0 30 30"><circle cx="15" cy="15" r="13.5" fill="none" stroke="#000" stroke-width="1.6"/><path d="M15 9 v12 M9 15 h12" stroke="#000" stroke-width="1.6" stroke-linecap="round"/></svg>
  </div>
</body></html>"""


def _initial(name: str) -> str:
    return (name or "?")[0]


def gen_chat(platform: str, uid: str, topic: str = "",
             n_round: int = 5, creator_role: str = "me") -> dict:
    """用账号资产生成对话文案。creator_role: 塔罗师在对话里的位置 me|tarot。"""
    profile = get_profile(platform, uid)
    assets = get_account_assets(platform, uid)
    if not profile:
        return {"error": f"画像缺失: {platform}:{uid}"}
    persona = load_persona()
    creator = persona.get("name") or "(未填 persona.name)"
    phrases = (assets.get("phrase_templates") or [])[:4]
    golden = (assets.get("golden_lines_pool") or [])[:4]
    if not topic:
        topics = assets.get("topic_inventory") or []
        topic = topics[0].get("主题名", "") if topics and isinstance(topics[0], dict) else "塔罗占卜"
    chat = chat_json(
        CHAT_PROMPT.format(
            gen_brief=profile.get("gen_brief", ""),
            phrases="；".join(str(p.get("句式", p)) if isinstance(p, dict) else str(p) for p in phrases)[:800],
            golden="；".join(str(g.get("text", g)) if isinstance(g, dict) else str(g) for g in golden)[:600],
            topic=topic, n_round=n_round, n_msg=n_round * 2),
        system=CHAT_SYSTEM.format(creator=creator, role_creator="塔罗师"),
        max_tokens=2500, temperature=0.85)
    if not chat or not chat.get("messages"):
        raise RuntimeError("微信对话文案 LLM 生成失败")
    return chat


def render_shot(chat: dict, title: str = "", out_dir: Path | None = None) -> Path:
    """对话 dict → 微信截图 PNG。title 默认用 scenario。"""
    import html as _h
    persona = load_persona()
    creator = persona.get("name") or "塔罗师"
    rows = []
    last_role = None
    for m in chat["messages"]:
        role = m["role"]
        text = _h.escape(str(m["text"]))
        # 视角：塔罗师放右边绿泡（截图发布者视角=塔罗师本人）
        side = "me" if role == "tarot" else "client"
        # 每 6 条消息后插时间分隔（模拟真实聊天的时间跳跃）
        if len(rows) and len(rows) % 6 == 0:
            hh = 16 + len(rows) // 12
            hh12 = hh - 12 if hh > 12 else hh
            rows.append(f'<div class="time-sep">星期五 下午 {hh12}:{(len(rows)*7) % 60:02d}</div>')
        last_role = role
        rows.append(
            f'<div class="row {side}">'
            f'<div class="avatar">{_initial(creator) if side == "me" else "客"}</div>'
            f'<div class="bubble">{text}</div></div>')
    status_time = time.strftime("%-I:%M") if hasattr(time, "_x") else time.strftime("%H:%M").lstrip("0")
    title = title or chat.get("scenario", "塔罗咨询")
    title = title[:8] + "…" if len(title) > 9 else title
    html = HTML_TMPL.format(
        title=title, unread="11",
        status_time=status_time, rows="\n".join(rows))
    out_dir = out_dir or SHOTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"chat-{int(time.time())}.html"
    html_path.write_text(html)
    # headless chromium 截图
    from playwright.sync_api import sync_playwright
    png_path = html_path.with_suffix(".png")
    with sync_playwright() as p:
        import os
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        b = p.chromium.launch(executable_path=chrome if os.path.exists(chrome) else None)
        page = b.new_page(viewport={"width": 402, "height": 874}, device_scale_factor=3)
        page.goto(f"file://{html_path}")
        page.wait_for_timeout(300)
        page.screenshot(path=str(png_path), full_page=True)
        b.close()
    return png_path


def generate_shot(platform: str, uid: str, topic: str = "", n_round: int = 5) -> Path:
    """一键：资产文案 → 微信截图 PNG。"""
    chat = gen_chat(platform, uid, topic, n_round)
    return render_shot(chat)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="微信聊天截图生成")
    ap.add_argument("--platform", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--topic", default="")
    ap.add_argument("--rounds", type=int, default=5)
    a = ap.parse_args()
    p = generate_shot(a.platform, a.uid, a.topic, a.rounds)
    print("截图:", p)
