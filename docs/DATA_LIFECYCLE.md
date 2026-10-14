# 数据生命周期

## 分层存储

```
output/media/    工作区    素材包（frames/*.jpg + audio.m4a + asr.json + meta.json）
output/corpus/   最终交付  轻量资产：封面/首中尾帧 + asr.json + content.md + comments.json + meta.json
```

**不保留原视频**。素材齐备（frames + asr）后原片直接删除；回溯靠 `works.source_url`
（db 列 + 语料库 meta.json 均有），xhs 链接带 xsec_token 可直接打开。
可选 `--mode archive` 冷备到 output/archive/（默认关闭）。

## 原则

最终资产 = **抽帧图片 + 文字稿 + 结构化元数据**。原始长视频只在素材包不完整时才需要回取。

## 流水线

```
采集(collect_accounts.py)          加工(prep_materials.py)           瘦身(lifecycle.py)
  作品入库+媒体下载+评论  ──────►  ffmpeg 抽帧+提音 → ASR  ──────►  原视频 → archive/
  (media_path 指 mp4)              (local whisper 或 --engine cloud)  (sweep 自动触发)
```

- 采集结束自动 sweep（`--no-sweep` 关闭）：素材齐备的作品原片移入 archive/，media_path 改指归档路径
- ASR 双引擎：本地 faster-whisper（默认） / 云端百炼 `qwen-audio-3.1-asr-flash-filetrans`（`--engine cloud`，快 ~50 倍，带字级时间戳）
- 无声视频（纯 BGM）ASR 写空文稿，不算失败
- 归档清理：`python lib/lifecycle.py --sweep --purge-days 30`（30 天前的原片删除）

## 语料库导出（export_all）

每个作品目录只含：`content.md`（正文）、`media-cover.jpg` + `frame-first/mid/last.jpg`（视频类）、`asr.json`（文字稿）、`audio.m4a`（可选复听）、`comments.json`、`meta.json`。**零 mp4**。

## 磁盘参考

310 作品规模：media ~21G（含未归档在制品）+ archive ~10G（原片冷备）+ corpus ~0.4G（最终资产）。
