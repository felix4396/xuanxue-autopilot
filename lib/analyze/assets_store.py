"""账号资产目录化存储：每账号一个目录，json（程序消费）+ md（人读/沉淀）并存认。

目录布局：
  output/assets/accounts/{platform}-{uid}/
    profile.json / profile.md            账号画像
    content_assets.json / content_assets.md  内容资产（选题/金句/话术）
    report.md                            账号拆解总报告（画像+资产合成叙事版）

顶层 profiles.json / content_assets.json 保留为索引（兼容已有读取方），
本模块负责双向同步：写目录版同时刷新索引。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS_DIR = ROOT / "output" / "assets"
ACCOUNTS_DIR = ASSETS_DIR / "accounts"

PROFILE_MD = """# 账号画像 · {nickname}

> 平台：{platform}｜uid：{uid}｜样本：{built_from} 条代表作｜生成时间：{ts}

## 内容领域
{niche}

## 风格调性
{tone}

## 人设关键词
{persona}

## 开场套路
{opening}

## 结尾套路
{cta}

## 高频主题
{topics}

## 标志性用词
{vocabulary}

## 标题公式
{titles}

## 典型结构
{structure}

## 生成简报（gen_brief）
{gen_brief}
"""

ASSETS_MD = """# 内容资产 · {nickname}

> 平台：{platform}｜uid：{uid}｜精华卡：{card_count} 条｜生成时间：{ts}

## 选题清单
{topic_inventory}

## 最佳开场钩子（原文）
{hooks}

## 金句池
{golden}

## 话术模板库
{phrases}

## 验证过的内容结构
{structures}

## 内容空白 / 可扩展方向
{gaps}
"""

REPORT_MD = """# 拆解报告 · {nickname}

> 平台：{platform}｜uid：{uid}｜报告时间：{ts}
> 依据：{built_from} 条代表作画像 + {card_count} 条作品精华卡

## 一、账号定位
{niche}

{gen_brief}

## 二、风格拆解

**调性**：{tone}
**人设**：{persona}
**口头禅**：{vocabulary}

**开场怎么做**：{opening}

**结尾怎么做**：{cta}

**标题怎么起**：
{titles}

## 三、内容结构
{structure}

**验证过的结构**：
{structures}

## 四、选题地图
{topic_inventory}

## 五、可直接复用的素材

**金句池**：
{golden}

**话术模板**：
{phrases}

## 六、给 {creator} 的行动建议

**内容空白（优先做）**：
{gaps}

**参考代表作**：{refs}
"""


def account_dir(platform: str, uid: str) -> Path:
    d = ACCOUNTS_DIR / f"{platform}-{uid[:24]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fmt(v, bullet=True) -> str:
    """任意 json 片段 → md 可读文本。"""
    if v is None or v == "":
        return "（无）"
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        if all(isinstance(x, (str, int, float)) for x in v):
            return "、".join(str(x) for x in v) if not bullet else "\n".join(f"- {x}" for x in v)
        return "\n".join(f"- {json.dumps(x, ensure_ascii=False)}" if not isinstance(x, str)
                         else f"- {x}" for x in v)
    return json.dumps(v, ensure_ascii=False, indent=2)


def _get(style: dict, *keys, default="（无）"):
    cur = style
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur else default


def write_profile_md(platform: str, uid: str, p: dict) -> Path:
    import time
    d = account_dir(platform, uid)
    (d / "profile.json").write_text(json.dumps(p, ensure_ascii=False, indent=2))
    st = p.get("style", {})
    md = PROFILE_MD.format(
        nickname=p.get("nickname", uid), platform=platform, uid=uid,
        built_from=p.get("built_from", "?"),
        ts=time.strftime("%Y-%m-%d %H:%M"),
        niche=_fmt(p.get("niche"), bullet=False),
        tone=_fmt(_get(st, "tone"), bullet=False),
        persona=_fmt(_get(st, "persona")),
        opening=_fmt(_get(st, "opening_pattern"), bullet=False),
        cta=_fmt(_get(st, "cta_pattern"), bullet=False),
        topics=_fmt(p.get("topics")),
        vocabulary=_fmt(p.get("vocabulary")),
        titles=_fmt(p.get("title_patterns")),
        structure=_fmt(_get(st, "content_structure"), bullet=False),
        gen_brief=_fmt(p.get("gen_brief"), bullet=False))
    path = d / "profile.md"
    path.write_text(md)
    return path


def write_assets_md(platform: str, uid: str, a: dict, cards: dict | None = None) -> Path:
    import time
    d = account_dir(platform, uid)
    (d / "content_assets.json").write_text(json.dumps(a, ensure_ascii=False, indent=2))
    if cards:
        (d / "cards.json").write_text(json.dumps(cards, ensure_ascii=False, indent=2))
    md = ASSETS_MD.format(
        nickname=a.get("nickname", uid), platform=platform, uid=uid,
        card_count=a.get("card_count", "?"), ts=time.strftime("%Y-%m-%d %H:%M"),
        topic_inventory=_fmt(a.get("topic_inventory")),
        hooks=_fmt(a.get("best_hooks")),
        golden=_fmt(a.get("golden_lines_pool")),
        phrases=_fmt(a.get("phrase_templates")),
        structures=_fmt(a.get("proven_structures")),
        gaps=_fmt(a.get("content_gaps")))
    path = d / "content_assets.md"
    path.write_text(md)
    return path


def write_report_md(platform: str, uid: str, p: dict, a: dict) -> Path:
    """拆解总报告：画像+资产合成的叙事版（人读，含行动建议）。"""
    import time
    from ..llm import chat_json
    d = account_dir(platform, uid)
    persona = json.loads((ASSETS_DIR / "persona.json").read_text()) if (ASSETS_DIR / "persona.json").exists() else {}
    creator = persona.get("name", "创作者")
    st = p.get("style", {})
    base = REPORT_MD.format(
        nickname=p.get("nickname", uid), platform=platform, uid=uid,
        ts=time.strftime("%Y-%m-%d %H:%M"),
        built_from=p.get("built_from", "?"), card_count=a.get("card_count", "?"),
        niche=_fmt(p.get("niche"), bullet=False), gen_brief=_fmt(p.get("gen_brief"), bullet=False),
        tone=_fmt(_get(st, "tone"), bullet=False), persona=_fmt(_get(st, "persona")),
        vocabulary=_fmt(p.get("vocabulary"), bullet=False),
        opening=_fmt(_get(st, "opening_pattern"), bullet=False),
        cta=_fmt(_get(st, "cta_pattern"), bullet=False),
        titles=_fmt(p.get("title_patterns")),
        structure=_fmt(_get(st, "content_structure"), bullet=False),
        structures=_fmt(a.get("proven_structures")),
        topic_inventory=_fmt(a.get("topic_inventory")),
        golden=_fmt(a.get("golden_lines_pool")),
        phrases=_fmt(a.get("phrase_templates")),
        gaps=_fmt(a.get("content_gaps")),
        refs="、".join(a.get("card_wids", [])[:5]) or "（无）",
        creator=creator)
    # LLM 补一段针对创作者的行动建议叙事。失败直接抛错，不降级。
    advice = chat_json(
        f"以下是对标账号的画像与内容资产。请以资深内容操盘手视角，给创作者「{creator}」"
        f"写一份 300-500 字的模仿策略：先学什么、再学什么、避开什么（不要照搬对方哪些特征）。"
        f"输出 JSON: {{\"strategy\": \"...\"}}\n\n画像：{json.dumps(p, ensure_ascii=False)[:2000]}"
        f"\n\n资产：{json.dumps(a, ensure_ascii=False)[:2000]}",
        max_tokens=1200)
    if not advice or not advice.get("strategy"):
        raise RuntimeError(f"{platform}:{uid} report.md 模仿策略 LLM 生成失败")
    strategy = advice["strategy"]
    md = base + f"\n## 七、模仿策略\n\n{strategy}\n"
    path = d / "report.md"
    path.write_text(md)
    return path
