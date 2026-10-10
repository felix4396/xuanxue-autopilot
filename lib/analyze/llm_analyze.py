"""LLM 深度拆解：用多模态模型把逐字稿+互动数据转成 doctor 风格拆解报告。

替换"模拟 doctor 报告"的临时做法。输出结构与 lib/analyze/doctor.py
的解析格式一致（钩子秒数/文本、结构类型、评分、标题、封面模板、
可迁移观点），直接可入库 AssetStore。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..llm import chat_json

SYSTEM = (
    "你是爆款短视频拆解专家。给定一条对标视频的逐字稿和元数据，"
    "输出严格的拆解 JSON。结构类型只能从：清单体/故事体/教程体/观点体 中选。"
)

PROMPT = """拆解以下对标视频：

标题：{title}
平台互动数据：{stats}
逐字稿（可能不完整）：
{transcript}

输出 JSON，字段：
- hook: {{"second": 开头钩子出现的秒数(int), "text": 钩子内容概括}}
- structure: 结构类型（清单体/故事体/教程体/观点体）
- score: {{"hook": 钩子强度1-10(int)}}
- title: 仿写一个同风格的新标题（15字内）
- cover_template: 封面模板建议（如 big_text/人物+大字/清单封面）
- takeaways: 可迁移观点数组，2-3条
- why_viral: 爆款原因，1-2句"""


def analyze_transcript(
    title: str,
    transcript_path: Path,
    stats: dict | None = None,
) -> dict:
    """读 asr.json 逐字稿，调 LLM 产出拆解 JSON。"""
    rows = json.loads(Path(transcript_path).read_text())
    transcript = " ".join(r.get("text", "") for r in rows)[:6000]
    out = chat_json(
        PROMPT.format(title=title, stats=json.dumps(stats or {}, ensure_ascii=False), transcript=transcript),
        system=SYSTEM,
        max_tokens=900,
        temperature=0.5,
    )
    return out
