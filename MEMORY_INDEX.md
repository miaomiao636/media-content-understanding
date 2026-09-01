# 项目记忆索引

> 最近更新：2026-09-01

## 快速事实

- 项目：`media-content-understanding`
- 路径：以当前 Git 检出目录为准
- 仓库：`https://github.com/miaomiao636/media-content-understanding`
- 分支：`main`
- 最新稳定标签：`v0.2.2`
- 最新预发布标签：`v0.3.0-rc.2`，指向 `749a938`；版本元数据为 `0.3.0rc2`
- 最新预发布新增 `summary.html` 阅读版；本地 Python 3.9 下 217 项测试和 GitHub Actions 18/18 个任务通过。
- 形态：Agent Skill + Python CLI，不是 Web 服务，不使用数据库。
- 稳定版能力：公开抖音和哔哩哔哩视频。
- 预发布新增：失败报告聚合、关键截图/动态短片、事实一致性与 `finalize` 门禁、抖音图文/长文本来源及统一分析编排。
- 输出：`media-analysis-package` 1.0；`summary.md` 是可编辑主文档，`summary.html` 是可显示包内图片和 MP4 短片的浏览器阅读层。分析先生成 `partial`，只有显式 `mcu finalize` 通过门禁后才能进入 `completed`。
- 当前审核结论：HTML 阅读版已通过本地 Python 3.9 下 217 项测试与 GitHub Actions 18/18 个任务；Skill ZIP、wheel、sdist 与校验和已作为 `v0.3.0-rc.2` Pre-release 发布。真实非 Codex 模型触发和用户跨设备验收仍未完成，因此不得晋升稳定版。

## Context 导航

| 需要了解的内容 | 文件 |
| --- | --- |
| 项目目标、架构、数据、API、边界 | `PROJECT_CONTEXT.md` |
| 已完成能力、验证结果、已知问题 | `PROGRESS.md` |
| 已确认的架构与安全决策 | `DECISIONS.md` |
| 分优先级的后续工作 | `NEXT_TASKS.md` |
| 最近一次 Agent 交接 | `HANDOFF.md` |
| Claude Code 剩余任务与停止点 | `CLAUDE_CODE_TASKS.md` |
| CodeBuddy 最终修复、验收与发布门禁 | `CODEBUDDY_TASKS.md` |

## 源码导航

| 主题 | 首选文件 |
| --- | --- |
| 用户入口与编排 | `SKILL.md`、`scripts/mcu.py` |
| 来源适配 | `scripts/source_adapter.py`、`references/platform-adapters.md` |
| 抖音图文/长文本来源 | `scripts/douyin_content_adapter.py`、`tests/test_douyin_content_adapter.py` |
| 字幕与 ASR | `scripts/asr_router.py` |
| 视觉路由 | `scripts/vision_router.py`、`references/visual-routing.md` |
| 配置、密钥与错误脱敏 | `scripts/config_loader.py`、`scripts/credential_store.py`、`scripts/sanitization.py`、`assets/config.example.json` |
| 输出契约 | `scripts/package_tool.py`、`references/package-contract.md` |
| 事实一致性 | `scripts/claim_audit.py`、`tests/test_claim_audit.py`、`tests/test_package_finalize.py` |
| 证据筛选 | `scripts/evidence_selector.py`、`tests/test_evidence_workflow.py` |
| 缓存安全 | `scripts/cleanup.py`、`references/storage-retention.md` |
| 安装与移植 | `README.md`、`references/installation.md`、`references/portability.md` |
| 自动测试 | `tests/`、`scripts/self_test.py`、`.github/workflows/test.yml` |

## 下一位 Agent 必须先知道

1. 不要把 Obsidian 入库功能合并进本项目；那属于后续独立流程。
2. 抖音 `long_text/gallery/mixed` 的来源获取和统一分析已实现；FINAL-005 attempt 3 已实时完成 `mcu analyze → 校订 → finalize completed`。
3. 不要把本机 Keychain 中的密钥复制到仓库、Context、日志或聊天。
4. FINAL-001～FINAL-005 均已有通过报告；最新本地全量为 212 项，FINAL-005 的脱敏通过报告为 `.agent-workflow/reports/FINAL-005-attempt-3-codex.md`。
5. 平台适配修改必须保留 URL 白名单、短链二次校验、按候选 URL Cookie 隔离、公网 IP/重定向检查和媒体完整性检查。
6. `AGENTS.md` 是用户原有未跟踪文件，不得修改、提交或删除；`.agent-workflow/` 是本轮持久化交接依据。
7. 抖音、B站、千问主模型、MiMo 故障接管和 Playwright 专用登录复用均已做真实验收。

## 当前最重要的后续方向

1. FINAL-003 已通过第二轮独立验收，不需要重做。
2. CodeBuddy 的本地候选复审之后，Codex 又完成四组整合修复、最新产物重建和 Python 3.9 干净验证；当前报告以 `.agent-workflow/reports/FINAL-007-integration-repair-codex.md` 为准，CodeBuddy 报告保留为历史证据。
3. 仍需目标环境完成：Claude Code 登录后真实 `/media-content-understanding` 触发，以及用户在其他设备/Agent 对抖音、B站和视觉故障接管的安装验收。
4. RC 已发布；稳定版 `v0.3.0` 必须等待用户验收。
