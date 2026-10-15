# 代码框架与数据流

## 数据流总图

```
                    ┌─────────────────────────────────────────┐
                    │  对标账号（6 平台: xhs/douyin/bilibili/   │
                    │  x/youtube/weibo，add 进 bench 库即生效）  │
                    └───────────────────┬─────────────────────┘
                                        │
══════════════ 管线 scripts/pipeline.py（①-⑥，增量幂等）══════════════
                                        │
        ┌───────────────────────────────▼───────────────────────────────┐
        │ ① 采集 lib/collect/unified/                                    │
        │   CDP 驱动 Chrome(9222) 抓主页列表 → 详情页(正文/评论/媒体)      │
        │   风控失败跳过不入库（下轮自愈）；have_partial 自动补抓           │
        │   写入: data/bench.db (accounts/works/comments + source_url)   │
        │        output/media/{p}-{wid}/  原片+图集原图                   │
        ├───────────────────────────────────────────────────────────────┤
        │ ② 加工 lib/analyze/video.py                                    │
        │   ffmpeg 抽帧(10帧) + 提音 audio.m4a                            │
        │   云端 ASR: OSS签名URL中转 → 百炼 filetrans → asr.json(句级)    │
        │   产物: frames/ + audio.m4a + asr.json + meta.json             │
        ├───────────────────────────────────────────────────────────────┤
        │ ③ 瘦身 lib/lifecycle.py sweep                                  │
        │   pack_complete(帧≥1且asr存在) → 删原片；兜底扫磁盘漏网件        │
        │   source_url 回档 db；media 只剩帧图+音频                       │
        ├───────────────────────────────────────────────────────────────┤
        │ ④ 导出 lib/corpus.py export_all                                │
        │   视频: content.md+封面+三帧+asr.json  图集: 全图 img-N.jpg     │
        │   产物: output/corpus/{p}/{wid}/（零视频零音频，0.2G/298作品）   │
        ├───────────────────────────────────────────────────────────────┤
        │ ⑤ 画像 lib/analyze/profile.py                                  │
        │   语料聚合代表作(按赞前12) → LLM 提炼风格/领域/公式/gen_brief    │
        │   产物: assets/profiles.json + accounts/{p}-{uid}/profile.*    │
        ├───────────────────────────────────────────────────────────────┤
        │ ⑥ 内容资产 lib/analyze/content_assets.py                       │
        │   逐作品文字稿 → 精华卡(钩子/金句/话术/结构) → 账号级归并         │
        │   产物: assets/content_assets.json + accounts/{p}-{uid}/cards  │
        │         .json / content_assets.md                              │
        └───────────────────────────────┬───────────────────────────────┘
                                        │
══════════════ 内容生成 lib/compose/generate.py（按需，单命令）═════════
                                        │
        ┌───────────────────────────────▼───────────────────────────────┐
        │ 三资产合流:                                                    │
        │   persona.json(我是谁: 名字/自称/口头禅/固定开场结尾)            │
        │   + profiles.json(对标怎么说话: gen_brief 风格层)               │
        │   + content_assets.json(对标讲过什么: 选题/金句/话术 素材层)     │
        │ 两步 LLM: 选题规划(优先 content_gaps) → 成稿(结构+few-shot)     │
        │ 规则: 学对标的结构和套路，身份强制用 persona，对标标志词禁照搬    │
        │ 产物: output/drafts/{p}-{uid}-{ts}.md + .json(可溯源)          │
        └───────────────────────────────┬───────────────────────────────┘
                                        │ 人审网关 lib/publish/
                                        ▼
                              发布 → lib/review/ 数据回流
```

## 资产分层（谁回答什么问题）

| 资产 | 回答的问题 | 层级 | 消费方 |
|---|---|---|---|
| `persona.json` | 我是谁，我怎么说话 | 创作者本人 | generate.py（身份注入） |
| `profiles.json` | 对标账号怎么说话 | 抽象风格层 | generate.py（风格模仿） |
| `content_assets.json` | 对标账号讲过什么 | 具体素材层 | generate.py（选题/话术/few-shot） |
| `assets/accounts/{p}-{uid}/` | 人读版沉淀（md 报告） | 展示层 | 人 / 仓库 |

## 代码组织

```
lib/
├── bench/store.py          SQLite 读写（accounts/works/comments）
├── collect/
│   ├── unified/            六平台统一采集器（base 主循环 + 各平台实现）
│   ├── ratelimit.py        限速风控
│   ├── adapter.py / comments.py / mediacrawler.py / ytdlp.py ...
├── analyze/
│   ├── video.py            抽帧/提音/云端 ASR
│   ├── llm_analyze.py      单作品拆解 → assets.json（爆款元素池）
│   ├── doctor.py           social-account-doctor 报告解析
│   ├── profile.py          账号画像（构建后自动同步 accounts/ 目录）
│   ├── content_assets.py   精华卡+归并（构建后自动同步 accounts/ 目录）
│   └── assets_store.py     账号资产目录化（json+md 双格式，report.md 拆解报告）
├── compose/
│   ├── generate.py         账号驱动生成（persona+双资产 → 成稿）
│   ├── draft.py / llm_expand.py / deaigen.py
├── publish/                人审网关 + 上传器适配点（待接真实上传器）
├── review/                 发布后数据回采（待接）
├── corpus.py               语料导出
├── lifecycle.py            sweep 删原片 / 用量统计
├── llm.py                  LLM 网关（chat_json: 重试+JSON截断抢救；失败抛错不降级）
scripts/
├── pipeline.py             ★ 一键管线（唯一管线入口）
├── collect_accounts.py     ① 的 CLI（管线内部调用）
├── prep_materials.py       ② 的 CLI
├── collect.py              关键词下载/用户下载独立 CLI
├── profiles.py → lib/profiles.py  账号资料采集
└── backfill_desc.py        历史正文补抓（自愈兜底）
```

## 失败处理原则

- LLM 调用失败（429/超时/空返回/JSON 坏）：`chat_json` 自动重试 2 次 + 截断抢救；仍失败**抛错**，不静默降级。
- 采集风控失败：跳过不入库，`skipped_risk` 统计，下轮自愈重试。
- 资产目录写失败不阻塞主流程（索引 json 是 source of truth，目录版可重建）。
