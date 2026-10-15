"""一键数据管线：采集 → 加工(ASR) → 瘦身 → 语料导出 → 账号画像 → 内容资产。

用法:
  python scripts/pipeline.py                    # 全流程（采集所有账号）
  python scripts/pipeline.py --no-collect       # 跳过采集，只做加工+瘦身+导出
  python scripts/pipeline.py --platforms xhs    # 只跑指定平台
  python scripts/pipeline.py --engine local     # ASR 用本地 whisper（默认 cloud）

固化流程（docs/DATA_LIFECYCLE.md）:
  1. 采集   collect_accounts.py   详情页失败跳过不入库，下轮自愈重试
  2. 加工   prep_materials.py     抽帧 + 提音 + 云端ASR（百炼3.1-filetrans）
  3. 瘦身   lifecycle sweep       素材齐备即删原片，source_url 留档
  4. 导出   corpus export_all     轻量资产: 帧图+全图集+文字稿+链接
  5. 画像   analyze/profile.py    账号级风格资产沉淀（LLM 提炼）
  6. 资产   analyze/content_assets.py 逐作品精华卡+账号归并（选题/金句/话术库）
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")


def run(cmd: list[str], label: str) -> bool:
    print(f"\n{'=' * 50}\n▶ {label}\n{'=' * 50}")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print(f"⚠️ {label} 退出码 {r.returncode}（继续后续步骤）")
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="玄学自媒体数据管线")
    ap.add_argument("--no-collect", action="store_true", help="跳过采集")
    ap.add_argument("--no-prep", action="store_true", help="跳过加工")
    ap.add_argument("--platforms", default="xhs,douyin,bilibili,x,youtube,weibo")
    ap.add_argument("--mode", default="latest", choices=["latest", "monitor", "top"])
    ap.add_argument("--engine", default="cloud", choices=["cloud", "local"])
    ap.add_argument("--skip-asr", action="store_true")
    a = ap.parse_args()

    ok = True
    if not a.no_collect:
        ok &= run([PY, "-u", "scripts/collect_accounts.py",
                   "--platforms", a.platforms, "--mode", a.mode], "1/4 采集")
    if not a.no_prep:
        prep = [PY, "-u", "scripts/prep_materials.py", "--engine", a.engine]
        if a.skip_asr:
            prep.append("--skip-asr")
        ok &= run(prep, "2/4 加工（抽帧+ASR）")
    run([PY, "lib/lifecycle.py", "--sweep"], "3/4 瘦身（删原片）")
    run([PY, "-c",
         "import sys; sys.path.insert(0, '.'); "
         "from lib.corpus import export_all; print(export_all())"], "4/4 语料导出")
    run([PY, "-c",
         "import sys; sys.path.insert(0, '.'); "
         "from lib.analyze.profile import build_all; print(build_all())"], "5/5 账号画像沉淀")
    print("\n✅ 管线完成" if ok else "\n⚠️ 管线完成（有步骤警告，见上方日志）")


if __name__ == "__main__":
    main()
