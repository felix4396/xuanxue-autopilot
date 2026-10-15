"""账号级画像资产：从语料库提炼账号风格/文案/领域特征，供内容生成复用。

数据流：output/corpus/{platform}/{wid}/（content.md + asr.json + meta.json）
  → 每账号聚合代表作素材（标题+正文+文字稿+互动数）
  → LLM 提炼账号画像 JSON
  → 沉淀 output/assets/profiles.json（AssetStore 同目录）
  → lib/compose 生成内容时读画像模仿该账号风格
"""
from __future__ import annotations

import json
from pathlib import Path

from ..llm import chat_json

ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS = ROOT / "output" / "corpus"
PROFILES_FILE = ROOT / "output" / "assets" / "profiles.json"

SYSTEM = (
    "你是自媒体账号研究专家。根据账号的多条作品（标题/正文/口播文字稿/互动数据），"
    "提炼该账号的内容画像，供后续以同样风格生成新内容。输出严格的 JSON。"
)

PROMPT = """分析以下对标账号「{nickname}」（平台：{platform}，粉丝：{followers}）。

其近期作品（{n} 条，含标题/文案/口播稿/点赞）：
{samples}

输出 JSON，字段：
- niche: 内容领域（一句话，如"塔罗占卜·情感向大众占卜"）
- style: {{}} 风格特征：
  - tone: 语气调性（如"闺蜜聊天感/神秘权威/干货输出"）
  - persona: 人设关键词数组（2-4个）
  - opening_pattern: 开场钩子套路（描述+1个原文示例）
  - cta_pattern: 结尾引导套路（描述+1个原文示例）
- topics: 高频主题数组（3-6个，每个附出现频率 高/中/低）
- vocabulary: 该账号标志性用词/口头禅数组（3-8个，从原文提取）
- title_patterns: 标题公式数组（2-4个，每个含 pattern 描述 + 2个原例）
- content_structure: 单条内容的典型结构（分段描述，如"选组引导→三组解读→结尾引导"）
- references: 适合生成新内容时模仿的代表作 wid 数组（互动最高的 3-5 条）
- gen_brief: 给内容生成模型的一段话简报（用什么身份/语气/结构写什么主题）"""


def _load_transcript(wid_dir: Path, limit: int = 2500) -> str:
    """asr.json → 纯文本（截断，避免超上下文）。"""
    f = wid_dir / "asr.json"
    if not f.exists():
        return ""
    try:
        rows = json.loads(f.read_text())
        text = " ".join(r.get("text", "") for r in rows)
        return text[:limit]
    except Exception:
        return ""


def _body(wid_dir: Path, limit: int = 600) -> str:
    f = wid_dir / "content.md"
    if not f.exists():
        return ""
    try:
        return f.read_text().strip()[:limit]
    except Exception:
        return ""


def gather_account_samples(platform: str, uid: str, max_works: int = 12) -> list[dict]:
    """从语料目录聚合该账号代表作素材（按点赞排序取前 N）。"""
    import sqlite3
    con = sqlite3.connect(ROOT / "data" / "bench.db")
    rows = con.execute(
        "SELECT wid, title, likes FROM works WHERE platform=? AND uid=? "
        "ORDER BY likes DESC LIMIT ?", (platform, uid, max_works)).fetchall()
    samples = []
    for wid, title, likes in rows:
        d = CORPUS / platform / wid
        if not d.exists():
            continue
        sample = {"wid": wid, "title": title, "likes": likes or 0}
        body = _body(d)
        if body:
            sample["body"] = body
        tr = _load_transcript(d)
        if tr:
            sample["transcript"] = tr
        samples.append(sample)
    return samples


def build_profile(platform: str, uid: str, nickname: str = "",
                  followers: int = 0, max_works: int = 12) -> dict:
    """LLM 提炼单账号画像。返回画像 dict（失败返回 {}）。"""
    samples = gather_account_samples(platform, uid, max_works)
    if not samples:
        return {}
    lines = []
    for i, s in enumerate(samples, 1):
        seg = f"[{i}] {s['title']}（赞 {s['likes']}）wid={s['wid']}"
        if s.get("body"):
            seg += f"\n文案: {s['body']}"
        if s.get("transcript"):
            seg += f"\n口播稿: {s['transcript']}"
        lines.append(seg)
    prompt = PROMPT.format(
        nickname=nickname or uid, platform=platform, followers=followers or "未知",
        n=len(samples), samples="\n\n".join(lines))
    result = chat_json(prompt, system=SYSTEM, max_tokens=2000)
    if not result:
        return {}
    result.update({
        "platform": platform, "uid": uid, "nickname": nickname,
        "built_from": len(samples), "samples": [s["wid"] for s in samples],
    })
    try:
        from .assets_store import write_profile_md
        write_profile_md(platform, uid, result)
    except Exception:
        pass  # 目录版失败不阻塞主流程
    return result


def build_all(platforms: list[str] | None = None,
              store: Path | None = None) -> dict:
    """全账号画像构建 → profiles.json。返回统计。"""
    import sqlite3
    profiles = json.loads(store.read_text()) if store and store.exists() else {}
    if not platforms:
        platforms = ["xhs", "douyin", "bilibili"]
    con = sqlite3.connect(ROOT / "data" / "bench.db")
    stats = {"built": 0, "skipped": 0, "failed": 0}
    for platform in platforms:
        accs = con.execute(
            "SELECT uid, nickname, followers FROM accounts WHERE platform=?",
            (platform,)).fetchall()
        for uid, nickname, followers in accs:
            key = f"{platform}:{uid}"
            if profiles.get(key, {}).get("built_from"):
                stats["skipped"] += 1
                continue  # 已有画像（重算删 profiles.json 对应键）
            p = build_profile(platform, uid, nickname or "", followers or 0)
            if p:
                profiles[key] = p
                stats["built"] += 1
                print(f"  ✅ {key} {nickname} | {p.get('niche', '')[:30]}")
            else:
                stats["failed"] += 1
                print(f"  ❌ {key} 画像失败")
            out = store or PROFILES_FILE
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(profiles, ensure_ascii=False, indent=2))
    return stats


def get_profile(platform: str, uid: str, store: Path | None = None) -> dict:
    """读取账号画像（内容生成时用）。"""
    out = store or PROFILES_FILE
    if not out.exists():
        return {}
    return json.loads(out.read_text()).get(f"{platform}:{uid}", {})
