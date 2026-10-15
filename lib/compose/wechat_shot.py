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
  * {{ margin:0; padding:0; box-sizing:border-box; font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif; }}
  body {{ width:390px; }}
  .nav {{ height:88px; padding-top:44px; background:#ededed; border-bottom:1px solid #d6d6d6;
         text-align:center; position:relative; }}
  .nav .title {{ font-size:17px; font-weight:600; color:#000; }}
  .nav .back {{ position:absolute; left:12px; top:52px; width:12px; height:20px;
                border-left:2.5px solid #000; border-bottom:2.5px solid #000;
                transform:rotate(45deg); }}
  .chat {{ background:#f5f5f5; padding:10px 12px 20px; }}
  .row {{ display:flex; margin:9px 0; align-items:flex-start; }}
  .row.me {{ flex-direction:row-reverse; }}
  .avatar {{ width:40px; height:40px; border-radius:5px; flex-shrink:0;
             display:flex; align-items:center; justify-content:center;
             color:#fff; font-size:16px; font-weight:600; }}
  .row.client .avatar {{ background:#5b7bd5; }}
  .row.tarot .avatar {{ background:#c58f5a; }}
  .row.me .avatar {{ background:#7aa87a; }}
  .bubble {{ max-width:240px; padding:9px 12px; border-radius:5px; font-size:15.5px;
             line-height:1.45; color:#000; position:relative; word-break:break-word; }}
  .row.client .bubble {{ background:#fff; margin-left:10px; }}
  .row.tarot .bubble {{ background:#fff; margin-left:10px; }}
  .row.me .bubble {{ background:#95ec69; margin-right:10px; }}
  .bubble::before {{ content:""; position:absolute; top:13px; border:6px solid transparent; }}
  .row.client .bubble::before {{ left:-11px; border-right-color:#fff; }}
  .row.tarot .bubble::before {{ left:-11px; border-right-color:#fff; }}
  .row.me .bubble::before {{ right:-11px; border-left-color:#95ec69; }}
  .time {{ text-align:center; margin:14px 0; }}
  .time span {{ background:#dadada; color:#fff; font-size:12px; padding:3px 8px; border-radius:3px; }}
</style></head>
<body>
  <div class="nav"><div class="back"></div><div class="title">{title}</div></div>
  <div class="chat">
    <div class="time"><span>{time}</span></div>
    {rows}
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
    persona = load_persona()
    creator = persona.get("name") or "塔罗师"
    rows = []
    for m in chat["messages"]:
        role = m["role"]
        text = str(m["text"]).replace("<", "&lt;").replace(">", "&gt;")
        # 视角：塔罗师放右边绿泡（截图发布者视角=塔罗师本人）
        side = "me" if role == "tarot" else "client"
        rows.append(
            f'<div class="row {side}">'
            f'<div class="avatar">{_initial(creator) if side == "me" else "客"}</div>'
            f'<div class="bubble">{text}</div></div>')
    ts = time.strftime("%H:%M")
    title = title or chat.get("scenario", "塔罗咨询")
    title = title[:14] + "…" if len(title) > 15 else title
    html = HTML_TMPL.format(
        title=title,
        time=ts, rows="\n".join(rows))
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
        page = b.new_page(viewport={"width": 390, "height": 800}, device_scale_factor=2)
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
