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

## 六、全流程实测（2026-10-10 第三轮，登录态接入后）

用真实登录态浏览器 + 真实数据跑通六阶段，**未触发任何真实发布**。

### 实测记录

| 阶段 | 实测内容 | 结果 |
|---|---|---|
| ①② 采集 | yt-dlp 真实拉取 @lexfridman shorts 8 条；下载 `UbOjufRmpq4`（480p 含音轨 1.1MB）落盘 | ✅ |
| ③ 抽帧 | FFmpeg 0.5fps → 58 张关键帧 | ✅ |
| ③ ASR | faster-whisper small 离线模式 → 24 段英文逐字稿，内容准确（COBOL 话题与视频相符） | ✅ |
| ③ 拆解入库 | doctor 报告解析 → 钩子第4秒/故事体/8分/标题入资产库；作品标记 analyzed | ✅ |
| ④ 产出 | 基于拆解自动产出小红书初稿（含去AI味），入人审队列 | ✅ |
| ⑤ 发布演练 | 人审 approve → 定时到期 → DryRunUploader 演练（remote_id=`dry-*`，**未真实发布**） | ✅ |
| ⑥ 复盘回流 | 回采 800播放/90赞/380完播 → 诊断「钩子有效：加分入库」，like_rate=0.1125；好钩子写入 validated | ✅ |

### 实测中发现并修复的问题

1. **yt-dlp 选流无音轨**：`-f "mp4[height<=480]"` 选到无声流 → ASR 报 IndexError。改为 `bv*+ba/b` 合流后音轨正常（opus）。
2. **whisper 模型下载三连坑**：httpx2 与代理环境变量（NO_PROXY `[::1]`、socks5）冲突导致 HF 下载全部失败 → 用 curl 从 hf-mirror.com 直拉 5 个模型文件到 HF 缓存目录 + `HF_HUB_OFFLINE=1` 离线运行解决。
3. **DryRunUploader 类型 bug**：不接受 str 路径 → 已修（commit f4e377e 后追加修复）。
4. **小红书用户主页风控**：直接访问 /user/profile/ 触发「安全限制 300012」（IP 风险）；首页与 explore 正常。**对标账号抓取应优先走 agent 浏览器模拟真人路径，或换 TikHub 付费 API**（与 doctor README 的数据源优先级一致）。
5. **抖音用户页未渲染出作品数据**（无验证码但快照无作品/粉丝字段），需要滚动加载或更长的真人模拟，列入后续接入点。

### 环境依赖结论

- whisper 模型：已缓存 `Systran/faster-whisper-small`（483MB），需 `HF_HUB_OFFLINE=1` 运行
- 网络规则：yt-dlp 需 `env -u all_proxy`（socks 代理会拖慢下载）；HF 下载走 hf-mirror 直连
- 登录态：`data/browser-profile/xhs` 持久 profile，四平台已登录

### 结论

**六阶段全部实测打通。** 真实发布按红线禁用（DryRun 演练替代）；国内平台对标账号抓取受风控限制，按 doctor 推荐优先级，下一步接入 TikHub 付费 API 或浏览器模拟真人路径。

- 仅限个人非商用；MediaCrawler 与 social-auto-upload/Sunbird 系许可证限商用。
- `.gitignore` 已覆盖 `data/`、cookie、登录态、`.env`，凭证不入 Git。
- 抓取控频 ≥ 30s/条；发布必须过 `pending_review` 人审网关（代码级强制）。
- 内容二创需实质性改编，不直接搬运。

## 七、数据采集接口接入（2026-10-10 第四轮）

调研并接入开源采集方案，替代纯接口硬闯路线。

### 选型结论

| 平台 | 选型 | 状态 |
|---|---|---|
| 抖音 | **Evil0ctal/Douyin_TikTok_Download_API v4**（⭐20.6k，自托管 REST，a_bogus 纯 Python 签名） | 服务已部署本机 :8080 并实测（见下） |
| 小红书 | **aki66938/xhs-toolkit**（⭐1.4k，MIT，创作者中心 MCP：Cookie 管理/发布/数据看板） | 已安装 + cookie 格式打通；发布与数据中心能力待用户登录创作者中心（短信验证码）后解锁 |
| YouTube | yt-dlp（已打通） | 不变 |
| 备选 | TikHub 付费 API（doctor 作者推荐，一 key 通吃全平台，零风控） | 未购买，作为兜底路线 |

### 实测记录

**抖音 dtk 服务**：
- v4 单机部署 `/tmp/dtk_v4`（pip 装依赖 + `python start.py`），端口 8080，OpenAPI 文档 `/docs` 可用
- 浏览器登录态 cookie（58 条）+ 真实 Chrome UA 已注入 `crawlers/douyin/web/config.yaml`
- 实测 `fetch_user_post_videos` 返回 403：抖音对服务端签名（a_bogus 与指纹绑定）校验比浏览器严格，开源方案常见问题，README 的 v5"身份池"即为此设计
- **结论**：接口路径打通，但需要身份池轮换或 TikHub 才能稳定跑。适配器代码已写好：`lib/collect/douyin_api.py`

**小红书 xhs-toolkit**：
- venv 安装成功，`CookieManager.validate_cookies()` 用我们导出的 11 条 cookie 验证通过
- playwright 导出的 cookie → toolkit JSON 格式转换脚本已验证
- 局限如实记录：toolkit 覆盖**创作者中心**（自己账号的发布/数据看板），不覆盖对标账号采集
- 对标采集新增 DOM 适配器：`lib/collect/xhs_dom.py`（持久登录态浏览器解析，控频 45s）

**小红书 IP 风控**：本次实测中搜索页/explore/首页相继触发「安全限制 300012」（IP 维度），系短时间高频自动化操作所致。风控冷却期约数小时；期间所有小红书采集暂停。**教训：自动化节奏必须走 RateLimiter，人改一次要等很久。**

### 新增适配器

- `lib/collect/douyin_api.py` — DouyinApiAdapter：对接 dtk 服务（fetch_works/fetch_detail/download_media）
- `lib/collect/xhs_dom.py` — XhsDomAdapter：浏览器 DOM 采集（fetch_works 搜索流/download_media 封面），视频下载留待 MediaCrawler 下载器接入

两个适配器均通过 `BaseAdapter` 抽象，`get_adapter()` 工厂可直接注册；导入验证通过，全量回归（6+5 tests）通过。

### 当前采集链路状态

```
YouTube:  yt-dlp ────────────────── 已打通（实测）
抖音:     dtk 服务 ── 需身份池/TikHub ─ 接口通，数据待通
小红书:   浏览器 DOM ── 需 IP 风控冷却 ─ 代码就绪，等冷却
```

- 仅限个人非商用；MediaCrawler 与 social-auto-upload/Sunbird 系许可证限商用。
- `.gitignore` 已覆盖 `data/`、cookie、登录态、`.env`，凭证不入 Git。
- 抓取控频 ≥ 30s/条；发布必须过 `pending_review` 人审网关（代码级强制）。
- 内容二创需实质性改编，不直接搬运。

## 八、密钥接入与 LLM 环节打通（2026-10-10 第五轮）

用户提供 LLM 端点与 TikHub key，六个缺口按优先级处理。

### 用户决策记录

- **发布能力不接**（缺口#4 #5 关闭）：人审网关保留为 DryRun 演练，真实发布器不做
- LLM key + TikHub key 提供（TikHub 免费额度，要求节约使用）
- MediaCrawler 授权安装

### 完成项

| 缺口 | 结果 |
|---|---|
| #2 LLM 深度拆解 | ✅ `lib/llm.py`（OpenAI 兼容客户端，.env 读 key）+ `lib/analyze/llm_analyze.py`。实测：COBOL 视频逐字稿 → 结构化拆解（钩子/观点体/8分/仿写标题/可迁移观点/爆款原因），端到端入资产库（STRUCT_MAP 补「观点体」→opinion） |
| #3 LLM 扩写 | ✅ `lib/compose/llm_expand.py`。实测：模板初稿 → 成稿（3 标题+口语化正文+CTA），去 AI 味词表写进 system prompt。产出质量显著高于模板拼接 |
| #6 MediaCrawler 本体 | ✅ vendor 进项目，uv sync（Python 3.11.12 独立 venv），CLI 验证可运行；导入器对接其 `data/` 目录空跑通过 |

### TikHub 实测结论（重要）

- key 有效，70 组端点可用，账户名「自媒体」
- **免费额度实际不可用于数据端点**：`allow_free_credit=1` 的标记与实际不符，实测 `douyin/search`、`douyin/app/v3/fetch_user_post_videos`、`xiaohongshu/app_v2/search_notes` 全部返回 **402 Insufficient balance**（"does not accept free credit"）
- 实际消耗：仅用户搜索端点成功计费过一次（0.001 美元级别），拿到了 25 个玄学对标账号（含「云溪爱玄学」17.5万粉/2049 作品、「玄学机长」13.6万粉/733 作品等，sec_user_id 已留存于会话记录）
- **结论：采集要跑通需充值**（或走已就绪的浏览器 DOM 路线等 IP 冷却）。适配器代码均已就绪

### 环境新增

- `.env`（gitignore 排除）：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / TIKHUB_API_KEY
- `lib/llm.py` 依赖 python-dotenv
- `vendor/MediaCrawler/`（uv 独立环境 `.venv`，Python 3.11.12）

### 缺口状态更新

| # | 缺口 | 状态 |
|---|---|---|
| 1 | 对标采集数据 | ⏳ TikHub 需充值；或小红书 IP 冷却后走 DOM 路线 |
| 2 | LLM 拆解 | ✅ 完成 |
| 3 | LLM 扩写 | ✅ 完成 |
| 4 | 发布能力 | 用户明确不接，关闭 |
| 5 | 真实发布器 | 用户明确不接，关闭 |
| 6 | MediaCrawler | ✅ 完成（等首次真实爬取产生 data/ 即入对标库） |

- 仅限个人非商用；MediaCrawler 与 social-auto-upload/Sunbird 系许可证限商用。
- `.gitignore` 已覆盖 `data/`、cookie、登录态、`.env`，凭证不入 Git。
- 抓取控频 ≥ 30s/条（小红书 45s）；发布必须过 `pending_review` 人审网关（代码级强制）。
- 内容二创需实质性改编，不直接搬运。

## 九、MediaCrawler 真实爬取打通（2026-10-11 第六轮）

用户决策：TikHub 降级为备选（免费额度不可用），MediaCrawler 为首选。跑通 B 站全链路。

### 实测结果

| 平台 | 结果 |
|---|---|
| **B站** | ✅ **完整跑通**：cookie 登录 → 搜索「玄学」→ 19 条作品 + 19 个账号入对标库（头部：120分钟玄学秘术合集 13.2万赞、倪海厦易经 7.8万赞）→ 取 2.7万赞爆款「算命真的准吗」→ yt-dlp 下载 69MB → 抽帧 358 张 + ASR 143 段 → LLM 拆解（观点体/钩子分）→ 入资产库 → 出初稿 → LLM 扩写成稿（标题/开头/正文/CTA 全齐）|
| 抖音 | ✅ **第七轮突破（抓包+逆向验证）：页面内 fetch 方案成立**。外部直调接口不可行（搜索返回 verify_check；post 接口 403 ArgusSecurityPlugin——签名与 TLS/JS环境强绑定，离浏览器无法伪造）。正解：CDP 在 douyin.com 页面上下文发 fetch(credentials:include)，页面 JS 拦截器自动注入全套签名，服务端全放行。实测：云溪爱玄学 18 条作品（赞/评/无水印视频直链）→ 入对标库 → 视频下载 6.3MB → ASR → LLM 拆解 → 成稿。**抖音线全闭环**。进一步突破：搜索接口在**搜索页上下文**内 fetch 也放行（13条结果含对标账号sec_uid），post 接口必须在**该用户主页上下文**发。至此抖音搜索→选号→全作品→下载 全免费闭环，TikHub 无需充值 |
| 抖音旧结论 | ~~cookie 登录失败~~ 实为 MediaCrawler 登录检查机制不适配新版抖音（localStorage 判定超时），且 COOKIES 共享变量被 B站覆盖过——已建 `scripts/mc_cookie.sh` 按平台切换 |
| 小红书 | 待 IP 风控冷却（MediaCrawler 支持 xhs 平台；DouyinDomAdapter 的 CDP 模式可复用到 xhs） |

### 过程中修复

1. **httpx2 NO_PROXY [::1] bug 再现**：MediaCrawler 环境同样中招。标准跑法：`env -u all_proxy -u ALL_PROXY -u http_proxy -u https_proxy -u NO_PROXY -u no_proxy NO_PROXY="" .venv/bin/python main.py ...`
2. **导入器字段映射**：MediaCrawler jsonl 不写 platform 字段 → 改为从文件路径推断（`data/bili/jsonl/`）；评论文件（`search_comments_*`）跳过不导，避免污染对标库

### MediaCrawler 使用手册（项目内）

```bash
cd vendor/MediaCrawler
# B站搜索采集（cookie 已配置在 config/base_config.py 的 COOKIES）
env -u all_proxy -u ALL_PROXY -u http_proxy -u https_proxy -u NO_PROXY -u no_proxy NO_PROXY="" \
  .venv/bin/python main.py --platform bili --type search --keywords "玄学" --start 1
# 产出 → 入对标库
cd ../.. && .venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from lib.collect.mediacrawler import import_mediacrawler_dir
from lib.bench.store import BenchStore
print(import_mediacrawler_dir('vendor/MediaCrawler/data', BenchStore('data/bench.db')))"
```

### 闭环状态

**B站线已实现全闭环**：采集(MC) → 入库 → 下载(yt-dlp) → 原料包(抽帧+ASR) → LLM拆解 → 资产库 → 初稿 → LLM扩写 → 人审队列。剩余：抖音需重新登录 cookie，小红书等风控冷却。

- 仅限个人非商用；MediaCrawler 与 social-auto-upload/Sunbird 系许可证限商用。
- `.gitignore` 已覆盖 `data/`、cookie、登录态、`.env`、key，凭证不入 Git。
- 抓取控频 ≥ 30s/条（小红书 45s）；发布必须过 `pending_review` 人审网关（代码级强制）。
- 内容二创需实质性改编，不直接搬运。
- TikHub 免费额度稀缺：禁止批量/循环调用数据端点，单次调用前须人工确认。

## 合规约束（持续有效）

- 仅限个人非商用；MediaCrawler 与 social-auto-upload/Sunbird 系许可证限商用。
- `.gitignore` 已覆盖 `data/`、cookie、登录态、`.env`、key，凭证不入 Git。
- 抓取控频 ≥ 30s/条（小红书 45s）；发布必须过 `pending_review` 人审网关（代码级强制）。
- 内容二创需实质性改编，不直接搬运。
