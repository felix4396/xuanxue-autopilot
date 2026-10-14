"""数据生命周期：原始长视频 → 加工素材 → 归档瘦身。

存储分层（output/ 下）：
  media/     工作区：素材包（frames/ audio.m4a/ asr.json/ meta.json）
             视频原始文件在其 asr 完成后移出
  archive/   冷备：原始 mp4（按平台分目录，bzip2 不适用视频，原样存）
             默认保留 14 天后可清（--purge）
  corpus/    最终交付：export_all 产物，仅含精简帧 + 文字稿 + 元数据 + 评论

原则：最终资产 = 抽帧图片 + 文字稿 + 结构化元数据。原视频只在素材包
不完整（asr 缺失 / frames 不足）时才需要回取。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "output" / "media"
ARCHIVE = ROOT / "output" / "archive"


def pack_dir(platform: str, wid: str) -> Path:
    return MEDIA / f"{platform}-{wid}"


def pack_complete(d: Path) -> bool:
    """素材包完整性：frames ≥1 且 asr.json 存在（空文稿=无声视频也算）。"""
    if not d.is_dir():
        return False
    frames = list((d / "frames").glob("*.jpg")) + list((d / "frames").glob("*.png"))
    return bool(frames) and (d / "asr.json").exists()


def archive_video(platform: str, wid: str, media_path: str, dry: bool = False) -> str | None:
    """asr+frames 齐备后，把原始 mp4 移入 archive/{platform}/。返回新路径或 None。"""
    src = Path(media_path)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        return None
    if not pack_complete(pack_dir(platform, wid)):
        return None  # 素材不齐，暂不归档
    dest_dir = ARCHIVE / platform
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dry:
        return str(dest)
    shutil.move(str(src), str(dest))
    return str(dest)


def sweep(dry: bool = False, days: int | None = None) -> dict:
    """全量扫描：素材包齐备的作品 → 原视频归档；days 给定时再清理过期归档。"""
    con = sqlite3.connect(ROOT / "data" / "bench.db")
    stats = {"archived": 0, "kept_incomplete": 0, "no_video": 0, "purged": 0}
    for platform, wid, mpath in con.execute(
            "SELECT platform, wid, media_path FROM works WHERE media_path LIKE '%.mp4'").fetchall():
        src = Path(mpath)
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            stats["no_video"] += 1
            continue
        dest = archive_video(platform, wid, mpath, dry=dry)
        if dest:
            if not dry:
                con.execute("UPDATE works SET media_path=? WHERE platform=? AND wid=?",
                            (dest, platform, wid))
            stats["archived"] += 1
        else:
            stats["kept_incomplete"] += 1
    con.commit()
    # 过期归档清理
    if days is not None:
        cutoff = time.time() - days * 86400
        for f in ARCHIVE.rglob("*.mp4"):
            if f.stat().st_mtime < cutoff:
                if not dry:
                    f.unlink()
                stats["purged"] += 1
    return stats


def disk_usage() -> dict:
    """分层磁盘占用报告。"""
    def _du(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0
    return {
        "media": _du(MEDIA),
        "archive": _du(ARCHIVE),
        "corpus": _du(ROOT / "output" / "corpus"),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="数据生命周期管理")
    ap.add_argument("--sweep", action="store_true", help="扫描并归档已加工完的原视频")
    ap.add_argument("--purge-days", type=int, default=None, help="清理 N 天前的归档视频")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--usage", action="store_true", help="报告分层磁盘占用")
    a = ap.parse_args()
    if a.usage:
        u = disk_usage()
        for k, v in u.items():
            print(f"  {k}: {v / 1e9:.1f} GB")
    if a.sweep or a.purge_days is not None:
        s = sweep(dry=a.dry, days=a.purge_days)
        print(json.dumps(s, ensure_ascii=False))
