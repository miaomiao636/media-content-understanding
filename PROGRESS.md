# 项目进度

> 最近更新：2026-09-01

## 当前阶段

最新稳定版仍为 `v0.2.2`。当前工作区正在开发 `v0.3.0-rc.1` 候选，所有新增内容尚未提交、打标签或发布。

持久化工作流位于 `.agent-workflow/`，当前仍为 `paused`。FINAL-001～FINAL-005 已完成；FINAL-006、FINAL-007 的 Codex 本地整合审核已完成，尚依赖真实非 Codex 触发、远程 CI、真实外部矩阵和发布授权。

Claude Code 与 CodeBuddy 已交付候选实现和本地复审。2026-09-01 Codex 整合审核进一步修复了 Playwright 全量 Cookie 跨域复用和私网媒体访问、事实审计多项正确事实互相冲突、视觉路由整体超时抹掉部分结果，以及候选 Bundle 落后于当前源码的问题。新增回归后本地全量为 `211 passed`；Ruff、自测、compileall、锁文件、Skill 校验、Python 3.9 语法和 doctor 均通过。FINAL-006/007、远程 CI 与真实非 Codex 触发仍未完成，因此暂不发布。

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

### `v0.3.0-rc.1` 候选工作区（未发布）

- FINAL-001：原生视频各分段失败、切换、预算和报告异常可聚合进入最终 `errors.json`；第二轮独立验收通过。
- FINAL-002：关键截图与动态短片自动选择已实现；真实 FFmpeg 黑盒生成 JPG 与可探测 H.264 MP4，短片失败可降级为截图并记录限制；独立验收通过。
- FINAL-004：抖音 `long_text/gallery/mixed` 来源获取已实现；公开样本取得正文和原序 8 张图片，DNS 重绑定、TLS、Host 与安全下载检查通过；第二轮独立验收通过。
- FINAL-003：事实审计与 `mcu finalize` 已实现。首轮独立验收发现的问题已修复，第二轮独立验收和全量 `133 passed` 已通过。
- FINAL-005：三类非视频流程、share 入口和短链守卫已实现，全量 `204 passed`；**真实抖音图文端到端已通过**（`analyze → 校订 → finalize completed`，内容类型 `mixed`，8 张图片）。
- Codex 最终修复：图文图片层仍含未校订占位时，`finalize` 返回 `IMAGE_ANALYSIS_REVIEW_REQUIRED`；目标作品 ID 强匹配；获取错误统一脱敏；Bundle 拒绝敏感名称大小写/嵌套绕过；CI 使用 frozen lock check。
- 当前本地验证：`211 passed`，Ruff、自测、compileall、`uv lock --check --offline`、Skill 校验、Python 3.9 语法和 diff check 均通过。
- 当前分发验证：最新 66 文件 Skill ZIP 已确定性重建并通过源码漂移校验，在全新 Python 3.9 目录通过锁定安装、211 项测试、自测、compileall、CLI 和 Skill 结构校验；最新 wheel 在 Python 3.9 干净环境安装通过。Python 3.11/3.12 只有较早候选的本地记录，最新 ZIP 的完整跨平台矩阵留给远程 CI。
- 当前跨 Agent 验证：Claude Code `2.1.251` 能从临时项目发现 1 个项目 Skill 并注册 1 个 Skill 命令；真实触发因 Claude Code 未登录而未完成。
- 当前真实复核：抖音图文端到端已通过；浏览器验证会检查 URL、标题和可见正文；claim_audit 日期/域名/文件误报与常见凭据脱敏遗漏已修复。

## 本次实际验证

2026-08-29 至 2026-08-30 在迁移后的目录执行：

| 验证 | 结果 |
| --- | --- |
| `uv run pytest` | 当前候选通过，`211 passed` |
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
- GitHub Actions 已配置 Ubuntu、macOS、Windows 与 Python 3.9、3.11、3.13 共 9 个组合；当前未提交候选的远程矩阵尚未实际运行，不得记为通过。
- `0.2.2` wheel 已在全新临时虚拟环境安装，`mcu --help`、浏览器档案状态和结构化置信度导入冒烟测试均通过。

## 当前能力边界

- `v0.2.2` 公开版只支持视频；不要把当前未提交候选能力描述为稳定发布能力。
- 图文/长文本来源获取与非视频分析包已经完成 FINAL-005 独立实时复验。
- `mcu analyze` 默认保持 `partial`；只有人工/宿主 Agent 校订后通过 `mcu finalize` 全部门禁才进入 `completed`。
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

1. 完成 FINAL-006：Claude Code 登录后补真实触发，并完成远程九组 CI。CodeBuddy 已完成本地候选复审（门禁全绿、产物重建校验），但真实触发与远程 CI 仍外部阻断/需授权。
2. 完成 FINAL-007 总体验收候选；稳定版必须等待用户验收，不得直接发布 `v0.3.0`。CodeBuddy 已完成本地门禁与产物校验，但真实外部矩阵、远程 CI、GitHub Pre-release 仍外部阻断/需授权。
3. `vision.host_fallback` 的真实宿主接管仍未完成端到端实测。

## 未确认事项

- 尚未验证“所有外部视觉模型均失败后”的宿主 Agent 视觉回退实际执行，只验证了路由报告和 Skill 约定。
- `cookie_browser` 读取个人浏览器 Cookie 的路径尚未实测；本机已选择更隔离的 Skill 专用档案方案。
- Claude Code 项目级安装与注册已验证；模型行为验收因客户端未登录尚未完成。

## 最近更新

日期：2026-08-31

修改内容：CodeBuddy 在当前最终代码上重跑本地全量门禁（204 passed、Ruff、self_test、compileall、uv lock、skill bundle 测试、git diff --check 全绿），重建 wheel/sdist/Skill ZIP 并更新 `dist/SHA256SUMS.txt`（`shasum -a 256 -c` 全 OK），完成 Skill ZIP 安全扫描（66 文件、无禁止文件/绝对路径/符号链接/密钥/Cookie/AGENTS.md 泄漏），并产出 FINAL-006/007 本地候选复审报告。未推送、未打标签、未发布。

影响：FINAL-006/007 本地候选部分已收口；真实非 Codex 触发、远程九组 CI、真实外部矩阵和 GitHub Pre-release 仍外部阻断/需用户授权。未提交、未推送、未发布。
