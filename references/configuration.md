# 配置

## 配置位置

按以下顺序寻找配置：

1. 命令行 `--config /absolute/path/config.json`。
2. 环境变量 `MEDIA_CONTENT_CONFIG`。
3. macOS/Linux：`~/.config/media-content-understanding/config.json`。
4. Windows：`%APPDATA%\\media-content-understanding\\config.json`。

没有配置文件时使用安全默认值：系统缓存目录、用户文档目录下的持久输出目录、`yt-dlp` 主获取、Playwright 可选回退、无外部视觉模型、允许宿主视觉回退。

从 `assets/config.example.json` 复制配置结构。API Key 不得写入配置；macOS 使用钥匙串字段，CI 或其他系统使用 `api_key_env`。当两者都存在时，环境变量优先。`base_url` 可以直接写非敏感地址，也可以用 `base_url_env`。

## 密钥持久化

macOS 使用：

```bash
python3 <skill_dir>/scripts/credential_tool.py --config <config.json> status
python3 <skill_dir>/scripts/credential_tool.py --config <config.json> set --provider <id> --gui
```

`set --gui` 通过系统隐藏输入框接收密钥，并经 Security.framework 写入登录钥匙串。脚本只输出是否存在和凭据来源，不输出密钥。删除密钥需要明确执行 `delete --provider <id> --yes`。

Windows/Linux 默认优先使用 `api_key_env` 对应的环境变量。安装 `keyring` 后，`credential_tool.py set` 也可使用系统凭据存储；无桌面 Secret Service 的 Linux 服务器和容器应使用环境变量或 Secret Manager。不得把真实密钥放入 Skill、JSON、日志或测试文件。

## 来源获取配置

| 字段 | 含义 |
| --- | --- |
| `acquisition.browser_fallback` | `yt-dlp` 失败后是否允许 Playwright 回退 |
| `acquisition.browser_headless` | 是否使用无界面浏览器；默认 `false`，因为抖音在无界面模式下可能只返回数秒预览片段 |
| `acquisition.cookie_browser` | 用户明确授权时传给 `yt-dlp --cookies-from-browser` 的浏览器名；默认空 |
| `acquisition.max_download_mb` | 单个媒体任务的下载大小上限 |

不得自动探测或读取浏览器 Cookie。配置 `cookie_browser` 视为用户对该设备本次工作流的明确授权。

## ASR 配置

| 字段 | 含义 |
| --- | --- |
| `asr.mode` | `auto`、`local` 或 `none` |
| `asr.local_model` | faster-whisper 模型名，默认 `small` |
| `asr.language` | 默认转写语言，中文为 `zh` |

## 视觉模型字段

| 字段 | 含义 |
| --- | --- |
| `id` | 当前配置内唯一名称，用于报错和审计 |
| `enabled` | 是否启用 |
| `priority` | 数字越小越优先 |
| `adapter` | 当前实现支持 `openai-compatible` |
| `request_profile` | 请求差异配置：`standard`、`qwen-omni`、`xiaomi-mimo` |
| `model` | 服务商模型 ID |
| `base_url_env` | 保存 Base URL 的环境变量名 |
| `api_key_env` | 保存 API Key 的环境变量名 |
| `api_key_keychain_service` | macOS 钥匙串 service 名称 |
| `api_key_keychain_account` | macOS 钥匙串 account 名称 |
| `capabilities` | `image`、`multi_image`、`video`，可选 `audio` |
| `timeout_seconds` | 单次请求超时 |
| `video_timeout_seconds` | 原生视频请求超时 |
| `max_image_base64_mb` | 图片 Base64 大小限制 |
| `max_video_base64_mb` | 视频 Base64 编码后大小限制 |
| `max_retries` | 可重试错误的额外重试次数 |
| `max_output_tokens` | 视觉说明最大输出量 |

`qwen-omni` 与 `xiaomi-mimo` 支持原生视频，也支持图片和多图。`standard` 仅用于普通 OpenAI 兼容图片接口。供应商字段和限制见 [provider-profiles.md](provider-profiles.md)。

## 宿主视觉

脚本不能代替 Agent 判断自身是否支持原生视觉。`vision_router.py` 只尝试外部模型；退出码为 `20` 或 `21` 时，Agent 根据 `host_fallback` 决定是否使用宿主的图片查看能力。

## 配置优先原则

- 外部视觉模型按优先级先于宿主模型。
- 只选择能力匹配的模型。
- 第一个产生有效结构化结果的模型即为主结果。
- `verification_mode=low-confidence` 时，只在结果不确定或互相矛盾时调用第二模型复核。
