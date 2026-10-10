"""分析拆解 — 资产库（钩子库 / 标题库 / 封面模板库）。

拆解结论 JSON 结构（v1）:
{
  "wid": "...", "platform": "...",
  "hook": {"second": 3, "text": "..."},        # 钩子出现在第几秒
  "structure": "list|story|tutorial",           # 清单体/故事体/教程体
  "cover_template": "big_text|product|face",    # 封面模板
  "topics": ["评论区话题"],
  "migratable": ["可迁移观点"],
  "score": {"hook": 8, "structure": 7}          # 六维评分（对齐 doctor）
}
"""

from __future__ import annotations

import json
from pathlib import Path

ASSETS_FILE = "output/assets/assets.json"


class AssetStore:
    """跨任务累积复用的资产库。"""

    def __init__(self, path: str | Path = ASSETS_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = {"hooks": [], "titles": [], "covers": [], "analyses": {}}

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_analysis(self, platform: str, wid: str, result: dict) -> None:
        self.data["analyses"][f"{platform}-{wid}"] = result
        if hook := result.get("hook", {}).get("text"):
            self.data["hooks"].append({"platform": platform, "wid": wid, "text": hook})
        if title := result.get("title"):
            self.data["titles"].append({"platform": platform, "wid": wid, "text": title})
        if tpl := result.get("cover_template"):
            self.data["covers"].append({"platform": platform, "wid": wid, "template": tpl})
        self.save()

    def top_hooks(self, n: int = 10) -> list[dict]:
        """按对应作品得分排序取 top 钩子。"""
        scored = []
        for h in self.data["hooks"]:
            a = self.data["analyses"].get(f"{h['platform']}-{h['wid']}", {})
            scored.append((a.get("score", {}).get("hook", 0), h))
        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored[:n]]

    def stats(self) -> dict:
        return {k: len(v) for k, v in self.data.items()}
