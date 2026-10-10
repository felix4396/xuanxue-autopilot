"""玄学 Autopilot — 对标账号内容闭环系统。

模块:
    lib.bench    对标账号库 (SQLite)
    lib.collect  采集下载适配层 (yt-dlp / MediaCrawler 适配点)
    lib.analyze  分析拆解 (钩子库 / 标题库 / 结构)
    lib.compose  内容产出 (仿写初稿 / 去 AI 味 / 多平台格式)
    lib.publish  发布队列 (人审网关 + 批量定时)
    lib.review   复盘回流 (数据回采写回资产库)
"""

__version__ = "0.1.0"
