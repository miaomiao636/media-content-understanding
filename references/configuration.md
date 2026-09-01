# 配置

## 配置位置

按以下顺序寻找配置：

1. 命令行 `--config /absolute/path/config.json`。
2. 环境变量 `MEDIA_CONTENT_CONFIG`。
3. macOS/Linux：`~/.config/media-content-understanding/config.json`。
4. Windows：`%APPDATA%\\media-content-understanding\\config.json`。

没有配置文件时使用安全默认值：系统缓存目录、用户文档目录下的持久输出目录、`yt-dlp` 主获取、Playwright 可选回退、无外部视觉模型、允许宿主视觉回退。

运行参数的生效顺序是：显式命令行参数、用户配置、内置默认值。命令行没有传值时才读取 `config.json`；这与上面的“从哪里寻找配置文件”是两个不同层次的优先级。

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
| `acquisition.browser_profile_dir` | 可选的 Skill 专用浏览器档案目录；非空代表用户明确授权 Playwright 跨任务保存登录状态 |
| `acquisition.cookie_browser` | 用户明确授权时传给 `yt-dlp --cookies-from-browser` 的浏览器名；默认空 |
| `acquisition.max_download_mb` | 单个媒体文件的下载大小上限；同时约束 `yt-dlp`、Playwright 下载和浏览器最终合并媒体 |

不得自动探测或读取浏览器 Cookie。配置 `cookie_browser` 视为允许 `yt-dlp` 读取所指浏览器的 Cookie；配置 `browser_profile_dir` 视为允许 Playwright 在独立目录保存本 Skill 自己的登录状态。两者默认均为空。

推荐把 `browser_profile_dir` 放在持久的应用数据目录，不要放入项目、缓存或输出目录。例如 macOS 可使用：

```json
{
  "acquisition": {
    "browser_profile_dir": "/Users/你的用户名/Library/Application Support/media-content-understanding/browser-profile"
  }
}
```

专用档案不会导入或污染日常 Chrome。首次运行时由用户在该窗口完成登录，后续任务复用该档案。查看和清除状态：

```bash
uv run mcu browser-profile status
uv run mcu browser-profile reset
uv run mcu browser-profile reset --yes
```

`reset` 默认只预览；`--yes` 也只会删除同时满足“配置路径、有效管理标记、非项目目录”三项条件的专用档案。专用档案不能与 `temp_root`、`output_root`、用户主目录或文件系统根目录重叠。同一时间只能有一个任务占用同一档案；冲突会报告 `BROWSER_PROFILE_IN_USE`。平台仍可能因会话过期或风控要求重新登录。

Ego Browser 的任务空间与 Playwright 专用档案是两套独立会话。宿主 Agent 可以在具备 Ego 时用它辅助查看页面，但 `mcu analyze` 内置回退不会自动接管 Ego 登录态。Playwright 打开新窗口不代表新档案；只要 `browser_profile_dir` 保持不变且未被清除，后续窗口会继续使用同一专用会话。

## ASR 配置

| 字段 | 含义 |
| --- | --- |
| `asr.mode` | `auto`、`local` 或 `none` |
| `asr.local_model` | faster-whisper 模型名，默认 `small` |
| `asr.language` | 默认转写语言，中文为 `zh` |

## 缓存保留配置

| 字段 | 实际行为 |
| --- | --- |
| `retention.cleanup_on_success` | `analyze` 的输出包验证通过后是否删除本次临时任务 |
| `retention.failed_job_retention_hours` | 失败任务在自动清理前保留的小时数 |
| `retention.cache_ttl_days` | 已完成、未知或遗留任务的通用缓存期限 |
| `retention.max_cache_gb` | 带本 Skill 标记的缓存任务总容量上限；超限时优先清理最旧的非运行任务 |
| `retention.keep_source_media` | 阻止成功任务立即清理，将来源保留在受控缓存；仍受 TTL 和容量策略管理 |

`mcu acquire` 的目标就是交付来源文件，因此即使 `cleanup_on_success=true` 也会保留该任务；后续启动或手工清理仍会应用 TTL 和容量策略。`keep_source_media` 不会把完整原视频复制进持久输出包。

## 视觉模型字段

### 全局视觉字段

| 字段 | 实际行为 |
| --- | --- |
| `vision.max_visual_calls` | 一次 `mcu analyze` 的共享 provider 尝试预算；原生视频转写、重试、故障切换、最终摘要和低置信度复核都计入 |
| `vision.max_upload_mb` | 单次请求中所有本地 Base64 媒体的合计上限；HTTP(S) 公网 URL 不计入本地上传量 |
| `vision.verification_mode` | `none` 或 `low-confidence`；后者只响应结构化 `MCU_CONFIDENCE: low` 标记 |
| `vision.max_frames` | 故事板最多抽取的帧数；最终单次视觉综合当前最多选择前 12 帧 |
| `vision.host_fallback` | 外部链无可用结果时，是否允许宿主 Agent 按 Skill 说明接管视觉检查 |

`max_visual_calls` 采用保守计数：每次进入一个 provider 的调用尝试就扣减一次，包括认证、配置、上传限制或网络错误导致的失败尝试。原生视频转写会至少为最终故事板摘要保留一次尝试；复核使用剩余预算，不能挤占总上限。视觉子进程若未能写出可信的用量报告，主流程会耗尽本次剩余预算，防止错误后继续产生不可控调用。

`max_upload_mb` 与 provider 限制同时生效：全局限制约束一批媒体的 Base64 合计；`max_image_base64_mb` 和 `max_video_base64_mb` 分别约束单个媒体项。实际允许值取两层约束共同满足的范围。

### Provider 字段

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

## 视觉模型选择原则

- 外部视觉模型按优先级先于宿主模型。
- 只选择能力匹配的模型。
- 第一个产生有效结果的模型即为主结果。
- 主流程提示模型在末尾返回 `<!-- MCU_CONFIDENCE: high|medium|low -->`；该标记写入报告，但会从用户正文中移除。
- `verification_mode=low-confidence` 时，只有主结果明确返回 `low` 才调用下一可用模型复核；缺少标记记为 `unknown`，不会仅凭自由文本猜测置信度。
- 第二模型需要基于同一媒体证据输出可直接替换的完整结果；复核失败时保留已成功的主结果，并在报告中记录失败链。
