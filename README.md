# 玄学 Autopilot · 对标账号内容闭环系统

个人非商用的自媒体对标闭环工作台：**找号对标 → 批量采集/下载 → 分析拆解 → 内容产出 → 发布 → 复盘数据回流**。

架构设计与选型依据见 [docs/design.md](docs/design.md)（源自《对标采集分析产出闭环方案》）。

## 模块

| 模块 | 目录 | 说明 |
|---|---|---|
| 对标账号库 | `lib/bench/` | 账号/作品增同步，SQLite 存储 |
| 采集下载 | `lib/collect/` | MediaCrawler/yt-dlp 适配层 + 限速风控 |
| 分析拆解 | `lib/analyze/` | 钩子/结构/封面拆解，沉淀资产库 |
| 内容产出 | `lib/compose/` | 仿写初稿 + 去 AI 味 + 多平台格式适配 |
| 发布队列 | `lib/publish/` | 人审网关 + 批量/定时发布适配 |
| 复盘回流 | `lib/review/` | 发布后数据回采，结论写回资产库 |

## 状态

🚧 开发中。当前为骨架 + 核心库实现阶段。

## 合规

- 仅限个人非商用。MediaCrawler / social-auto-upload 系许可证限商用。
- 任何 cookie / token / 登录态不入库不入 Git（`.gitignore` 已覆盖）。
- 抓取控频 ≥ 30s/条；发布必须过人工审核网关。
