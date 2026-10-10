"""采集适配层 — 统一适配点，具体抓取器后续接入。

设计原则（源自 docs/design.md）：
- 每个平台一个 Adapter，实现 fetch_account(uid) -> dict / fetch_works(uid) -> list[dict]
- 适配 MediaCrawler JSON 输出与 yt-dlp；本层不内置任何默认端点
- 媒体按 `平台-作品ID` 命名落盘（与 social-account-doctor 约定一致），24h 缓存复用
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path

CACHE_TTL = 24 * 3600


class BaseAdapter(ABC):
    platform: str = ""

    def __init__(self, cache_dir: str | Path = "output/media"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def cache_path(self, wid: str, suffix: str = "json") -> Path:
        return self.cache_dir / f"{self.platform}-{wid}.{suffix}"

    def read_cache(self, wid: str) -> dict | None:
        p = self.cache_path(wid)
        if not p.exists():
            return None
        if time.time() - p.stat().st_mtime > CACHE_TTL:
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def write_cache(self, wid: str, data: dict) -> Path:
        p = self.cache_path(wid)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    @abstractmethod
    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        """返回 [{wid,title,likes,comments,shares,media_url,cover_url}]。"""

    @abstractmethod
    def download_media(self, wid: str, url: str) -> Path:
        """下载作品媒体到 output/media/平台-作品ID.*，返回路径。"""


class YtdlpAdapter(BaseAdapter):
    """YouTube / 通用平台：yt-dlp 下载字幕、封面、视频。"""

    platform = "youtube"

    def fetch_works(self, uid: str, limit: int = 20) -> list[dict]:
        raise NotImplementedError("接入 yt-dlp: yt-dlp --flat-playlist --print id,title")

    def download_media(self, wid: str, url: str) -> Path:
        raise NotImplementedError("接入 yt-dlp --write-sub --write-auto-sub --write-thumbnail")


def get_adapter(platform: str, **kw) -> BaseAdapter:
    adapters = {a.platform: a for a in (YtdlpAdapter,)}
    if platform not in adapters:
        raise KeyError(f"未注册的平台适配器: {platform}，可用: {list(adapters)}")
    return adapters[platform](**kw)
