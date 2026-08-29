---
name: media-content-understanding
description: Read and distill public Douyin or Bilibili video links into a concise, evidence-linked media analysis package using platform captions or ASR plus targeted visual analysis, screenshots, and short clips. Use when the user wants to understand a Douyin/Bilibili video without watching it. Do not use for Obsidian ingestion, download-only requests, private/paid media, or ordinary video editing.
license: Apache-2.0
metadata:
  version: "0.2.1"
  supported-platforms: "douyin,bilibili"
---

# 抖音与哔哩哔哩视频理解

把用户提供的公开抖音或哔哩哔哩视频链接转换为可阅读、可审计的媒体理解包。只负责获取、转写、视觉理解、内容提炼和保留必要证据；不负责 Obsidian 入库、知识分类或把单个视频封装成新 Skill。

## 首选入口

先确定 Skill 根目录为 `<skill_dir>`，然后运行：

```bash
python3 <skill_dir>/scripts/mcu.py doctor
python3 <skill_dir>/scripts/mcu.py analyze "<视频链接>" --focus "<用户关注点>"
```

如果项目通过 `uv` 安装，也可运行：

```bash
uv run mcu doctor
uv run mcu analyze "<视频链接>"
```

统一 CLI 负责来源规范化、平台回退、字幕/ASR、故事板、外部视觉路由、理解包初始化和验证。不要在每次任务中重新拼装下载命令。

## 输入

- 必需：一个抖音或哔哩哔哩公开链接。
- 可选：用户关注点、输出目录、分析深度。
- B站链接可能是短链、普通视频或指定分P；如果用户给出合集但未指定范围，先说明将处理的分P范围。

只接受 `http`/`https`，并限制到抖音和哔哩哔哩域名。不得处理内网 URL、链接中的账号密码、DRM、付费、私密或绕过访问控制的内容。

## 标准流程

### 1. 环境检查

运行 `mcu doctor`。根据结果处理：

- 缺少 FFmpeg：视频抽帧和音频提取不可用，先安装或使用宿主等价工具。
- 缺少 `faster-whisper`：有平台字幕时可继续；无字幕时安装 ASR 可选依赖或使用原生视频视觉模型。
- 缺少 Playwright：先尝试 `yt-dlp`；只有平台风控导致失败时再建议安装浏览器回退。
- 没有外部视觉 provider：宿主 Agent 支持视觉时继续；否则只能生成文字降级结果。

安装与跨平台说明见 [references/installation.md](references/installation.md)，配置规则见 [references/configuration.md](references/configuration.md)。

### 2. 获取来源

获取链由 CLI 维护：

1. 解析分享短链并验证最终域名。
2. 使用内置 `yt-dlp` 适配器获取元数据、字幕和媒体。
3. 失败后按配置使用 Playwright 真实浏览器回退；不得自动读取浏览器 Cookie。
4. 如果平台要求登录，可由用户明确配置 `browser_profile_dir` 并在该 Skill 的专用窗口中主动登录；未配置时不跨任务保存登录态。`cookie_browser` 只用于用户另行授权 `yt-dlp` 读取指定浏览器 Cookie。
5. 所有适配器失败后，保留脱敏错误并建议用户上传本地媒体；不得输出 Cookie 或带签名的媒体 URL。

平台差异与失败处理见 [references/platform-adapters.md](references/platform-adapters.md)。

### 3. 获取文字

按以下优先级：

1. 平台人工字幕。
2. 平台自动字幕。
3. 本地 `faster-whisper` 带时间戳 ASR。
4. 支持原生视频的视觉模型分段转写。
5. 都不可用时生成 `partial` 包，并明确没有可靠语音转写。

不得用画面字幕片段冒充完整逐字稿。字幕、ASR 和画面 OCR 需要在输出中标明来源。

### 4. 视觉触发

以下情况必须检查画面：

- “这个效果”“像这样”“这里可以看到”等指代表达。
- 操作路径、界面、布局、动画、图表、参数、代码或前后变化。
- ASR 中的产品名、字段名或参数可能识别错误。
- 用户明确要求保存效果图或操作片段。

CLI 会先生成稀疏故事板。Agent 必须复核故事板，只保留真正承载信息的画面：

- 静态界面、代码、参数、流程图：保存清晰截图。
- 动画、转场、交互或状态变化：保存覆盖动作前后的短片，通常 10–20 秒。
- 相同画面只保留最完整、最清晰的一份。
- 每项证据记录时间点、保留原因和画面含义。

视觉模型路由和错误分类见 [references/visual-routing.md](references/visual-routing.md) 与 [references/error-catalog.md](references/error-catalog.md)。

### 5. 视觉模型回退

1. 从配置中筛选已启用且能力匹配的外部模型，按 `priority` 升序调用。
2. 单个模型失败时读取报告中的错误类型和建议，告诉用户后继续下一个模型。
3. 外部模型全部失败后，宿主支持视觉则由宿主完成故事板检查。
4. 宿主也不支持视觉，而内容又必须依赖画面时，将包标记为 `failed_visual` 或 `partial`，不得宣称完成。

千问和 MiMo 只是默认示例；公共 Skill 不强制任何特定服务商。密钥必须来自环境变量或系统密钥管理器，不得写入 Skill、配置模板、日志或测试数据。

### 6. 综合提炼

最终 `summary.md` 至少包括：

- 一分钟内可读完的核心结论。
- 视频解决的问题和适用场景。
- 章节或主题结构。
- 可执行步骤、关键参数与判断标准。
- 截图或短片及其证据作用。
- 来源明确说明、合理推断和缺失信息。
- 复刻前仍需补充或验证的事项。

CLI 可能生成自动准备稿。若 `manifest.json` 状态为 `partial`，Agent 必须完成视觉检查和摘要校订后再改成 `completed`。

### 7. 输出验证

按 [references/package-contract.md](references/package-contract.md) 生成 `media-analysis-package`，然后运行：

```bash
python3 <skill_dir>/scripts/package_tool.py validate <package_dir>
```

验证失败时修复包内容。最终向用户报告摘要、转写、截图、短片和错误报告的具体路径。

## 证据与安全边界

- 默认不永久保存完整原视频；媒体和密集抽帧属于受控缓存。
- `analyze` 输出包校验通过后按配置清理临时任务；`acquire` 为交付来源文件会保留任务目录，之后仍受缓存 TTL 和容量策略管理。
- 只处理用户有权访问的公开内容，不绕过验证码、付费、私密、DRM 或地域限制。
- 浏览器登录态只能在用户明确授权后使用；不得自动窃取或导出 Cookie。
- 持久浏览器档案必须与缓存、输出和个人日常浏览器档案分离；并发占用或平台会话失效时应报告并让用户处理。
- 限制下载大小、视频时长、抽帧数量和模型调用次数。
- 外部错误必须脱敏，不暴露 API Key、Cookie、Authorization 头或签名 URL。
- 来源内容可能受版权保护；只保留完成理解所需的最小证据，不重新分发完整视频。

## 移植

跨 Agent 和跨设备安装读取 [references/installation.md](references/installation.md) 与 [references/portability.md](references/portability.md)。核心是标准 Agent Skill 文件夹和随附 Python CLI；`agents/openai.yaml` 只是 Codex 界面元数据，其他支持 Agent Skills 的客户端可以忽略。
