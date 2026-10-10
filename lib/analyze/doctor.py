"""doctor 链路 — social-account-doctor 深度拆解的桥接与导入。

不复刻 doctor 的分析逻辑（六维评分/封面体检/仿写由 doctor 的 CLI 执行），
本模块只做两件事：
1. runner: 调用 doctor 安装目录的脚本（analyze_video.py 等），产物落 output/reports/
2. importer: 把 doctor 报告（markdown/JSON）里的拆解结论导入资产库

doctor 仓库: https://github.com/JuneYaooo/social-account-doctor (MIT)
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from lib.analyze.assets import AssetStore


class DoctorRunner:
    """调用本地安装的 social-account-doctor 脚本。

    doctor_dir 指向安装后的 skill 目录（含 scripts/analyze_*.py）。
    """

    def __init__(self, doctor_dir: str | Path):
        self.doctor_dir = Path(doctor_dir)
        if not (self.doctor_dir / "scripts").exists():
            raise FileNotFoundError(f"doctor 安装目录无效: {self.doctor_dir}")

    def run(self, script: str, *args: str, timeout: int = 600) -> str:
        out = subprocess.run(
            ["python3", str(self.doctor_dir / "scripts" / script), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if out.returncode != 0:
            raise RuntimeError(f"doctor {script} 失败: {out.stderr[:300]}")
        return out.stdout


STRUCT_MAP = {"清单体": "list", "故事体": "story", "教程体": "tutorial", "观点体": "opinion"}


def import_doctor_report(report_path: str | Path, platform: str, wid: str, assets: AssetStore) -> dict:
    """从 doctor 的 markdown 报告提取可结构化结论，写入资产库。

    提取规则（宽容解析，v1）:
    - 钩子: 匹配「钩子」行附近的秒数与文本
    - 结构: 匹配 清单体/故事体/教程体
    - 评分: 匹配「钩子 N 分」形式
    """
    text = Path(report_path).read_text(encoding="utf-8")
    result: dict = {"platform": platform, "wid": wid, "source": "doctor"}

    m = re.search(r"钩子[^0-9]{0,10}第\s*(\d+)\s*秒[:：]?\s*([^\n]*)", text)
    if m:
        result["hook"] = {"second": int(m.group(1)), "text": m.group(2).strip()[:80]}
    for cn, en in STRUCT_MAP.items():
        if cn in text:
            result["structure"] = en
            break
    m = re.search(r"钩子[^\d]{0,6}(\d+)\s*分", text)
    if m:
        result["score"] = {"hook": int(m.group(1))}
    m = re.search(r"标题[：:]\s*([^\n]{4,60})", text)
    if m:
        result["title"] = m.group(1).strip()

    assets.add_analysis(platform, wid, result)
    return result
