# Changelog

## 0.3.0rc1 - 2026-08-31

Release candidate for cross-platform and cross-Agent testing. Stable version remains 0.2.2 until the RC is promoted after user acceptance.

- 新增抖音图文、长文本和混合内容解析，区分作者正文、标题、作者、发布时间和原序图片。
- 新增 `douyin_content_adapter.py`，支持 `long_text`、`gallery`、`mixed` 三种非视频来源类型。
- 非视频分析包不再生成 `transcript.md`、时间戳或 clips，溯源层明确区分 `author_body`、`image_ocr`、`visual_inference` 和 `summary`。
- 新增 `mcu finalize` 显式门禁：结构校验、必要证据、图文图片层校订和严重事实冲突检查全部通过后才原子写入 `completed`。
- 新增 `claim_audit.py` 结构化事实审计，提取数字、金额、百分比、时长、版本和模型/软件名称。
- 新增关键截图与动态短片自动选择，基于字幕视觉触发词和场景变化规划证据，短片失败可降级为截图。
- 新增失败报告聚合，原生视频分段失败、provider 切换、预算和报告异常可进入最终 `errors.json`。
- 新增 Skill ZIP 确定性构建器和 Bundle 安全测试。
- 来源作品 ID 必须与目标抖音作品匹配，不会把内嵌推荐作品当作作者内容；获取错误和 URL 查询参数在持久化前统一脱敏。
- 浏览器验证等待统一检查 URL、标题和可见正文；正文内出现登录、验证码或滑块时保留窗口并限时等待，超时返回 `CHALLENGE_REQUIRED`。
- 错误脱敏覆盖带引号字典、普通键值和请求头中的 Cookie、Token、Client Secret 等常见凭据格式。
- 事实审计排除 ISO 日期、域名、媒体文件和元数据字段，避免把它们误判为数值范围或软件名称。
- CI 新增九组合 Bundle 验证 Job（解压后执行 pytest、自测、compileall 和 CLI 冒烟测试）。
- 真实公开抖音图文已完成 `analyze → 证据校订 → finalize completed`；平台风控仍可能随时间要求用户重新登录或验证。
- CodeBuddy 本地候选复审：在当前最终代码重跑 204 passed、Ruff、self_test、compileall、uv lock、skill bundle 测试、git diff --check 全绿；重建 wheel/sdist/Skill ZIP 并更新 `dist/SHA256SUMS.txt`；Skill ZIP 66 文件安全扫描无禁止文件/绝对路径/符号链接/密钥/Cookie/AGENTS.md 泄漏。真实非 Codex 触发、远程九组 CI、真实外部矩阵和 GitHub Pre-release 仍外部阻断/需用户授权，未发布。
- 最终整合修复：Playwright 媒体 Cookie 改为按候选 URL 过滤，跨域重定向移除认证信息，视频下载复用公网 IP 锁定与安全重定向；事实审计按来源和最近软件对象对齐，避免多项正确事实互相误判；视觉路由整体超时保留部分结果；Bundle 新增当前源码漂移校验。
- 远程矩阵修复：干净 CI Runner 允许 setup-uv 下载指定 Python；Skill ZIP 条目和 manifest 在 Windows/macOS/Linux 统一使用 POSIX `/` 分隔符。

## 0.2.2 - 2026-08-30

- 将 `max_visual_calls` 改为覆盖原生视频转写、重试、故障切换、摘要和复核的共享 provider 尝试预算。
- 让显式 `--config` 进入所有视觉子流程，并在缺少可信子进程用量时保守停止后续调用。
- 让 `max_upload_mb` 约束单次请求中本地 Base64 媒体的合计大小，同时保留 provider 单项限制。
- 新增结构化 `MCU_CONFIDENCE` 协议；主结果明确为低置信度时由下一可用 provider 复核。

## 0.2.1 - 2026-08-30

- 统一 `analyze` 的配置优先级：显式 CLI 参数覆盖用户配置，用户配置覆盖内置默认值。
- 让 ASR 模式、模型、语言和故事板帧数配置真正进入运行流程。
- 将下载上限同时应用到 `yt-dlp`、Playwright 候选流和最终合并媒体。
- 新增配置数值校验，并修复加载默认配置时污染全局默认值的问题。
- 新增任务状态、成功清理、失败保留、TTL 和缓存容量清理；未标记目录及近期运行任务不会被容量清理。
- `mcu acquire` 保留获取结果并记录完成状态，`mcu analyze` 仅在输出包验证成功后按配置清理任务目录。
- 增加配置、下载上限、缓存安全和主流程生命周期测试。
- 新增可选的 Playwright 专用持久浏览器档案，支持用户首次登录后跨任务复用会话；默认仍使用一次性隔离上下文。
- 新增 `mcu browser-profile status/reset`，通过管理标记和路径隔离防止清除普通目录，并限制专用档案与缓存、输出、用户主目录及根目录重叠。

## 0.2.0 - 2026-08-29

- 拆分为独立的视频理解 Skill，不包含 Obsidian 入库。
- 新增抖音和哔哩哔哩来源路由、短链解析与浏览器回退。
- 新增平台字幕、本地 `faster-whisper` 与原生视频模型分段转写。
- 新增故事板、多视觉模型故障切换、错误类型与处理建议。
- 千问 `qwen3.5-omni-plus` 为示例主 provider，小米 `mimo-v2.5` 为第二备用。
- 新增 macOS Keychain 与 Windows/Linux Python Keyring 密钥持久化。
- 新增跨平台打包、锁定依赖、CI、安全说明与 Apache-2.0 许可证。
- 浏览器回退使用 `ffprobe` 区分预览、纯视频和纯音频流，并进行完整性验证。
- 统一 CLI 的 UTF-8 输出，避免 Windows 默认控制台编码导致中文错误报告崩溃。
