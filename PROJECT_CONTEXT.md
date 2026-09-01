# 项目上下文

> 最近核对：2026-09-01
> 项目根目录：以当前 Git 检出目录为准
> GitHub：`https://github.com/miaomiao636/media-content-understanding`
> 最新 Git 标签与公开 Release：`v0.2.2`
> 当前工作区：`v0.3.0-rc.1` 候选已完成最新本地整合修复与 212 项回归；FINAL-005 真实图文端到端通过；候选代码已推送，远程 CI 首轮发现的干净 Runner/Windows ZIP 路径问题正在修复，尚未打标签或发布

## 项目定位

`media-content-understanding` 是一个可移植的 Agent Skill 和 Python 命令行工具，用于读取用户有权访问的公开抖音图文/视频和哔哩哔哩视频链接，获取文本、媒体与字幕，在需要时执行 ASR 和视觉理解，最终生成带文字、时间轴或画面证据的 `media-analysis-package`。

本项目是整个“Agent 技能知识库”设想中的第一层能力，只负责媒体内容读取、理解和提炼。它明确不负责：

- Obsidian 入库、分类和知识库目录管理。
- 把每个视频自动封装成新的 Skill。
- 单纯下载视频或普通视频剪辑。
- 绕过验证码、登录、付费、私密、DRM、地域或其他访问控制。

## 目标用户与核心需求

目标用户是希望让支持 Agent Skills 且允许执行本地命令的 Agent 客户端代替自己观看技能类视频的人。

核心需求：

1. 接受抖音或哔哩哔哩公开视频链接，识别平台并解析短链。
2. 优先提取平台字幕；缺失时使用本地 `faster-whisper` 或原生视频视觉模型分段转写。
3. 对“这个效果”“像这样”等必须看画面才能理解的内容执行视觉分析。
4. 保留必要的截图、时间点和可选短片，使结论能够复核。
5. 外部视觉模型按优先级故障切换，每次失败记录错误类型和处理建议。
6. 输出独立、结构化、可验证的媒体理解包，供人或后续 Agent 使用。
7. 能够通过 GitHub 分发，并在 macOS、Windows、Linux 和不同 Agent 客户端中移植。

## 已核实的技术栈

| 层次 | 实际实现 |
| --- | --- |
| Skill 入口 | `SKILL.md`，符合文件夹式 Agent Skill 结构 |
| 确定性执行层 | Python 3.9+，命令行入口 `mcu` |
| 来源获取 | 抖音图文专用适配器；视频使用 `yt-dlp` 主适配器和 Playwright 浏览器回退 |
| 媒体处理 | FFmpeg、FFprobe |
| 字幕与 ASR | VTT/SRT 解析；可选 `faster-whisper` |
| 视觉调用 | OpenAI 兼容 HTTP 接口；内置 `standard`、`qwen-omni`、`xiaomi-mimo` 请求配置 |
| 凭据管理 | 环境变量优先；macOS Keychain；Windows/Linux Python Keyring |
| 配置 | JSON 文件，默认位置由操作系统决定，也可用 `MEDIA_CONTENT_CONFIG` 或 `--config` 指定 |
| 持久输出 | 文件系统中的 `media-analysis-package` |
| 缓存 | 带根标记和任务标记的本地临时目录 |
| 构建与依赖 | `pyproject.toml`、setuptools、`uv.lock` |
| 测试与质量 | pytest、Ruff、离线 `scripts/self_test.py`、GitHub Actions |
| 许可证 | Apache-2.0 |

项目没有前端、常驻后端服务、MCP Server、消息队列或容器部署定义。

## 系统架构

```text
用户 / 宿主 Agent
        │
        ▼
     SKILL.md
        │
        ▼
 mcu doctor / acquire / analyze
        │
        ├── config_loader + credential_store
        ├── SourceRouter（抖音图文专用路由；视频 yt-dlp → Playwright）
        ├── 字幕解析 → faster-whisper → 原生视频视觉转写
        ├── FFmpeg 故事板 / 截图 / 短片工具
        ├── VisionRouter（外部 provider 故障切换）
        └── media-analysis-package 初始化、写入与验证
                  │
                  ▼
            宿主 Agent 视觉复核和最终校订
```

### 核心模块

| 文件 | 职责 |
| --- | --- |
| `scripts/mcu.py` | 统一 CLI；编排获取、转写、故事板、视觉综合、输出与验证 |
| `scripts/source_adapter.py` | URL 白名单、短链解析、`yt-dlp` 与 Playwright 获取、按 URL Cookie 隔离、安全媒体下载与完整性核验 |
| `scripts/douyin_content_adapter.py` | 抖音图文、长文本和混合内容解析，正文/元数据提取与安全图片下载 |
| `scripts/browser_verification.py` | 视频与图文共用的登录/验证码/滑块检测、用户等待和超时边界 |
| `scripts/asr_router.py` | 字幕选择与规范化、本地 ASR |
| `scripts/vision_router.py` | provider 选择、请求适配、重试、故障切换与脱敏错误报告 |
| `scripts/media_tools.py` | FFmpeg 探测、抽帧、短片、故事板和音频提取 |
| `scripts/evidence_selector.py` | 按字幕视觉触发词和场景变化规划关键截图与动态短片 |
| `scripts/package_tool.py` | `media-analysis-package` 1.0 初始化、校验与 finalize 完成门禁 |
| `scripts/sanitization.py` | 获取和视觉错误的共享脱敏边界 |
| `scripts/claim_audit.py` | 数字、金额、百分比、时长、版本和模型/软件名称的结构化事实审计及对象对齐 |
| `scripts/config_loader.py` | 默认配置、深度合并、跨平台路径解析 |
| `scripts/credential_store.py` | 环境变量、Keychain、Keyring 的密钥解析 |
| `scripts/credential_tool.py` | 凭据状态、保存和删除 |
| `scripts/preflight.py` | 只读环境与配置检查 |
| `scripts/cleanup.py` | 只操作带管理标记缓存目录的预览式/显式清理 |

## 数据与数据库

### 数据库配置

经源码、配置和依赖扫描确认：项目没有数据库，也没有 ORM、数据库迁移或数据库连接配置。

### 实际持久化位置

- 用户配置：macOS/Linux 默认为 `~/.config/media-content-understanding/config.json`；Windows 默认为 `%APPDATA%\\media-content-understanding\\config.json`。
- 临时缓存：macOS 默认为 `~/Library/Caches/media-content-understanding`；Linux 默认为 `~/.cache/media-content-understanding`；Windows 使用 `%LOCALAPPDATA%` 下的目录。
- 输出：默认 `~/Documents/媒体内容提炼`。
- 可选浏览器档案：由用户通过 `acquisition.browser_profile_dir` 明确指定，必须与缓存、输出和个人日常浏览器档案分离。
- 密钥：环境变量或操作系统凭据存储，不写入仓库和输出包。
- 输出包：`manifest.json`、`summary.md`、`source-content.md`、`transcript.md`、`errors.json`、`media/images/`、`media/clips/`。

## 对外接口定义

### 统一 CLI

```text
mcu [--config PATH] doctor
mcu [--config PATH] browser-profile status
mcu [--config PATH] browser-profile reset [--yes]
mcu [--config PATH] acquire URL [--work-dir PATH]
mcu [--config PATH] analyze URL
    [--focus TEXT] [--output-root PATH]
    [--asr auto|local|none] [--asr-model NAME] [--language CODE]
    [--vision auto|none]
    [--storyboard-interval SECONDS] [--max-frames COUNT]
mcu [--config PATH] finalize PACKAGE_DIR
```

主要退出码：

- `0`：命令成功，或输出包通过结构校验。
- `2`：配置、输入、依赖、获取或运行错误。
- `3`：`analyze` 已生成输出，但输出包校验未通过。

### 视觉路由接口

`scripts/vision_router.py` 接受单图、多图、本地视频或公网视频 URL。外部服务使用配置的 `base_url + endpoint_path`，默认接口路径为 `/chat/completions`。

视觉路由退出码：

- `0`：外部模型成功。
- `20`：没有启用或能力匹配的外部模型，宿主可接管。
- `21`：外部模型均失败，宿主可按配置接管。
- `2`：输入或配置本身无效。

### 输出包契约

- `schema_version`：`1.0`。
- `package_type`：`media-analysis-package`。
- 状态集合：`initialized`、`partial`、`completed`、`failed_acquisition`、`failed_visual`。
- 当前 `mcu analyze` 先写入 `partial`；`mcu finalize` 只有在摘要、结构、必要视觉证据、图文图片层校订和严重事实冲突门禁全部通过时，才原子写入 `completed`，否则保持 `partial` 并列出阻断项。
- 媒体证据必须位于包内，包含路径、类型、时间点/范围、保留原因和画面说明。

## 配置与运行事实

- 示例配置中的千问 `qwen3.5-omni-plus` 优先级为 `10`，小米 MiMo `mimo-v2.5` 优先级为 `20`；两者在公开模板中默认关闭。
- 2026-08-29 本机 `mcu doctor` 检查到独立用户配置，两个 provider 都从 macOS Keychain 取得凭据并通过静态检查；本次分析没有读取或输出密钥。
- 本机可用 FFmpeg、FFprobe、`yt-dlp`、Ego Browser 和 Playwright；未安装 `faster-whisper`。
- Ego Browser 目前仅由预检发现并作为宿主替代能力提示，统一来源适配器实际实现的是 `yt-dlp` 与 Playwright，不会直接调用 Ego Browser。
- Ego 任务空间、日常 Chrome 和 Playwright 专用档案是独立会话；CLI 打开新 Playwright 窗口时，只要固定档案路径未改变，仍复用同一专用登录状态。
- 显式 CLI 参数现在覆盖用户配置，用户配置覆盖内置默认值；ASR 模式、模型、语言和故事板帧数已接入该规则。
- 下载大小上限已同时进入 `yt-dlp`、Playwright 候选下载与最终媒体校验。
- 受控任务会记录 `running/completed/failed` 状态；新任务开始前自动执行 TTL 和容量清理。
- 2026-08-29 的真实验收确认：抖音可在 `yt-dlp` Cookie 失败后由可见 Playwright 获取；B站可由 `yt-dlp` 直接获取；两者均能通过千问原生视频理解生成转写和视觉摘要。
- 真实故障切换确认：千问发生非重试型鉴权错误后，路由会记录错误类型与处理建议并调用 MiMo；MiMo 成功返回。
- `vision.max_visual_calls` 现在是一次分析的共享 provider 尝试预算，覆盖原生视频转写、重试、故障切换、摘要和低置信度复核；显式 `--config` 会传入全部视觉子流程。
- `vision.max_upload_mb` 约束单次请求中全部本地 Base64 媒体的合计大小，并与 provider 单项限制共同生效。
- 结构化 `MCU_CONFIDENCE` 标记已接入：只有主结果明确为 `low` 才调用下一 provider 复核，标记从用户正文移除但保留在报告中。
- Playwright 默认调用 `browser.new_context()` 保持一次性隔离；用户明确配置 `acquisition.browser_profile_dir` 后改用专用持久上下文。专用档案与个人 Chrome、缓存和输出目录分离，并带有效管理标记；非空未标记目录和项目目录不会被采用或删除。可用 `mcu browser-profile status/reset` 管理。
- Playwright 媒体候选只携带浏览器判定适用于该 URL 的 Cookie；跨域重定向移除 Cookie/认证头，初始 URL、DNS 和每次重定向均经过公开网络地址检查并使用已验证 IP 连接。
- 本机专用档案已启用并以 `700` 权限保存；连续两次真实抖音获取均取得 310.8 秒完整媒体，第二次没有要求用户重新登录。

## 当前事实、推理与风险

### 事实

- 最新公开 Release `v0.2.2` 只处理视频。
- 当前候选已经实现抖音 `gallery`、`long_text`、`mixed` 来源获取与统一分析编排；FINAL-005 attempt 3 已用当前代码实时完成 `analyze → 校订 → finalize completed`。
- 当前候选已经实现基于字幕触发词和场景变化的关键截图与动态短片计划，并通过真实 FFmpeg 黑盒验收。
- 当前候选已加入 `mcu finalize` 和事实审计。最新整合修复了未校订图片层绕过、ISO 日期/域名/文件误判、多项正确事实互相误判和常见凭据脱敏遗漏；全量 212 项测试通过。
- 最终代码审查还修复了推荐作品 ID 误匹配、获取错误查询串泄露、Bundle 敏感文件名大小写绕过和锁文件漂移。
- 最新 66 文件 Skill ZIP 已从当前源码统一重建，通过 Bundle 漂移校验、Python 3.9 干净解压安装、212 项测试、自测、compileall、CLI 和安全扫描；首轮远程 3×3 OS/Python CI 暴露出干净 Runner 的 offline Python 与 Windows ZIP 路径分隔符问题，当前修复待重跑。
- 外部视觉路由通常在第一个有效 provider 返回后停止；当其最终结构化置信度为 `low` 且仍有预算时，会调用下一可用 provider 基于同一证据复核。
- `analyze` 只有在输出包验证通过后才按配置清理当前任务；失败任务按独立保留时间保存，`acquire` 因需交付来源文件而保留成功任务。

### 推理

- 对外最准确的稳定状态仍是“已发布的公开视频理解 Skill 0.2.2”。工作区是未经最终审核的 `v0.3.0-rc.1` 候选，不能提前宣传为发布能力。
- `partial` 是有意的安全状态；只有显式 `finalize` 通过门禁后才允许进入 `completed`。

### 风险与边界

- 平台页面和风控会变化，真实链接可用性不能由离线测试长期保证。
- 共享预算采用保守 provider 尝试计数，不等同于服务商实际计费次数或金额；真实调用成本仍取决于媒体大小、模型和服务商规则。
- 缓存容量策略不会为满足上限而删除近期 `running` 任务，因此并发任务本身超过上限时会暂时超额，这是有意的安全取舍。
- 外部模型会接收用户提供的媒体证据；用户需确认内容授权、隐私和服务商条款。
- 抖音登录仍受平台风控影响；专用持久档案已实测可跨任务复用，但不能保证平台会话永不过期。
- 自动摘要可能误写数字或把引流视频误当作完整教程；`partial` 包必须由宿主依据转写和画面证据校订后才能进入下游知识库。
- 抖音实时风控会使同一公开图文样本在不同时间返回挑战；实现不得绕过登录或挑战，真实成功收据只证明记录时刻的可用性。
- 候选代码已推送至 `main`；本机仍保留未跟踪的 Agent 交接文件，接手 Agent 不得重置、覆盖、删除或提交这些用户/内部文件。
- 当前候选不可发布：FINAL-005 已通过，FINAL-006/007 在持久化工作流中仍无独立通过报告；Claude Code 仅完成项目级注册验证，真实触发被未登录状态阻断；远程九组合 CI 和完整真实样本矩阵未完成。

## 关键仓库历史

- `c98620b`：首次发布可移植的抖音/B站视频理解 Skill。
- `293c752`：补充公开仓库链接。
- `e8eac20`、`68420e0`：修复 Windows 中文输出和子进程 UTF-8 解码。
- `882a2a9`、`9b61c48`：更新并固定 GitHub Actions 运行版本。
- `v0.2.0`：上一版本标签，指向 `9b61c48`。
- `v0.2.1`：上一发布标签，指向 `15e5af8`；包含配置优先级、下载上限、任务状态、缓存生命周期、专用浏览器登录档案和安全管理标记。
- `v0.2.2`：当前发布标签，指向 `3ce70c7`；包含视觉共享预算、合计上传限制、显式配置传递和结构化低置信度复核。

## 维护规则

- 不依赖某台设备的绝对检出路径；命令应从仓库根目录执行，用户配置使用操作系统标准位置或显式 `--config`。
- 不提交 API Key、Cookie、Authorization 头、签名 URL、完整原视频或本机用户配置。
- 修改平台适配、配置语义或输出契约时，需要同步更新测试、README、相关 `references/` 和本 Context。
- 真实平台验收不进入公共 CI；记录时只保留脱敏错误类型和结论。
