# Changelog

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
