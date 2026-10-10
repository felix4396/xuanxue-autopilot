"""发布执行器 — 取自 Sunbird OS / social-auto-upload 的能力位：批量 + 定时。

本模块只做两件事（不重新实现平台上传器）：
1. dispatch_due: 扫 PublishQueue.due()，把到期项交给注册的平台 uploader
2. Uploader 协议: 每平台一个 uploader，实现 upload(item) -> remote_id
   social-auto-upload 的 uploader/ 目录可直接注册进来（其 Playwright 实现是关键能力）
"""

from __future__ import annotations

import time
from pathlib import Path

from lib.publish.queue import PublishQueue


class Uploader:
    """平台上传器协议。social-auto-upload 的各平台 uploader 适配到此接口。"""
    platform: str = ""

    def upload(self, item: dict) -> str:
        """执行发布，返回平台侧 remote_id。"""
        raise NotImplementedError


class DryRunUploader(Uploader):
    """测试/演练用：不真发，返回伪 remote_id。"""
    platform = "dryrun"

    def __init__(self, log: Path):
        self.log = log
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def upload(self, item: dict) -> str:
        rid = f"dry-{int(time.time())}"
        with self.log.open("a", encoding="utf-8") as f:
            f.write(f"{rid}\t{item['platform']}\t{item['title']}\n")
        return rid


class Dispatcher:
    def __init__(self, queue: PublishQueue, uploaders: dict[str, Uploader] | None = None):
        self.queue = queue
        self.uploaders = uploaders or {}

    def register(self, platform: str, uploader: Uploader) -> None:
        self.uploaders[platform] = uploader

    def dispatch_due(self, now: float | None = None) -> list[dict]:
        """发布所有到期项。未注册平台的到期项跳过并保留在队列。"""
        results = []
        for item in self.queue.due(now):
            up = self.uploaders.get(item["platform"]) or self.uploaders.get("dryrun")
            if up is None:
                continue
            rid = up.upload(item)
            self.queue.transition(item["id"], "published")
            self.queue.conn.execute(
                "UPDATE publish_queue SET remote_id=? WHERE id=?", (rid, item["id"])
            )
            self.queue.conn.commit()
            results.append({"qid": item["id"], "remote_id": rid, "platform": item["platform"]})
        return results
