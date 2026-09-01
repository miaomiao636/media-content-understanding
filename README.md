# Media Content Understanding

[![test](https://github.com/miaomiao636/media-content-understanding/actions/workflows/test.yml/badge.svg)](https://github.com/miaomiao636/media-content-understanding/actions/workflows/test.yml)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

一个面向 Agent Skills 客户端的开源 Skill：读取公开的抖音和哔哩哔哩链接，对视频提取字幕或执行 ASR，对抖音长文本和图集分层保留作者正文、图片 OCR 与视觉推断，生成精炼、可审计的媒体理解包。

它不负责 Obsidian 入库，也不用于绕过登录、付费、私密、DRM 或平台访问控制。

## 功能

- 抖音和哔哩哔哩链接识别、短链解析与元数据获取。
- 抖音 `/note/` 长文本、纯图集和图文混合内容路由，保留图片原始顺序。
- 公开 `iesdouyin.com/share/note/<id>/` 入口会窄范围规范化为作品页；`v.douyin.com` 短链只在解析到具体 `/note/` 或 `/video/` 时继续，用户主页或其他落地页会被保守拒绝。
- 作者正文、图片 OCR、视觉推断和 Agent/自动摘要四层溯源；无音视频时不生成转写或时间轴。
- 图文包的图片分析仍含“尚未校订”占位内容时，`mcu finalize` 会保持 `partial`，不能只改摘要就绕过视觉复核。
- `yt-dlp` 主获取器，Playwright 真实浏览器可选回退。
- 可选的 Skill 专用持久浏览器档案，首次登录后可跨任务复用抖音会话。
- 平台字幕优先，无字幕时可使用 `faster-whisper`。
- 稀疏故事板、外部视觉模型路由和宿主视觉回退。
- 静态截图与动态短片的最小证据策略。
- 千问、MiMo 及普通 OpenAI 兼容视觉接口。
- macOS、Windows、Linux 配置和安全密钥存储。
- 标准 `media-analysis-package` 输出与验证，同时生成可编辑的 `summary.md` 和可直接浏览的 `summary.html`。

## 安装

推荐使用 Python 3.9+、[uv](https://docs.astral.sh/uv/) 和 FFmpeg：

```bash
git clone https://github.com/miaomiao636/media-content-understanding.git
cd media-content-understanding
uv sync --extra all
uv run playwright install chromium
uv run mcu doctor
```

只需要平台字幕和外部视觉模型时，可以不安装本地 ASR：

```bash
uv sync --extra browser
```

先从 GitHub 页面下载 ZIP 或复制仓库 Git 地址进行克隆。然后将整个仓库目录安装到 Agent 的 Skills 目录，或按照目标 Agent 的说明注册这个 `SKILL.md`。不同客户端的安装目录可能不同，但核心文件夹结构遵循 Agent Skills 规范。

详细说明见 [安装与跨平台配置](references/installation.md)。

## 使用

```bash
uv run mcu analyze "https://v.douyin.com/.../"
uv run mcu analyze "https://www.douyin.com/note/7659275356428852849" --vision none
uv run mcu analyze "https://www.bilibili.com/video/BV..." --focus "提炼操作步骤和关键参数"
```

也可以让支持 Agent Skills 的 Agent 直接使用：

```text
使用 $media-content-understanding 理解这个视频链接，并保留必要的画面证据。
```

结果默认保存到用户文档目录下的“媒体内容提炼”文件夹，可以在配置或命令行中修改。

每个理解包都会保留 `summary.md` 作为可编辑源文档，并生成 `summary.html` 阅读版。双击 HTML 即可在普通浏览器中查看排版、截图和本地短视频播放器，不依赖 Codex、VS Code 或其他 Agent 的 Markdown 预览。修改 Markdown 后运行 `uv run mcu finalize <package_dir>`，或单独运行 `uv run python scripts/package_tool.py render-html <package_dir>` 刷新 HTML。

运行参数按“显式命令行参数 → 用户 `config.json` → 内置默认值”的顺序解析。例如未传 `--asr-model` 时使用 `asr.local_model`，显式传入时则覆盖配置。

如需保留抖音登录状态，在用户配置中明确设置独立的 `acquisition.browser_profile_dir`。它不会读取日常 Chrome。可用 `uv run mcu browser-profile status` 查看，用 `uv run mcu browser-profile reset --yes` 清除。

Skill 内置 CLI 的浏览器回退是 Playwright，不是 Ego Browser。Ego 可由具备该能力的宿主 Agent 作为人工查看或替代访问工具，但公共 Skill 不强制依赖它。Playwright 每次会打开新窗口；配置固定 `browser_profile_dir` 后，这些窗口仍复用同一个专用登录档案。捕获媒体后只取该媒体 URL 适用的 Cookie，跨域重定向会移除 Cookie/认证头，且所有媒体目标必须解析到公开网络地址。

## 配置视觉模型

从 `assets/config.example.json` 创建用户配置，将要使用的 provider 的 `enabled` 设为 `true`。模板保留“千问主模型、MiMo 第二备用”的顺序，但默认全部关闭，也不包含 API Key。不要把 API Key 写进 JSON。

```bash
export MEDIA_CONTENT_CONFIG="/path/to/config.json"
export QWEN_VISION_API_KEY="..."
export MIMO_API_KEY="..."
```

也可以运行 `credential_tool.py` 将密钥保存到 macOS 钥匙串或 Windows/Linux 系统 Keyring。

## 当前边界

- 平台风控会持续变化，任何单一下载器都不能保证永久可用。
- Playwright 不会导入日常 Chrome 或 Ego 的 Cookie；只有用户明确配置专用档案目录时才跨任务保存该 Skill 自己的登录状态。媒体下载只临时复用浏览器判定为适用于对应 URL 的 Cookie。
- 没有字幕、没有本地 ASR、宿主不支持视觉且未配置外部视觉模型时，只能生成部分结果。
- 图集的外部 OCR/视觉分析复用现有 provider 路由、共享调用预算和上传上限。未配置 provider 时，`analyze` 会保留分层占位稿和原序图片，由支持视觉的宿主 Agent 校订。
- 获取和视觉阶段写入磁盘或终端的错误会再次脱敏，移除常见凭据及 URL 查询参数；公共 Bundle 也拒绝大小写变体的凭据、Cookie、Token 和浏览器档案路径。
- B站合集和多分P默认处理链接直接指向的单个视频/分P；批量范围应由用户明确指定。

## 开发与验证

```bash
uv sync --extra dev
uv run pytest
uv run python scripts/self_test.py
uv run python scripts/package_tool.py validate /path/to/package
uv run python scripts/build_skill_bundle.py --verify-existing /path/to/skill-bundle.zip --manifest-in /path/to/skill-bundle-manifest.json
```

## 隐私与版权

`mcu analyze` 的输出包通过验证后，默认删除本次受控临时任务；失败任务按配置保留，方便续跑和排查。`mcu acquire` 会保留其来源文件，但仍受缓存 TTL 和容量策略管理。任何流程都不记录 API Key、Cookie、Authorization 头或签名媒体地址。请只分析你有权访问的内容，并遵守平台条款和适用法律。

安全问题请阅读 [SECURITY.md](SECURITY.md)。

## 架构

```text
media-content-understanding/
├── SKILL.md                 # Agent 触发条件与标准工作流
├── scripts/                 # 获取、字幕/ASR、视觉路由、证据包与清理
├── references/              # 平台回退、错误分类、配置和输出契约
├── assets/config.example.json
├── agents/openai.yaml       # Codex 界面元数据，其他 Agent 可忽略
├── tests/                   # 不联网单元测试
└── pyproject.toml           # 可选 CLI 安装和依赖
```

Skill 是唯一用户入口，Python CLI 是 Skill 随附的确定性执行层。不需要常驻服务、MCP 或数据库。

## 贡献

提交修复前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。平台适配器受网页和风控变化影响，报告问题时请提供脱敏错误类型，不要上传 Cookie、API Key 或带签名的媒体 URL。

## License

Apache-2.0
