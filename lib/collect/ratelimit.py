"""限速器 — 平台风控要求的最低纪律。

抓取间隔 >= 30s/条；可按平台覆盖。
"""

from __future__ import annotations

import time

DEFAULT_INTERVAL = 30.0


class RateLimiter:
    def __init__(self, interval: float = DEFAULT_INTERVAL, per_platform: dict[str, float] | None = None):
        self.interval = interval
        self.per_platform = per_platform or {}
        self._last: dict[str, float] = {}

    def wait(self, platform: str) -> float:
        """阻塞到该平台下次允许抓取，返回实际等待秒数。"""
        gap = self.per_platform.get(platform, self.interval)
        now = time.monotonic()
        last = self._last.get(platform)
        sleep = 0.0 if last is None else max(0.0, gap - (now - last))
        if sleep > 0:
            time.sleep(sleep)
        self._last[platform] = time.monotonic()
        return sleep
