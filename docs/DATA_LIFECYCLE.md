# 数据生命周期

## 一键管线

```bash
python scripts/pipeline.py                  # 全流程: 采集→加工→瘦身→导出
python scripts/pipeline.py --no-collect     # 只加工存量+瘦身+导出
python scripts/pipeline.py --platforms xhs  # 单平台
python scripts/pipeline.py --engine local   # ASR 走本地 whisper（默认云端）
```

## 分层存储

```
output/media/    工作区    素材包（frames/*.jpg + audio.m4a + asr.json + meta.json）
                          视频原片在素材齐备后由 sweep 删除
output/corpus/   最终交付  轻量资产（0.2G / 298 作品）：
                          视频: content.md + media-cover.jpg + frame-first/mid/last.jpg
                                + asr.json（句级时间戳）+ meta.json
                          图集: content.md + img-1..N.jpg（全图）+ meta.json
                          纯文本: content.md + meta.json
```

## 自愈机制（无需人工干预）

| 场景 | 行为 |
|---|---|
| 详情页风控/失败 | 跳过不入库，下轮采集自动重试（统计显示 ⏭️ 风控跳过） |
| 历史作品正文空 | 视同待抓，每轮自动补抓（title='' 即入重试队列） |
| 媒体已加工 | 重抓正文时不重下视频、不重转写 |
| 素材齐备的原片 | sweep 自动删除，source_url 回档（db 列 + meta.json） |
| 无声视频（纯BGM） | ASR 写空文稿，不算失败 |
| 转写临时网络失败 | 重跑 prep_materials 自动补（asr.json 存在即跳过） |

## 各步骤说明

1. **采集** `collect_accounts.py` — 主页拉作品列表 + 详情页抓正文/评论/媒体 + 下载。风控失败的作品不入库。
2. **加工** `prep_materials.py --engine cloud` — ffmpeg 抽帧（默认10帧）+ 提音 + 阿里云百炼 `qwen-audio-3.1-asr-flash-filetrans`（OSS 签名 URL 暂存，转写完即删云端对象；~4s/条，带字级时间戳）。
3. **瘦身** `lib/lifecycle.py --sweep` — 素材齐备（frames≥1 且 asr.json 存在）→ 删原片；兜底扫 `media_path` 空但磁盘有 mp4 的漏网件。
4. **导出** `lib/corpus.py export_all` — 每作品一个目录，零视频零音频，全部可回溯（`source_url`，xhs 带 xsec_token 可直接打开）。

## 回溯与清理

- 找原片链接: `SELECT source_url FROM works WHERE wid='...'` 或读语料 `meta.json`
- 彻底删素材包回收磁盘: 手动删 `output/media/{platform}-{wid}/`（不影响 corpus）
- 可选冷备模式: `python lib/lifecycle.py --sweep --mode archive --purge-days 30`
