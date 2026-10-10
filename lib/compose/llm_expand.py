"""LLM 扩写：把模板初稿扩成可直接发布的成稿，并顺带去 AI 味。

替代 AIWriteX 外链方案——同一个 deepseek-v4.1-flash 端点同时承担
拆解（lib/analyze/llm_analyze.py）与扩写。去 AI 味词表来自
lib/compose/deaigen.py，写进 system prompt 约束。
"""
from __future__ import annotations

from .deaigen import AI_FLAVOR_WORDS
from .draft import Draft


def _system() -> str:
    banned = "、".join(AI_FLAVOR_WORDS[:12])
    return (
        "你是自媒体写手。把给定初稿扩写成成稿。硬性规则："
        f"禁用这些AI腔词汇：{banned} 等；句子短，说人话；"
        "多用具体数字和场景，不用排比堆砌；正文用口语，像跟朋友讲。"
    )


PROMPT = """初稿信息：
选题：{topic}
平台：{platform}（标题限{limit}字）
候选标题：{titles}
开头：{opening}
正文骨架：{body}
自己要表达的角度：{angle}

输出 JSON：
- titles: 3 个成稿标题（都 ≤{limit} 字）
- opening: 成稿开头（2 句内，含钩子）
- body: 正文段落数组（{n} 段，每段 2-4 句）
- cta: 结尾行动号召（1 句）"""


def expand_draft(
    draft: Draft,
    angle: str = "",
    *,
    max_tokens: int = 1500,
) -> Draft:
    """模板初稿 → LLM 扩写成稿。返回新 Draft（保持 dataclass 结构）。"""
    from ..llm import chat_json

    limit = 20 if draft.platform == "xhs" else 55
    out = chat_json(
        PROMPT.format(
            topic=draft.topic,
            platform=draft.platform,
            limit=limit,
            titles="；".join(draft.titles),
            opening=draft.opening,
            body="；".join(draft.body),
            angle=angle or "（初稿未指定，自选贴近选题的角度）",
            n=3 if len(draft.body) >= 3 else 2,
        ),
        system=_system(),
        max_tokens=max_tokens,
        temperature=0.8,
    )
    if not out:
        raise RuntimeError("LLM 扩写失败：返回不是合法 JSON")
    new = Draft(topic=draft.topic, platform=draft.platform)
    new.titles = [t[:limit] for t in out.get("titles", draft.titles)][:3]
    new.cover_texts = draft.cover_texts
    new.opening = out.get("opening", draft.opening)
    new.body = out.get("body", draft.body)
    new.cta = out.get("cta", draft.cta)
    return new
