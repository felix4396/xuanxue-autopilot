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

## 整合的开源项目（只取关键能力，不引入冗余）

| 来源项目 | 集成内容 | 集成位置 |
|---|---|---|
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | JSONL 产物导入（不重写爬虫，只消费其结果 → 对标库） | `lib/collect/mediacrawler.py` |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | YouTube 字幕/封面/视频批量下载 | `lib/collect/ytdlp.py` |
| [social-account-doctor](https://github.com/JuneYaooo/social-account-doctor)（MIT） | 深度拆解执行（本机调用其 analyze 脚本）+ 报告解析入库 | `lib/analyze/doctor.py` |
| [viral-video-analyzer](https://github.com/pelpeljakob-creator/viral-video-analyzer) | 抽帧 + 本地 ASR 原料包链路（FFmpeg + faster-whisper） | `lib/analyze/video.py` |
| [xhs-ai-writer](https://github.com/EBOLABOY/xhs-ai-writer) + [AIWriteX](https://github.com/iniwap/AIWriteX)（Apache-2.0） | 去 AI 味规则层（词表注入 AIWriteX 重写 prompt） | `lib/compose/deaigen.py` |
| [Sunbird OS / social-auto-upload](https://github.com/niupTang/douyin) | 对标库数据模型 + 批量/定时发布协议（uploader 适配点） | `lib/bench/` + `lib/publish/dispatcher.py` |

全流程串联入口：`lib/pipeline.py`（`Pipeline` 类）。
未集成的上游（AIWriteX 界面、Sunbird 桌面端、MediaCrawler 本体）按需单独安装，通过上述适配点对接。

## 快速开始

```bash
python3 -m tests.smoke         # 6 项冒烟
python3 -m tests.integration   # 5 项整合测试
python3 scripts/cli.py bench add douyin <uid> --nickname 某对标 --followers 1000
python3 scripts/cli.py analyze xhs n1 --file doctor报告.json
python3 scripts/cli.py compose "选题" --analysis output/assets/assets.json
python3 scripts/cli.py publish list
```

## 状态

🚧 骨架 + 六模块核心 + 整合适配层完成；平台真实采集/上传器为待接入的适配点。

## 合规

- 仅限个人非商用。MediaCrawler / social-auto-upload 系许可证限商用。
- 任何 cookie / token / 登录态不入库不入 Git（`.gitignore` 已覆盖）。
- 抓取控频 ≥ 30s/条；发布必须过人工审核网关。
