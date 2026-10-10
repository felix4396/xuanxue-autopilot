"""复盘闭环 — 取自 Sunbird OS 的复盘逻辑：数据回采 → 诊断 → 结论写回资产库。

诊断规则（v1 规则版，LLM 版预留）：
- 播放低 + 完播低 → 问题在开头/钩子
- 播放高 + 互动率低 → 问题在 CTA/内容价值
- 互动率高于自身均值 → 该钩子入库加分
"""

from __future__ import annotations

from lib.analyze.assets import AssetStore
from lib.review.metrics import MetricsStore


def diagnose(platform: str, remote_id: str, metrics: MetricsStore, assets: AssetStore | None = None) -> dict:
    e = metrics.engagement(platform, remote_id)
    if not e:
        return {"verdict": "no_data"}
    last = e["last"]
    finish_rate = last["finishes"] / max(1, last["views"])
    verdicts = []
    if last["views"] < 500 and finish_rate < 0.3:
        verdicts.append("开头钩子弱：优先改前 3 秒")
    if e["like_rate"] < 0.03:
        verdicts.append("内容价值感不足：补干货密度或 CTA")
    if e["like_rate"] >= 0.08:
        verdicts.append("钩子有效：加分入库")
        if assets:
            top = assets.top_hooks(1)
            if top:
                assets.data.setdefault("validated", []).append(top[0])
                assets.save()
    return {
        "verdict": verdicts or "正常范围",
        "like_rate": round(e["like_rate"], 4),
        "finish_rate": round(finish_rate, 4),
        "views": last["views"],
    }
