"""微信对话内容生成：账号资产文案 → 对话式 md 文档。

用途：塔罗/玄学账号常见的"塔罗师和客户的聊天实录"型内容（图文形式）。
流程：
  1. 用账号资产（画像+内容资产+persona）生成一段对话（塔罗师 vs 客户）
  2. 排布为对话体 md 文档 → output/shots/{ts}.md
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
    "风格解读（学它的结构和话术套路）。对话要口语、有停顿感、"
    "客户消息短、塔罗师消息可稍长但别大段。"
    "⚠ 身份铁律：塔罗师的自称、名字、口头禅一律用「{creator}」本人设定，"
    "严禁使用对标账号的自称/昵称/标志性自我介绍（如对方的 opening）。"
    "输出严格 JSON。"
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

CHAT_SYSTEM = (
    "你是「{creator}」（{role_creator}）和客户的微信聊天编剧。"
    "根据账号画像写出一段真实感强的微信对话：客户提问，{creator}以该账号的"
    "风格解读（学它的结构和话术套路）。对话要口语、有停顿感、"
    "客户消息短、塔罗师消息可稍长但别大段。"
    "⚠ 身份铁律：塔罗师的自称、名字、口头禅一律用「{creator}」本人设定，"
    "严禁使用对标账号的自称/昵称/标志性自我介绍（如对方的 opening）。"
    "输出严格 JSON。"
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


def _initial(name: str) -> str:
    return (name or "?")[0]


def gen_chat(platform: str, uid: str, topic: str = "",
             n_round: int = 5, creator_role: str = "me") -> dict:
    """用账号资产生成对话文案。"""
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


MD_TMPL = """# {title}

> 场景：{scenario}
> 生成：{ts} ｜ 参考账号：{ref}

{body}
"""


def render_shot_md(chat: dict, title: str = "", ref: str = "", out_dir: Path | None = None) -> Path:
    """对话 dict → 对话体 md 文档。"""
    persona = load_persona()
    creator = persona.get("name") or "塔罗师"
    title = title or chat.get("scenario", "塔罗咨询")
    lines, n = [], 0
    for m in chat["messages"]:
        role = m["role"]
        text = str(m["text"]).strip()
        if n and n % 6 == 0:
            hh = 16 + n // 12
            hh12 = hh - 12 if hh > 12 else hh
            lines.append(f"*—— 星期五 下午 {hh12}:{(n * 7) % 60:02d} ——*\n")
        n += 1
        if role == "tarot":
            lines.append(f"**{creator}**：{text}\n")
        else:
            lines.append(f"**客户**：{text}\n")
    out_dir = out_dir or SHOTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"chat-{int(time.time())}.md"
    md_path.write_text(MD_TMPL.format(
        title=title,
        scenario=chat.get("scenario", ""),
        ts=time.strftime("%Y-%m-%d %H:%M"),
        ref=ref or "—",
        body="\n".join(lines)))
    return md_path


def generate_shot(platform: str, uid: str, topic: str = "", n_round: int = 5) -> Path:
    """一键：资产文案 → 对话体 md 文档。"""
    chat = gen_chat(platform, uid, topic, n_round)
    return render_shot_md(chat, ref=f"{platform}:{uid}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="微信对话体内容生成")
    ap.add_argument("--platform", required=True)
    ap.add_argument("--uid", required=True)
    ap.add_argument("--topic", default="")
    ap.add_argument("--rounds", type=int, default=5)
    a = ap.parse_args()
    p = generate_shot(a.platform, a.uid, a.topic, a.rounds)
    print("文档:", p)
