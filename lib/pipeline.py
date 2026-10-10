"""pipeline — 全流程串联：找号 → 采集 → 拆解 → 产出 → 发布 → 复盘。

一次 run 执行一个平台的一条龙；每阶段产物落盘，断点可续。
"""

from __future__ import annotations

import time
from pathlib import Path

from lib.bench.store import BenchStore
from lib.collect.ratelimit import RateLimiter
from lib.analyze.assets import AssetStore
from lib.analyze.doctor import DoctorRunner, import_doctor_report
from lib.analyze.video import VideoPrep
from lib.compose.draft import compose_from_analysis, adapt_platform
from lib.compose.deaigen import deaigen_draft_body
from lib.publish.queue import PublishQueue
from lib.publish.dispatcher import Dispatcher
from lib.review.metrics import MetricsStore


class Pipeline:
    def __init__(self, data_dir: Path | str = "data", output_dir: Path | str = "output"):
        self.data = Path(data_dir)
        self.out = Path(output_dir)
        self.bench = BenchStore(self.data / "bench.db")
        self.assets = AssetStore(self.out / "assets" / "assets.json")
        self.queue = PublishQueue(self.data / "publish.db")
        self.metrics = MetricsStore(self.data / "metrics.db")
        self.limiter = RateLimiter()
        self.dispatcher = Dispatcher(self.queue)
        self.doctor: DoctorRunner | None = None
        self.video_prep = VideoPrep(self.out / "media")

    # ---- ① 找号（已有 CLI） ----
    # ---- ② 采集：由 mediacrawler.py / ytdlp.py 导入 bench ----

    def pending_for_analysis(self, platform: str, limit: int = 5) -> list[dict]:
        """取待拆解且已采集媒体的作品。"""
        works = [w for w in self.bench.pending_works("analyzed", platform) if w["media_path"]]
        return works[:limit]

    # ---- ③ 拆解 ----
    def analyze_one(self, platform: str, wid: str, report: Path) -> dict:
        """用 doctor 报告更新资产库，标记 analyzed。"""
        result = import_doctor_report(report, platform, wid, self.assets)
        self.bench.mark(platform, wid, "analyzed")
        return result

    # ---- ④ 产出 ----
    def compose(self, topic: str, analysis: dict, platform: str = "xhs", angle: str = "") -> Path:
        d = compose_from_analysis(topic, analysis, platform=platform, angle=angle)
        d.body = deaigen_draft_body(d.body)          # 规则级去 AI 味
        adapt_platform(d, platform)
        reports = self.out / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        out = reports / f"draft-{int(time.time())}-{platform}.md"
        out.write_text(d.to_markdown(), encoding="utf-8")
        self.queue.enqueue(platform, d.titles[0], d.to_markdown())
        return out

    # ---- ⑤ 发布 ----
    def publish_due(self) -> list[dict]:
        return self.dispatcher.dispatch_due()

    # ---- ⑥ 复盘 ----
    def review(self, platform: str, remote_id: str, published_at: float) -> list[float]:
        """返回已到期待回采的时点（小时）；回采数据由平台抓取器写入 metrics 后调 diagnose。"""
        return self.metrics.due_checkpoints(platform, remote_id, published_at)
