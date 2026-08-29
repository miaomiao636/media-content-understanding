# 跨设备与跨 Agent 移植

## 可移植边界

仓库遵循 Agent Skills 文件夹规范。支持 Agent Skills 且允许执行 Python/系统工具的客户端，可以读取同一个 `SKILL.md` 并运行随附 CLI。

“支持 Skill”不代表目标环境已经具备视频下载、FFmpeg、ASR、浏览器或视觉模型。安装后必须运行 `mcu doctor`，以实际检查结果为准。

## 不随仓库分发的内容

- API Key。
- Cookie、浏览器配置和登录态。
- 用户 `config.json`。
- 完整原视频、缓存、逐字稿和分析结果。
- 本机绝对路径。
- 本地 Whisper 模型文件。

## 操作系统

- macOS：环境变量优先，持久密钥使用系统钥匙串。
- Windows：环境变量优先，持久密钥通过 Python Keyring 使用系统凭据存储。
- Linux：环境变量优先；持久 Keyring 是否可用取决于桌面 Secret Service。服务器和容器建议使用环境变量或 Secret Manager。

## Agent 客户端

- `SKILL.md`、`scripts/`、`references/`、`assets/` 是通用核心。
- `agents/openai.yaml` 是 Codex 界面元数据，其他客户端可以忽略。
- 客户端如果不允许执行本地命令，将无法运行获取和媒体处理脚本，只能把 Skill 当作流程说明使用。
- 宿主支持视觉时，可以接管外部视觉模型失败后的故事板分析。

## 移植步骤

1. 下载完整仓库，不只复制 `SKILL.md`。
2. 保持目录名为 `media-content-understanding`。
3. 安装 Python、FFmpeg 和所需可选依赖。
4. 创建本机独立配置，不复制别人的密钥和 Cookie。
5. 运行 `mcu doctor`、离线自测和一个公开测试视频。
6. 只有真实输出通过验证，才把环境标记为可用。

## 发布测试矩阵

- macOS、Windows、Ubuntu。
- Python 3.9–3.13。
- 抖音短链、长链和无字幕视频。
- B站 BV 链接、短链、指定分P和无字幕视频。
- 只有宿主视觉、只有外部视觉、视觉模型故障切换。
- 平台验证码、登录要求、403/412、删除或私密视频。

