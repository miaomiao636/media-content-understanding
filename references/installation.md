# 安装与跨平台配置

## 基础要求

- Python 3.9 或更高版本。
- Git。
- FFmpeg 与 FFprobe。
- 互联网访问。
- 支持 Agent Skills 的 Agent 客户端。

推荐使用 `uv` 管理隔离环境和跨平台锁文件。

## 通用安装

```bash
cd media-content-understanding
uv sync --extra all
uv run playwright install chromium
uv run mcu doctor
```

按需安装：

```bash
# 不使用本地 ASR
uv sync --extra browser

# 不使用浏览器回退
uv sync --extra asr

# 仅基础依赖
uv sync
```

## FFmpeg

- macOS：`brew install ffmpeg`
- Windows：`winget install Gyan.FFmpeg`
- Ubuntu/Debian：`sudo apt-get install ffmpeg`

安装后运行 `ffmpeg -version` 和 `ffprobe -version` 验证。Skill 不自动修改系统包管理器。

## 安装到 Agent

将整个 `media-content-understanding` 目录放入目标 Agent 的 Skills 目录，并确保父目录名与 `SKILL.md` 中的 `name` 一致。不同 Agent 的实际 Skills 路径以其官方文档为准。

如果客户端允许从 Git 仓库注册 Skill，可以直接选择仓库根目录。`agents/openai.yaml` 只是 Codex 的界面元数据；其他客户端可以忽略。

## 配置文件

复制 `assets/config.example.json` 到用户配置目录：

- macOS/Linux：`~/.config/media-content-understanding/config.json`
- Windows：`%APPDATA%\media-content-understanding\config.json`

或设置：

```bash
MEDIA_CONTENT_CONFIG=/absolute/path/config.json
```

配置文件只保存非敏感设置。API Key 使用环境变量或系统密钥管理器。

## 可选浏览器回退

```bash
uv sync --extra browser
uv run playwright install chromium
```

默认使用隔离的可见浏览器窗口，不读取个人 Chrome Cookie。抖音在无界面模式下可能只返回数秒预览片段，因此桌面环境建议保持 `acquisition.browser_headless: false`。服务器或 CI 可设为 `true`，但若完整性校验报错，应改用可见浏览器或用户主动提供本地媒体。

需要跨任务保留抖音登录时，可由用户明确配置 `acquisition.browser_profile_dir`。它是本 Skill 的独立浏览器档案，不会导入或污染日常 Chrome。第一次在该窗口登录后，后续任务可以复用；平台会话过期时仍可能要求重新登录。用 `mcu browser-profile status` 查看状态，用 `mcu browser-profile reset --yes` 清除。公共自动化和 CI 不应启用个人 Cookie 或持久登录档案。

## 可选本地 ASR

```bash
uv sync --extra asr
```

第一次使用会下载 Whisper 模型。默认模型为 `small`，可在配置中改为 `tiny`、`base`、`medium` 等。模型越大，质量通常越高，但占用更多内存和时间。

## 发布前检查

```bash
uv run mcu doctor
uv run pytest
uv run python scripts/self_test.py
```
