# 玄学 Autopilot · 对标账号内容闭环系统

个人非商用的自媒体对标闭环工作台：**找号对标 → 批量采集/下载 → 分析拆解 → 内容产出 → 发布 → 复盘数据回流**。

架构设计与选型依据见 [docs/design.md](docs/design.md)。数据生命周期细则见 [docs/DATA_LIFECYCLE.md](docs/DATA_LIFECYCLE.md)。

## 一键管线

```bash
python scripts/pipeline.py                  # 全流程 6 步（约 40 分钟，增量幂等）
python scripts/pipeline.py --no-collect     # 只处理存量：加工→瘦身→导出→画像→资产
python scripts/pipeline.py --platforms xhs  # 单平台
python scripts/pipeline.py --engine local   # ASR 换本地 whisper（默认云端百炼）
```

## 管线六步

| 步 | 模块 | 做什么 | 产物 |
|---|---|---|---|
| 1 采集 | `lib/collect/` | CDP 驱动 Chrome 抓主页列表+详情页（正文/评论/媒体），支持 6 平台：xhs / douyin / bilibili / x / youtube / weibo（`--platforms` 选配，默认全跑），风控失败跳过下轮自愈 | `data/bench.db` + `output/media/` |
| 2 加工 | `lib/analyze/video.py` | ffmpeg 抽帧（10帧）+ 提音 + 云端 ASR（百炼 `qwen-audio-3.1-asr-flash-filetrans`，OSS 中转，~4s/条，句级时间戳） | `frames/ + audio.m4a + asr.json` |
| 3 瘦身 | `lib/lifecycle.py` | 素材齐备即删原片（兜底扫磁盘漏网件），`source_url` 回档 | media 只留帧图+音频 |
| 4 导出 | `lib/corpus.py` | 轻量语料：视频=封面+三帧+文稿，图集=全图，零视频零音频 | `output/corpus/`（0.2G） |
| 5 画像 | `lib/analyze/profile.py` | LLM 提炼账号风格（人设/语气/标题公式/`gen_brief`），增量 | `output/assets/profiles.json` |
| 6 内容资产 | `lib/analyze/content_assets.py` | 逐作品文字稿浓缩精华卡（钩子/金句/话术/结构）→ 账号级归并（选题清单/金句池/话术模板/内容空白） | `output/assets/content_assets.json` |

## 内容生成（画像+资产双驱动）

```bash
python -m lib.compose.generate --platform xhs --uid Ginababy1111
```

`lib/compose/generate.py` 全自动两步：
1. **选题规划** — 读内容资产的选题清单 + `content_gaps`（内容空白）选差异化选题，给出切入角度和参考代表作
2. **成稿生成** — 画像 `gen_brief` 定风格 + 参考卡片的钩子/金句/话术做 few-shot + **创作者 persona 注入身份**

双资产分工：
- `profiles.json` → 对标账号**怎么说话**（人设/语气/公式，抽象层）
- `content_assets.json` → 对标账号**讲过什么**（选题/金句/话术模板，素材层）
- `persona.json` → **我自己是谁**（名字/自称/口头禅/固定开场结尾）

生成规则：学对标账号的结构和套路，身份/自称/口头禅/结尾强制用 persona 的，对标账号标志性词汇禁止照搬。产物落 `output/drafts/`（md + json 含选题依据与参考 wid，可溯源）。

## 代码组织

```
lib/
├── bench/                # 对标账号库（SQLite: data/bench.db，账号/作品/评论）
├── collect/              # 采集下载（CDP 驱动 + yt-dlp 适配 + 限速风控）
│   └── unified/          #   六平台统一采集器（base 主循环 + xhs/douyin/bilibili/x/youtube/weibo 实现）
├── analyze/              # 分析拆解
│   ├── video.py          #   抽帧/提音/云端 ASR（OSS 签名 URL 中转）
│   ├── llm_analyze.py    #   单作品 LLM 拆解（钩子/结构/可迁移观点）
│   ├── profile.py        #   账号画像（风格/领域/文案特征）
│   ├── content_assets.py #   内容资产（作品精华卡 + 账号归并）
│   └── assets.py         #   爆款元素资产库（钩子/标题/封面）
├── compose/              # 内容产出
│   ├── generate.py       #   账号驱动生成（画像+资产+persona → 成稿）
│   ├── draft.py          #   Draft 数据结构 + 平台格式适配
│   ├── llm_expand.py     #   初稿 LLM 扩写
│   └── deaigen.py        #   去 AI 味词表
├── publish/              # 发布队列（人审网关 + 上传器适配点）
├── review/               # 复盘回流（发布后数据回采）
├── corpus.py             # 语料导出（轻量资产：帧图+文稿+链接）
├── lifecycle.py          # 数据生命周期（sweep 删原片 / 用量统计 / 冷备）
├── llm.py                # LLM 网关（deepseek-v4.1-flash，chat_json 带重试+截断抢救）
└── pipeline.py           # 全流程串联（Pipeline 类，脚本级编排用 scripts/pipeline.py）
```

## 数据存储

```
data/
└── bench.db              # SQLite：accounts / works / comments（source_url 回溯链接）

output/
├── media/                # 工作区（~5.3G）：{platform}-{wid}/ 素材包
│   └── xhs-6a3b.../        frames/*.jpg + audio.m4a + asr.json + meta.json
│                           （原片在素材齐备后由 sweep 自动删除）
├── corpus/               # 最终语料（0.2G，298 作品）
│   └── xhs/{wid}/          视频: content.md + media-cover.jpg + frame-first/mid/last.jpg
│                                 + asr.json（句级时间戳）+ meta.json（含 source_url）
│                           图集: content.md + img-1..N.jpg + meta.json
├── assets/               # LLM 沉淀的资产库（长期维护）
│   ├── persona.json        ★ 创作者个人固定信息（名字/自称/口头禅/固定开场结尾）
│   ├── profiles.json       15 个对标账号画像（niche/style/topics/vocabulary/gen_brief）
│   ├── content_assets.json 246 作品精华卡 + 13 账号归并（选题/金句池/话术/结构/空白）
│   └── assets.json         爆款元素池（钩子/标题/封面）
├── drafts/               # 生成的成稿（md + json 选题依据可溯源）
├── reports/              # doctor 拆解报告
└── archive/              # 可选冷备（sweep --mode archive）
```

## 自愈机制（无需人工干预）

| 场景 | 行为 |
|---|---|
| 详情页风控/失败 | 跳过不入库，下轮采集自动重试 |
| 历史作品正文空 | 视同待抓，每轮自动补抓 |
| 媒体已加工 | 不重下视频、不重转写（asr.json 存在即跳过） |
| 素材齐备的原片 | sweep 自动删除，source_url 回档 |
| 无声视频（纯BGM） | ASR 写空文稿，不算失败 |
| LLM 空返回/JSON 截断 | 自动重试 + 截断抢救（lib/llm.py） |
| 画像/资产已存在 | 增量跳过，新账号/新作品自动补 |

## 整合的开源项目（只取关键能力，不引入冗余）

| 来源项目 | 集成内容 | 集成位置 |
|---|---|---|
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | JSONL 产物导入（不重写爬虫，只消费其结果 → 对标库） | `lib/collect/mediacrawler.py` |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | YouTube 字幕/封面/视频批量下载 | `lib/collect/ytdlp.py` |
| [social-account-doctor](https://github.com/JuneYaooo/social-account-doctor)（MIT） | 深度拆解执行 + 报告解析入库 | `lib/analyze/doctor.py` |
| [viral-video-analyzer](https://github.com/pelpeljakob-creator/viral-video-analyzer) | 抽帧 + 本地 ASR 原料包链路（FFmpeg + faster-whisper） | `lib/analyze/video.py` |
| [xhs-ai-writer](https://github.com/EBOLABOY/xhs-ai-writer) + [AIWriteX](https://github.com/iniwap/AIWriteX)（Apache-2.0） | 去 AI 味规则层 | `lib/compose/deaigen.py` |
| [Sunbird OS / social-auto-upload](https://github.com/niupTang/douyin) | 对标库数据模型 + 批量/定时发布协议 | `lib/bench/` + `lib/publish/dispatcher.py` |

## 快速开始

```bash
python3 -m tests.smoke         # 6 项冒烟
python3 -m tests.integration   # 5 项整合测试
python3 scripts/pipeline.py    # 跑一轮完整管线
python -m lib.compose.generate --platform xhs --uid <对标uid>   # 生成一条新内容
```

采集前置：Chrome 开调试口 `--remote-debugging-port=9222` 并登录三平台；douyin 需 `douyin_cookies.txt`。凭据在 `.env`（不入 git）：DASHSCOPE_API_KEY、OSS 四项、LLM_API_KEY/BASE_URL/MODEL。

## 状态

✅ 采集→加工→瘦身→导出→画像→内容资产 六步管线闭环；内容生成（persona + 双资产驱动）已验证出稿。

## 合规

- 仅限个人非商用。MediaCrawler / social-auto-upload 系许可证限商用。
- 任何 cookie / token / 登录态不入库不入 Git（`.gitignore` 已覆盖）。
- 抓取控频 ≥ 30s/条；发布必须过人工审核网关。
