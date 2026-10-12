"""限速器 — 平台风控纪律。

三阶防打标：
1. 高斯抖动间隔：固定间隔本身就是机器特征（xhs 风控对比请求序列方差），
   用 random.uniform(min, max) + 偶发长停顿模拟人类节奏。
2. 拟人化浏览穿插：连续搜索间随机插入"打开一篇笔记停留 N 秒 + 滚动"，
   直跳详情页不经浏览路径是明确机器信号（socai 反爬规则确认）。
3. APM 风控上报拦截：xhs 页面 JS 会把 isRiskUser 判定上报服务端（autoclaw
   risk_analyzer 方案），在 fetch 里拦截该字段——被打标前主动降速，不等扫码。
"""
from __future__ import annotations

import random
import time

DEFAULT_INTERVAL = 30.0

# 每平台抖动区间 (min, max)——平均值接近原固定间隔，方差是保护本身
JITTER_RANGE: dict[str, tuple[float, float]] = {
    "xhs": (20.0, 60.0),
    "douyin": (18.0, 50.0),
    "x": (20.0, 55.0),
    "weibo": (18.0, 50.0),
    "bilibili": (8.0, 20.0),
    "youtube": (8.0, 20.0),
}

# 偶发长停顿：约 8% 概率插入 90-180s（人去倒水/看手机）
LONG_PAUSE_PROB = 0.08
LONG_PAUSE_RANGE = (90.0, 180.0)


class RateLimiter:
    def __init__(self, interval: float = DEFAULT_INTERVAL,
                 per_platform: dict[str, float] | None = None,
                 jitter: bool = True):
        self.interval = interval
        self.per_platform = per_platform or {}
        self.jitter = jitter
        self._last: dict[str, float] = {}
        # 拟人化穿插状态：连续同类操作计数（连续搜索越多越该插浏览）
        self._streak: dict[str, int] = {}
        # 风控状态（由 APM 拦截钩子更新）
        self.risk_flag: dict[str, bool] = {}
        self._on_risk = None  # 回调：检测到 isRiskUser 时触发

    def set_risk_callback(self, fn) -> None:
        """fn(platform, detail) — 检测到风控标记时调用（用于主动降速/告警）。"""
        self._on_risk = fn

    def wait(self, platform: str) -> float:
        """阻塞到该平台下次允许抓取，返回实际等待秒数。"""
        lo, hi = JITTER_RANGE.get(platform, (self.interval * 0.7, self.interval * 1.8))
        gap = random.uniform(lo, hi) if self.jitter else self.per_platform.get(platform, self.interval)
        # 已被打标 → 主动降速 2.5 倍（不等扫码才反应）
        if self.risk_flag.get(platform):
            gap *= 2.5
        # 连续第 4+ 次搜索 → 强制插一次拟人化浏览（由 adapter 注册 hook 执行）
        if self._streak.get(platform, 0) >= 3:
            hook = getattr(self, "_browse_hook", {}).get(platform)
            if hook:
                hook()
            self._streak[platform] = 0
            # 浏览穿插后再来一次常规等待
            gap = random.uniform(lo, hi)
        now = time.monotonic()
        last = self._last.get(platform)
        sleep = 0.0 if last is None else max(0.0, gap - (now - last))
        # 偶发长停顿
        if random.random() < LONG_PAUSE_PROB:
            sleep += random.uniform(*LONG_PAUSE_RANGE)
        if sleep > 0:
            time.sleep(sleep)
        self._last[platform] = time.monotonic()
        self._streak[platform] = self._streak.get(platform, 0) + 1
        return sleep

    def register_browse_hook(self, platform: str, fn) -> None:
        """注册拟人化浏览穿插钩子（由 adapter 提供，打开笔记+滚动+停留）。"""
        if not hasattr(self, "_browse_hook"):
            self._browse_hook = {}
        self._browse_hook[platform] = fn

    def mark_risk(self, platform: str, detail: str = "") -> None:
        """APM 拦截钩子检测到 isRiskUser 时调用 → 主动降速。"""
        first = not self.risk_flag.get(platform)
        self.risk_flag[platform] = True
        if first and self._on_risk:
            self._on_risk(platform, detail)

    def clear_risk(self, platform: str) -> None:
        self.risk_flag[platform] = False
