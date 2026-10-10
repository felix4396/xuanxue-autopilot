"""去 AI 味处理器 — 取自 AIWriteX（Apache-2.0）与 xhs-ai-writer 提示词 v2.2 的规则层。

纯规则的确定性处理（不依赖外部 LLM）：
- 移除高频 AI 腔词（「非常」「总之」「综上所述」「值得注意的是」「让我们」…）
- 削弱模板句式（「首先…其次…最后…」→ 换行分隔）
- 压缩排比式三连（连续顿号短语 → 保留两个）
LLM 级重写仍由 AIWriteX 执行；本层做发布前的最后一道机械去味。

xhs-ai-writer 的 AIGC<10% 提示词版本（v2.2）见 docs/design.md 引用，其逻辑为提示词资产，
集成方式：compose 生成初稿后，将本模块词表注入 AIWriteX 的重写 prompt。
"""

from __future__ import annotations

import re

AI_FLAVOR_WORDS = [
    "非常", "总之", "综上所述", "值得注意的是", "让我们", "首先", "其次", "最后",
    "值得一提的是", "毫无疑问", "众所周知", "在这个时代", "随着", "赋能", "闭环思维",
    "deep dive", "In conclusion", "It is worth noting",
]

SENTENCE_SPLIT = re.compile(r"([。！？\n])")


def deaigen(text: str) -> str:
    """对一段文案执行规则级去 AI 味。"""
    for w in AI_FLAVOR_WORDS:
        text = text.replace(w, "")
    # 「首先/其次/最后」被删后可能残留行首标点
    text = re.sub(r"^\s*[，、。]\s*", "", text, flags=re.MULTILINE)
    # 压缩连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def deaigen_draft_body(body: list[str]) -> list[str]:
    return [deaigen(p) for p in body if deaigen(p)]
