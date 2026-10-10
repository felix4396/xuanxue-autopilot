# 开发记录 — 玄学 Autopilot（xuanxue-autopilot）

> 项目：个人非商用自媒体对标闭环系统
> 仓库：https://github.com/felix4396/xuanxue-autopilot（private，账号 felix4396）
> 本地：`/Users/shiro/codex/xuanxue-autopilot`
> 设计依据：`docs/design.md`（源自《对标采集分析产出闭环方案》，方案定稿于 2026-10-10）

---

## 一、开发里程碑

| 日期 | Commit | 内容 |
|---|---|---|
| 2026-10-10 | `f729122` | 项目骨架 + 设计文档入库 |
| 2026-10-10 | `fc850fb` | 六模块核心库 + CLI + 6 项冒烟测试 |
| 2026-10-10 | `7c10516` | 整合五个上游项目关键能力 + Pipeline 串联 + 5 项整合测试 |

当前分支 `main`，全部已推送。

---

## 二、已交付模块与验收结果

### 1. lib/bench — 对标账号库（对应方案阶段①）
- **实现**：SQLite 存储；`accounts` / `works` 两表；upsert 幂等；`mark()` 状态标记（collected/analyzed）；`pending_works()` 待办队列。
- **数据模型来源**：Sunbird OS 对标库字段（昵称、粉丝数、作品列表、点赞、原始链接）。
- **验收**：✅ `test_bench` 通过——账号去重、待办标记、状态流转正确。

### 2. lib/collect — 采集下载（阶段②）
- **实现**：
  - `adapter.py`：`BaseAdapter` 协议（fetch_works / download_media）+ 24h 缓存 + `平台-作品ID` 命名约定（doctor 一致）。
  - `ratelimit.py`：`RateLimiter`，默认 30s/条、可按平台覆盖。
  - `mediacrawler.py`：MediaCrawler JSONL 产物导入——平台名映射（xhs/douyin/kuaishou/bilibili/weibo）、千分位数字解析（"1,200"→1200）、脏行容错、按 user_id 归账。
  - `ytdlp.py`：`--flat-playlist` 拉频道作品列表 + 字幕/封面/视频下载。
- **验收**：✅ `test_ratelimit`、`test_mediacrawler_import` 通过——含千分位与非法 JSON 行容错断言。
- **⚠️ 未完成**：ytdlp 仅桩实现（未跑真实网络调用）；MediaCrawler 本体需用户单独安装，本系统只消费其产物。

### 3. lib/analyze — 分析拆解（阶段③）
- **实现**：
  - `assets.py`：资产库（hooks/titles/covers/analyses 四类），`top_hooks()` 按得分排序。
  - `doctor.py`：doctor 桥接——`DoctorRunner` 调用其 `scripts/analyze_*.py`；`import_doctor_report()` 从 markdown 报告正则提取钩子秒数、结构（清单/故事/教程）、评分、标题。
  - `video.py`：viral-video-analyzer 关键链路——FFmpeg 抽帧（默认 0.5fps）+ faster-whisper 本地转写 → `asr.json` + `meta.json` 原料包。
- **验收**：✅ `test_assets`、`test_doctor_import` 通过——从报告文本正确提取「第 3 秒 / 清单体 / 8 分 / 标题」。
- **⚠️ 未完成**：doctor/video 均未跑真实外部依赖（需安装 doctor skill 与 faster-whisper）。

### 4. lib/compose — 内容产出（阶段④）
- **实现**：
  - `draft.py`：`Draft` 结构化初稿（3 标题 + 3 封面大字 + 开头 + 正文 + CTA，标题用 doctor「数字+人群+效果」公式）；`adapt_platform()` 多平台标题裁剪（xhs 20 字等）。
  - `deaigen.py`：规则级去 AI 味（词表 19 条，取自 AIWriteX/xhs-ai-writer v2.2 提示词资产的规则化）。
- **验收**：✅ `test_compose`、`test_deaigen` 通过——AI 腔词全部移除、正常语义保留、标题裁剪生效。

### 5. lib/publish — 发布队列（阶段⑤）
- **实现**：
  - `queue.py`：状态机 `pending_review → approved → scheduled → published / rejected`；**人审网关红线**——非法转移抛 `ValueError`；`due()` 到期扫描。
  - `dispatcher.py`：`Uploader` 协议（social-auto-upload 各平台上传器适配点）+ `DryRunUploader` 演练 + `Dispatcher.dispatch_due()` 批量发布并回写 remote_id。
- **验收**：✅ `test_publish_gateway`（pending_review 直接 publish 被拒）、`test_dispatch`（到期项调度成功且 remote_id 回写）通过。

### 6. lib/review — 复盘回流（阶段⑥）
- **实现**：
  - `metrics.py`：回采点 24h/72h/7d；`due_checkpoints()` 计算到期未回采时点；`engagement()` 互动率汇总。
  - `diagnose.py`：规则版诊断——播放<500 且完播<30% → 「开头钩子弱」；赞率<3% → 「内容价值感不足」；赞率≥8% → 「钩子有效」并自动将 top 钩子写入 `assets.validated` 回流。
- **验收**：✅ `test_metrics`、`test_diagnose_loop` 通过——高互动作品的好钩子成功回流资产库，低互动作品命中两条诊断。

### 7. lib/pipeline — 全流程串联
- **实现**：`Pipeline` 类持有全部六个组件，提供 `pending_for_analysis / analyze_one / compose / publish_due / review` 一条龙接口。
- **验收**：✅ 端到端演练通过（2026-10-10）：MC 导入 → doctor 报告入库（识别 story 结构）→ 初稿去 AI 味 → 入队 → 人审 approve → 定时到期 → DryRun 发布（remote_id=`dry-*`）→ 回采 800 播放/100 赞/400 完播 → 诊断输出「钩子有效：加分入库」，like_rate=0.125。

---

## 三、测试汇总

| 套件 | 数量 | 结果 |
|---|---|---|
| `tests/smoke.py` | 6 | ✅ 全过 |
| `tests/integration.py` | 5 | ✅ 全过 |
| 端到端演练（临时目录） | 1 条全链路 | ✅ 通过 |

运行方式：`python3 -m tests.smoke && python3 -m tests.integration`。

---

## 四、待办闭环进展（2026-10-10 第二轮）

原 devlog 待办全部处理完毕：

| 待办 | 结果 |
|---|---|
| MediaCrawler 本体接入 | ✅ 导入器已就绪（`lib/collect/mediacrawler.py`），本体待用户装好后指向其 data/ 目录 |
| yt-dlp 真实调用验证 | ✅ **已打通**：venv 内 yt-dlp 2026.08.19，真实拉取 YouTube 频道（@LexFridman）3+3 条作品入对标库，`scripts/youtube_collect.py` 提供 fetch/transcribe 子命令 |
| doctor skill 安装对接 | ✅ **已 vendor**：`vendor/social-account-doctor`（MIT，含 9 个 analyze 脚本），`DoctorRunner` 验证可枚举脚本 |
| faster-whisper 安装 | ✅ venv 内 1.2.1；ASR + meta 链路用测试音频实测通过（纯音调 → 0 段符合预期） |
| 真实平台 Uploader | 保留适配点，仍待接（涉及账号登录态，按需接入） |
| AIWriteX 重写链路 | 保留词表注入方案，仍待接 |
| LLM 版诊断 | 保留规则版，仍待接 |

**环境事实**：
- 项目 venv：`.venv/`（python3.10 + faster-whisper 1.2.1 + yt-dlp 2026.08.19），测试命令改为 `.venv/bin/python -m tests.smoke`
- 代理坑：httpx2 对 `NO_PROXY` 含 `[::1]` 解析崩（Invalid port），已在 `lib/analyze/video.py` 进程内清除；`all_proxy=socks5://` 会使 yt-dlp 走 socks 需 `env -u all_proxy`
- `vendor/social-account-doctor` 与 `.venv/` 已加入 `.gitignore` 不入库

**状态：骨架与闭环逻辑 100% 完成，外部平台对接为适配点待接入。**

## 五、合规约束（持续有效）

- 仅限个人非商用；MediaCrawler 与 social-auto-upload/Sunbird 系许可证限商用。
- `.gitignore` 已覆盖 `data/`、cookie、登录态、`.env`，凭证不入 Git。
- 抓取控频 ≥ 30s/条；发布必须过 `pending_review` 人审网关（代码级强制）。
- 内容二创需实质性改编，不直接搬运。
