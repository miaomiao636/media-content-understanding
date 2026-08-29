# Media Content Understanding

一个面向 Agent Skills 客户端的开源 Skill：读取公开的抖音和哔哩哔哩视频链接，提取字幕或执行 ASR，结合关键画面生成精炼、可审计的媒体理解包。

它不负责 Obsidian 入库，也不用于绕过登录、付费、私密、DRM 或平台访问控制。

## 功能

- 抖音和哔哩哔哩链接识别、短链解析与元数据获取。
- `yt-dlp` 主获取器，Playwright 真实浏览器可选回退。
- 平台字幕优先，无字幕时可使用 `faster-whisper`。
- 稀疏故事板、外部视觉模型路由和宿主视觉回退。
- 静态截图与动态短片的最小证据策略。
- 千问、MiMo 及普通 OpenAI 兼容视觉接口。
- macOS、Windows、Linux 配置和安全密钥存储。
- 标准 `media-analysis-package` 输出与验证。

## 安装

推荐使用 Python 3.9+、[uv](https://docs.astral.sh/uv/) 和 FFmpeg：

```bash
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
uv run mcu analyze "https://www.bilibili.com/video/BV..." --focus "提炼操作步骤和关键参数"
```

也可以让支持 Agent Skills 的 Agent 直接使用：

```text
使用 $media-content-understanding 理解这个视频链接，并保留必要的画面证据。
```

结果默认保存到用户文档目录下的“媒体内容提炼”文件夹，可以在配置或命令行中修改。

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
- Playwright 回退不会自动读取浏览器 Cookie；需要登录态时必须由用户明确配置。
- 没有字幕、没有本地 ASR、宿主不支持视觉且未配置外部视觉模型时，只能生成部分结果。
- B站合集和多分P默认处理链接直接指向的单个视频/分P；批量范围应由用户明确指定。

## 开发与验证

```bash
uv sync --extra dev
uv run pytest
uv run python scripts/self_test.py
uv run python scripts/package_tool.py validate /path/to/package
```

## 隐私与版权

默认不永久保存完整原视频，不记录 API Key、Cookie、Authorization 头或签名媒体地址。请只分析你有权访问的内容，并遵守平台条款和适用法律。

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
