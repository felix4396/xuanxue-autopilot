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
<link rel="stylesheet" href="phone.css">
<style>
  html,body {{ margin:0; width:1125px; background:#ededed; }}
</style>
</head>
<body>
<div class="wc-phone-wrap"><div class="wc-phone-content">
<div class="wc-phone wc-phone-ios">
  <div class="wc-phone-top">
    <div class="wc-status-bar">
      <div class="wc-time">{status_time}</div>
      <div class="wc-status-icons">
        <div class="wc-signal-group">
          <span class="wc-official-signal wc-official-signal-single"><img class="wc-official-signal-base" src="ios-cell-signal-official.png"><span class="wc-official-signal-active wc-official-signal-primary" style="width:100%"><img src="ios-cell-signal-official.png"></span></span>
          <span class="wc-official-wifi"><img src="ios-wifi-official.png"></span>
        </div>
        <div class="wc-battery-wrap" style="background-image:url(ios-battery-dark.png)"><span class="wc-battery-level"><i style="width:97%;background:#34c759"></i></span></div>
      </div>
    </div>
    <div class="wc-nav">
      <div class="wc-nav-left">
        <svg width="20" height="38" viewBox="0 0 27 52" fill="none"><path d="M25 2L3 26l22 24" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span class="wc-nav-badge">{unread}</span>
      </div>
      <div class="wc-nav-center"><span>{title}</span></div>
      <div class="wc-nav-right"><div class="wc-nav-dots"><i></i><i></i><i></i></div></div>
    </div>
  </div>
  <div class="wc-chat-body" style="background-color:#ededed">
    <div class="wc-chat-content">
{rows}
    </div>
  </div>
  <div class="wc-bottom">
    <div class="wc-bottom-chat">
      <div class="wc-bottom-inner">
        <div class="wc-bottom-icon"><img src="wechat-bottom-icon1.png"></div>
        <div class="wc-input-box">
          <svg class="wc-input-mic" viewBox="0 0 48 48" fill="none" stroke="#999" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 20v9a5 5 0 0 0 10 0v-9a5 5 0 0 0-10 0z"/><path d="M14 28c0 5.5 4.5 10 10 10s10-4.5 10-10"/><line x1="24" y1="38" x2="24" y2="42"/></svg>
        </div>
        <div class="wc-bottom-icon"><img src="wechat-bottom-icon2.png"></div>
        <div class="wc-bottom-icon"><img src="wechat-bottom-icon3.png"></div>
      </div>
    </div>
    <div class="wc-home-indicator"><i></i></div>
  </div>
</div>
</div></div>
</body></html>"""

TIME_NOTICE = '      <div class="wc-notice"><span class="wc-notice-bg">{t}</span></div>'
ROW = "      <div class=\"wc-dialog{cls}\">\n        <div class=\"wc-face\"><img src=\"{avatar}\"></div>\n        <div class=\"wc-body\"><div class=\"wc-bubble\"{style}><i class=\"wc-arrow\"{style}></i>{text}</div></div>\n      </div>"


def render_shot(chat: dict, title: str = "", out_dir: Path | None = None) -> Path:
    """对话 dict -> 微信截图 PNG（复刻 wechat-dialog-generator 的 DOM 与样式）。"""
    import html as _h
    persona = load_persona()
    creator = persona.get("name") or "塔罗师"
    rows, n = [], 0
    for m in chat["messages"]:
        role = m["role"]
        text = _h.escape(str(m["text"]))
        right = role == "tarot"  # 视角：塔罗师在右（截图发布者=塔罗师本人）
        if n and n % 6 == 0:
            hh = 16 + n // 12
            hh12 = hh - 12 if hh > 12 else hh
            rows.append(TIME_NOTICE.format(t="星期五 下午 %d:%02d" % (hh12, (n * 7) % 60)))
        n += 1
        color = "#95ec69" if right else "#ffffff"
        rows.append(ROW.format(
            cls=" wc-dialog-right" if right else "",
            avatar="avatar_2.png" if right else "avatar_1.png",
            style=' style="background:%s"' % color,
            text=text))
    html = HTML_TMPL.format(
        title=title or chat.get("scenario", "塔罗咨询"),
        unread="11",
        status_time=time.strftime("%H:%M").lstrip("0"),
        rows="\n".join(rows))
    out_dir = out_dir or SHOTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / ("chat-%d.html" % int(time.time()))
    html_path.write_text(html)
    # 资源直接拷贝到 html 旁（file:// 下 symlink 被视为跨源，CSS 会加载失败）
    import shutil
    for f in (ROOT / "lib" / "compose" / "wx_template").iterdir():
        dst = out_dir / f.name
        if dst.is_symlink():
            dst.unlink()
        shutil.copy2(f, dst)  # 总是覆盖，模板改了即生效
    from playwright.sync_api import sync_playwright
    png_path = html_path.with_suffix(".png")
    with sync_playwright() as p:
        import os
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        b = p.chromium.launch(executable_path=chrome if os.path.exists(chrome) else None)
        page = b.new_page(viewport={"width": 1125, "height": 2436}, device_scale_factor=1)
        # full_page 会自动展开实际内容高度
        page.goto("file://" + str(html_path))
        page.wait_for_timeout(400)
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
