# 项目进度

> 最近更新：2026-08-30

## 当前阶段

`0.2.2` 已完成视觉配置语义、真实双模型复核、发布审查、跨平台 CI 和正式发布。最新已发布标签为 `v0.2.2`。

仓库已经具备公开分发所需的 Skill 说明、CLI、依赖锁定、许可证、安全说明、跨平台安装文档和 CI。核心离线基线通过，但平台真实链路和部分配置语义仍需继续完善，因此不应描述为完全无人值守或所有内容类型均已支持。

## 已完成

### 产品与架构

- 已拆分出独立的“视频内容理解”Skill，不包含 Obsidian 入库。
- 支持公开抖音、哔哩哔哩 URL 白名单和短链二次域名验证。
- 已建立 `yt-dlp → Playwright` 来源回退链。
- 已实现平台字幕优先、本地 `faster-whisper` 可选回退，以及原生视频视觉模型分段转写路径。
- 已实现 FFmpeg 故事板、单帧、短片和音频工具。
- 已实现外部视觉 provider 按数值优先级升序调用、能力匹配、有限重试、错误分类、建议和脱敏报告。
- 已提供千问 Omni、Xiaomi MiMo 和普通 OpenAI 兼容接口配置。
- 已实现环境变量、macOS Keychain、Windows/Linux Keyring 凭据路径。
- 已定义并实现 `media-analysis-package` 1.0 的初始化和校验。
- 已实现带根标记、任务标记和显式 `--apply` 的安全缓存清理工具。
- 已实现“显式 CLI 参数 → 用户配置 → 内置默认值”的运行参数优先级。
- 已将 ASR 模式、模型、语言、故事板帧数和下载上限接入实际运行流程。
- 已实现任务状态、成功清理、失败保留、TTL、容量清理和近期运行任务保护。
- 已实现可选的 Playwright 专用持久浏览器档案、跨任务登录复用、并发占用错误和安全状态清除命令。
- 已将 `max_visual_calls` 实现为覆盖原生视频转写、重试、故障切换、摘要和复核的共享 provider 尝试预算。
- 已将 `max_upload_mb` 实现为单次请求的本地 Base64 媒体合计限制，并保留 provider 单项限制。
- 已实现结构化低置信度协议；主结果明确为 `low` 时调用下一可用 provider 复核。
- 显式 `--config` 已传入全部视觉子流程；缺少可信子进程用量时会保守耗尽剩余预算。

### 工程化与发布

- Python 要求为 3.9+；核心、ASR、浏览器、开发依赖已在 `pyproject.toml` 分组。
- `uv.lock` 已锁定跨 Python 版本依赖。
- CI 覆盖 Ubuntu、macOS、Windows，以及 Python 3.9、3.11、3.13。
- 已采用 Apache-2.0 许可证。
- GitHub 仓库位于 `miaomiao636/media-content-understanding`。
- `v0.2.1` 发布提交为 `15e5af8`；`v0.2.2` 发布提交为 `3ce70c7`。两个标签和 GitHub Release 均已公开。
- 已成功构建 `0.2.2` 的 wheel 和 sdist，并确认 wheel 包含统一 CLI、视觉路由、清理模块和 Keychain Swift 助手。

## 本次实际验证

2026-08-29 至 2026-08-30 在迁移后的目录执行：

| 验证 | 结果 |
| --- | --- |
| `uv run pytest` | 通过，`75 passed` |
| `uv run ruff check scripts tests` | 通过 |
| `uv run python scripts/self_test.py` | 通过 |
| `uv run python -m compileall -q scripts` | 通过 |
| `uv run mcu doctor` | 通过，`ok: true` |
| 抖音真实链接 | 通过；`yt-dlp` 要求新鲜 Cookie 后由可见 Playwright 回退成功，输出包校验通过 |
| B站真实链接 | 通过；`yt-dlp` 直接获取成功，输出包校验通过 |
| 千问真实视觉调用 | 通过；两条视频均由 `qwen3.5-omni-plus` 完成音画转写与综合 |
| 千问失败后 MiMo 接管 | 通过；临时模拟千问鉴权错误，`mimo-v2.5` 自动接管并成功返回 |
| 低置信度复核 | 通过；真实千问结果标记为 `low` 后，MiMo 基于同一故事板复核为 `high`，总调用严格为 2 次 |
| Playwright 登录复用 | 通过；同一专用档案连续两次取得 310.8 秒完整抖音视频，第二次无需重新登录 |

本机环境检查结果：

- FFmpeg、FFprobe、`yt-dlp`、Ego Browser 可用。
- Playwright Python 依赖可用。
- `faster-whisper` 当前未安装。
- 千问主 provider 和 MiMo 第二 provider 均已通过静态配置检查，凭据来源为 macOS Keychain；密钥内容未读取、未输出。
- 用户授权的公开抖音和 B站样本均生成 `partial` 输出包并通过结构校验；抖音样本保留 10 张故事板，B站样本保留 6 张。
- 两条测试源均无可用平台字幕；由于本机未安装 `faster-whisper`，实际采用千问原生视频理解生成带时间戳转写。
- 故障切换测试只使用不含密钥的临时配置；千问错误被正确分类为 `AUTHENTICATION_ERROR` 并附处理建议，MiMo 随后成功。临时配置已删除。
- 本机已明确配置 Skill 专用浏览器档案；目录权限为 `700`，与个人 Chrome、项目、缓存和输出目录分离。
- GitHub Actions 对最终发布提交执行 Ubuntu、macOS、Windows 与 Python 3.9、3.11、3.13 共 9 个组合，全部通过。
- `0.2.2` wheel 已在全新临时虚拟环境安装，`mcu --help`、浏览器档案状态和结构化置信度导入冒烟测试均通过。

## 当前能力边界

- 统一 CLI 只支持视频，图文、长文本和混合内容仍停留在契约/设计文档层。
- `mcu analyze` 生成自动准备稿和故事板后将包保持为 `partial`，需要宿主 Agent 复核画面、精简证据和校订摘要。
- `media_tools.py` 能生成短片，但统一分析流程暂不自动判断并保存动态证据片段。
- 真实平台测试不放入 CI；平台下载可用性受页面、地区和风控影响。

## 已知问题

### 已在工作区 0.2.1–0.2.2 解决

1. ASR 和故事板配置被 argparse 固定默认值遮蔽。
2. `acquisition.max_download_mb` 没有传入来源适配器。
3. 缓存保留、失败任务、TTL 和容量策略没有进入主流程。
4. 加载无配置文件时会修改全局默认配置对象。
5. Playwright 每次使用一次性上下文，无法保存用户已授权的抖音登录状态。
6. 部分已有配置仍可能污染默认配置对象；浏览器档案清除只依赖配置路径，缺少独立管理标记。
7. `vision.max_upload_mb` 未与 provider 专属限制共同生效。
8. `verification_mode=low-confidence` 缺少结构化协议和二次复核。
9. `max_visual_calls` 只限制视频分段数量，不能覆盖重试、备用模型和摘要。

### 仍需处理

1. `vision.host_fallback` 只进入视觉报告；实际宿主视觉回退仍由 `SKILL.md` 指导 Agent 完成。
2. 故事板会整体复制到输出包；自动证据筛选和动态短片选取尚未完成。
3. `gallery`、`long_text`、`mixed` 尚无来源适配和 CLI 编排实现。
4. 高分辨率长视频的 FFmpeg 故事板生成耗时偏长，尚未做按目标尺寸抽帧等性能优化。
5. 外部模型生成的数字、身份与宣传性结论仍需宿主复核；低置信度复核减少但不能消除此类错误，输出仍不能自动标记为 `completed`。

## 未确认事项

- 尚未验证“所有外部视觉模型均失败后”的宿主 Agent 视觉回退实际执行，只验证了路由报告和 Skill 约定。
- `cookie_browser` 读取个人浏览器 Cookie 的路径尚未实测；本机已选择更隔离的 Skill 专用档案方案。
- 尚未在 Codex 之外的其他 Agent 客户端完成安装与行为验收。

## 最近更新

日期：2026-08-30

修改内容：发布 `0.2.2`，接通视觉共享预算、合计上传限制、显式配置传递和结构化低置信度复核；新增视觉路由测试并完成真实千问→MiMo 复核。

影响：修改业务代码、测试、文档和 Context；已提交、推送、创建 `v0.2.2` 标签并发布 GitHub Release。没有修改或发布密钥、Cookie、浏览器档案和原始媒体；临时复核输出与安装环境已移入废纸篓，可恢复。
