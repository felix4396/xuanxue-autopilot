"""内容产出 — 仿写初稿模板 + 多平台格式适配。

本模块只负责「结构化产出」：给定拆解结论与选题，产出结构化初稿。
文案扩写 / AI 配图 / 去 AI 味由外部 LLM（AIWriteX 链路）执行，接口预留 generate()。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Draft:
    topic: str
    titles: list[str] = field(default_factory=list)      # 3 个候选标题
    cover_texts: list[str] = field(default_factory=list)  # 3 个封面大字
    opening: str = ""                                     # 开头
    body: list[str] = field(default_factory=list)         # 正文段落
    cta: str = ""                                         # 行动号召
    platform: str = "xhs"

    def to_markdown(self) -> str:
        lines = [f"# {self.topic}", "", "## 候选标题"]
        lines += [f"{i+1}. {t}" for i, t in enumerate(self.titles)]
        lines += ["", "## 封面大字", *[f"- {c}" for c in self.cover_texts]]
        lines += ["", f"## 开头", self.opening, "", "## 正文", *self.body, "", "## CTA", self.cta]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


def compose_from_analysis(
    topic: str,
    analysis: dict,
    platform: str = "xhs",
    angle: str = "",
) -> Draft:
    """基于一条拆解结论 + 自己的角度，产出结构化仿写初稿。

    analysis: lib/analyze 拆解 JSON；angle: 自己可迁移的观点（来自 Sunbird 机会洞察）。
    """
    hook = analysis.get("hook", {})
    struct = analysis.get("structure", "list")
    d = Draft(topic=topic, platform=platform)
    # 标题公式：数字 + 人群 + 效果（doctor 推荐公式）
    d.titles = [f"{topic}", f"{len(hook.get('text','')) and '3个'}方法 | {topic}", f"为什么{topic}？{angle}"]
    d.cover_texts = [topic[:12], f"{topic[:8]}避坑", "新手必看"]
    d.opening = hook.get("text", "") or f"如果你也在做{topic}，这条先收藏。"
    if struct == "list":
        d.body = [f"{angle or topic} 的 3 个关键点：", "1. …", "2. …", "3. …"]
    else:
        d.body = [f"先说结论：{angle or topic}。", "背景：…", "转折：…"]
    d.cta = "觉得有用的话，收藏慢慢看。"
    return d


def adapt_platform(draft: Draft, target: str) -> Draft:
    """多平台格式适配（v1: 标题长度裁剪；后续接 AIWriteX）。"""
    limits = {"xhs": 20, "wechat": 64, "toutiao": 30, "douyin": 55}
    limit = limits.get(target, 30)
    draft.platform = target
    draft.titles = [t[:limit] for t in draft.titles]
    return draft
