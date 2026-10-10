# 自媒体对标账号「采集 → 下载 → 拆解 → 产出 → 发布 → 复盘」流程闭环方案设计

> 结论先行：GitHub 上没有一个开源项目完整覆盖全链路。最优组合是
> **MediaCrawler（采集底座） + social-account-doctor（诊断拆解大脑） + yt-dlp / viral-video-analyzer（下载与视频级拆解） + AIWriteX / social-auto-upload 系（产出与发布）**。
> **（个人非商用前提）** Sunbird OS 也可直接纳入组合：**Sunbird OS（对标库 + 拆解 + 复盘 + 多账号发布，桌面工作台） + social-account-doctor（深度诊断大脑） + MediaCrawler / yt-dlp（补充采集） + AIWriteX（规模化产出）**。

---

## 一、候选项目综合评估

评估维度：活跃度（pushedAt / commits）、issue 健康度、许可证、闭环覆盖度、技术可拼装性。

| 项目 | ⭐ | 最后推送 | 维护状态 | 许可证 | Issue/风险 | 评估结论 |
|---|---|---|---|---|---|---|
| [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 66.5k | 2026-10-09 | **极活跃** | 自定义（禁商用） | 上游反爬加剧，作者自己都建议降级为最后手段 | ✅ 采集层事实标准，但反爬压力上升，需配合登录态隔离与限速 |
| [JuneYaooo/social-account-doctor](https://github.com/JuneYaooo/social-account-doctor) | 281 | 2026-09-19 | 活跃，提交质量高 | MIT ✅ | 无阻塞 issue；README 明确数据源优先级（agent 浏览器 > opencli > TikHub 付费 > mc 扫码） | ✅ **核心推荐**。赛道对标扫描、六维爆款拆解、仿写初稿一应俱全，MIT 允许自由改造 |
| [niupTang/douyin（Sunbird OS）](https://github.com/niupTang/douyin) | 100 | 2026-10-05 | 活跃 | ⚠️ NC 非商业（个人使用无碍） | 闭环最完整（对标库→拆解→复盘→发布→Codex 接入），支持抖音/视频号/B站/小红书/快手/百家号/TikTok | ✅ **个人用直接整用**，作为主工作台；仅商用需作者授权 |
| [jidouqie/redbeacon](https://github.com/jidouqie/redbeacon) | 258 | 2026-09-07 | ⚠️ 仓库已转型为安装入口，核心源码**不再在 GitHub 发布** | Other | Issue #3：官网域名解析失败 | ❌ 闭源产品，不可拼装，放弃 |
| [EBOLABOY/xhs-ai-writer](https://github.com/EBOLABOY/xhs-ai-writer) | 329 | 2026-02-22 | **停滞 7 个月+** | 无许可证 | 2 个 High 安全告警未修（#4）、cookie 报错（#1/#3） | ❌ 代码可借鉴其提示词（AIGC 率 <10% 的 v2.2），但不要直接部署 |
| [iniwap/AIWriteX](https://github.com/iniwap/AIWriteX) | 2k | 2026-09-24 | 活跃（V2.5.1） | Apache-2.0 ✅ | peewee 兼容性 issue（#40）等小问题 | ✅ 产出/发布层推荐：选题→生成→配图→去AI味→多平台发布 |
| [pelpeljakob-creator/viral-video-analyzer](https://github.com/pelpeljakob-creator/viral-video-analyzer) | 140 | 2026-03-30 | 半停滞，一次成型 | 无 | 架构清晰（FFmpeg 抽帧 + faster-whisper 本地 ASR + Vision API） | ✅ 视频级拆解组件值得抽取，或由 social-account-doctor 的 analyze_video.py 替代 |
| [pengyueren/Douyin-Account-Analysis-Skill](https://github.com/pengyueren/Douyin-Account-Analysis-Skill) | 23 | - | 小体量 skill | - | - | 备选，功能被 social-account-doctor 覆盖 |

**取优合并方案（个人非商用版）**：
- **主工作台**：Sunbird OS —— 对标账号库、爆款拆解、机会洞察、作品复盘、多账号管理、发布中心、Codex 工具链，一站承接「采集→拆解→复盘→发布」主干
- **深度诊断大脑**：social-account-doctor —— Sunbird 拆解偏"能用什么"，doctor 补齐六维评分、封面逐张体检、带货脚本等深度分析（MIT，独立 CLI 可用）
- **采集补充**：MediaCrawler（国内多平台深抓正文/评论）+ yt-dlp（YouTube 字幕/封面/视频）
- **视频精拆（可选）**：viral-video-analyzer 的抽帧/ASR 链路
- **规模化产出**：AIWriteX —— 扩写、AI 配图、去 AI 味、多平台格式适配

---

## 二、流程闭环设计

```
┌──────────────────────────────────────────────────────────────────┐
│  ① 找号对标        ② 批量采集/下载      ③ 分析拆解               │
│  Sunbird OS        MediaCrawler/yt-dlp  Sunbird 拆解(能用什么)    │
│  贴主页建对标库 →  增量同步+媒体落盘 →  + doctor 深拆(为什么爆)   │
│        ↑                    ↓                     ↓              │
│  ⑥ 复盘迭代      ⑤ 多平台发布       ④ 内容产出                   │
│  Sunbird 回流诊断 ← Sunbird 发布中心 ← 仿写初稿+AIWriteX 扩写配图  │
└──────────────────────────────────────────────────────────────────┘
```

### 阶段 ①：找号与对标库建立
- 输入：赛道关键词、自身账号定位。
- **主路径**：Sunbird OS 直接贴对标账号主页链接，自动同步账号资料、粉丝数、作品列表（标题/封面/点赞/链接），沉淀为持续更新的对标内容库。
- **补充路径**：MediaCrawler 按关键词搜索各平台内容，按互动率（赞藏比、评论数）排序，人工/LLM 筛出 5-10 个对标账号，导入对标库。
- 沉淀字段（Sunbird OS 数据模型）：`对标账号 → 平台 → 账号ID → 昵称/粉丝数/作品列表 → 同步时间`。

### 阶段 ②：批量采集与下载
- **国内平台**（小红书/抖音/快手/B站/微博）：MediaCrawler 按账号主页增量抓取作品列表 + 正文 + 评论 + 媒体文件。
- **YouTube**：`yt-dlp --write-sub --write-auto-sub --write-thumbnail` 批量下载字幕/封面/视频。
- 媒体按 `平台-作品ID` 命名落盘（social-account-doctor 的约定），24h 缓存避免重复抓取。
- **风控要求**：每平台独立浏览器 profile 隔离登录态；抓取间隔 ≥ 30s/条；单账号日抓取量设上限；优先用 agent 浏览器/TikHub 付费 API 代替自有账号扫码（social-account-doctor 推荐的数据源优先级）。

### 阶段 ③：分析拆解
- **第一层（Sunbird OS）**：拆作品"在说什么、钩子是什么、结构怎么展开、爆点在哪"；再拆"和我有什么关系"—— 哪些观点可迁移、哪些表达可借鉴、哪些不能照搬。
- **第二层（social-account-doctor）**：深度诊断 —— 账号层「扫同赛道」出对标清单与自号差距；内容层逐条拆钩子第几秒、封面模板、结构、评论区话题；封面逐张体检。
- **视频层（深度可选）**：FFmpeg 抽帧 + faster-whisper 转写 + 多模态模型分析 → 逐秒拆解表。
- 沉淀资产库：`output/assets/` 钩子库、标题库、封面模板库，跨任务累积复用。

### 阶段 ④：内容产出
- 输入：拆解结论（可迁移观点 + 选题）+ 自己的素材。
- Sunbird OS 从对标内容提炼"我能从哪个角度讲"，social-account-doctor 生成仿写初稿（3 标题 + 3 封面大字 + 开头 + CTA）。
- AIWriteX 接力：扩写全文、AI 配图、**去 AI 味处理**（降 AIGC 检测率）、多平台格式适配。
- 人工审核：Sunbird OS 自带素材管理与发布前确认流，所有产出过人审再进发布队列（红线，不做全自动发布）。

### 阶段 ⑤：发布
- **主路径**：Sunbird OS 发布中心 —— 选择素材与账号，单次/批量/定时发布；支持抖音、视频号、B站、小红书、快手、百家号、TikTok；多账号登录态统一隔离管理。
- **补充路径**：图文规模化分发走 AIWriteX 内置发布链路。
- 发布前先用测试账号验证各平台自动化流程（Sunbird README 明确建议）。

### 阶段 ⑥：复盘与数据回流（闭环关键）
- 登录自有账号后，Sunbird OS 一键同步已发布作品数据（播放、赞、完播、跳出），对账号或单条作品做流量诊断：问题出在选题、开头、结构还是账号状态。
- social-account-doctor「我这条为什么不爆」深度诊断 → 结论写回对标库与钩子库。
- 每周用复盘结论刷新：对标账号列表（淘汰衰减号）、爆款公式（更新钩子库）、选题方向。

---

## 三、落地建议

1. **最小可用版（第 1 周）**：直接部署 Sunbird OS（对标库 + 拆解 + 复盘 + 发布一站搞定），叠一个 social-account-doctor 做深度诊断。它自带 analyze_image/video/document 脚本，可脱离 agent 独立 CLI 调用。
2. **进阶版（第 2-4 周）**：接入 AIWriteX 做规模化产出；YouTube 线用 yt-dlp；写一个同步脚本把 MediaCrawler 的 JSON 喂给 Sunbird 对标库或 doctor 的缓存目录；配置 Sunbird 的 Codex 工具链，让 AI 直接调"查对标→拆解→发布"。
3. **合规注意（个人非商用版）**：
   - MediaCrawler、social-auto-upload/Sunbird OS 为**禁/限商用许可证**——个人私下使用无碍，一旦商业化需授权或换 MIT/Apache 项目（doctor、AIWriteX）替代；
   - 平台 ToS 风险依然存在：逆向接口抓取与自动发布均违反多数平台条款，控制频率、隔离账号、内容二创需实质性改编，避免直接搬运侵权。
