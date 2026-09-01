# 后续任务

> 最近整理：2026-09-01。Claude Code 与 CodeBuddy 已交付，Codex 本地整合修复已完成；以下以当前代码和 `.agent-workflow/` 状态为准。

## 执行分工

- Codex 主 Agent：本地整合审核、远程矩阵和 `v0.3.0-rc.1` Pre-release 已完成；下一步是收集真实非 Codex 触发和用户跨设备安装反馈，稳定版晋升前处理确认的问题。

## 当前起点

- 工作流：`paused`
- 检查点：`.agent-workflow/checkpoints/20260831T113325247086Z-paused.json`
- FINAL-001：`completed`
- FINAL-002：`completed`
- FINAL-003：`completed`，attempt 2 独立验收通过
- FINAL-004：`completed`
- FINAL-005：`completed`，真实抖音图文端到端通过（`analyze → 校订 → finalize completed`）
- FINAL-006：`pending`，212 项本地回归、66 文件 Skill ZIP、源码一致性、Python 3.9 干净安装、安全扫描和远程 3×3 OS/Python 矩阵通过；真实非 Codex 模型触发待目标客户端登录后完成
- FINAL-007：`pending`，安全/正确性阻断项、远程 CI 和 GitHub Pre-release 已完成；等待用户跨设备/跨 Agent 验收
- 版本元数据：已更新至 `0.3.0rc1`（PEP 440）/ `v0.3.0-rc.1`（展示格式）

## 已完成：FINAL-003

- 第二轮独立复验已经通过。
- `300-3000` 与 `300-30000` 跨来源冲突会阻断完成状态。
- `Claude`、`Gemini`、`Microsoft Excel` 等无版本实体已进入审计。
- 正常包只有全部门禁通过才以一次原子替换进入 `completed`。

## 已完成：FINAL-005 抖音非视频分析包

依赖：FINAL-003、FINAL-004 完成。

- 真实抖音图文端到端已通过：`analyze → 校订 → finalize completed`。
- 样本：`https://www.douyin.com/note/7659275356428852849`，内容类型 `mixed`，8 张图片。
- 浏览器验证等待机制已统一实现：检查 URL、标题和可见正文，正文内登录/滑块也会进入最长 120 秒等待并有超时测试。
- claim_audit 已排除 ISO 日期、域名、媒体文件和元数据字段误报。
- claim_audit 已避免同一来源内并列的正确版本、金额、时长、百分比和软件名称互相制造冲突，并把版本绑定到最近的软件对象。
- 错误脱敏已覆盖带引号字典、请求头和普通键值中的 Cookie、Token、Client Secret 等格式。
- 所有 finalize 门禁通过：summary、structure、visual_evidence、severe_claim_conflicts。

## P1：FINAL-006 完整 Skill 分发与跨环境安装

依赖：FINAL-003、FINAL-005 完成。

- CodeBuddy 本地候选复审已完成：66 文件 Skill ZIP 重建，`shasum -a 256 -c` 全 OK，无禁止文件/绝对路径/符号链接/密钥/Cookie/AGENTS.md 泄漏；报告 `.agent-workflow/reports/FINAL-006-local-recheck-codebuddy.md`。
- 从解压包执行安装、测试、自测、compileall 和 Skill 结构检查的执行路径与 `.github/workflows/test.yml` 的 `bundle` job 一致；远程九组已全部通过。
- 发布前必须执行 `build_skill_bundle.py --verify-existing ... --manifest-in ...`，确认候选 ZIP 与最终源码和文档逐文件一致。
- CI 继续覆盖 Ubuntu/macOS/Windows 与 Python 3.9/3.11/3.13 九组组合。
- 在本机 Codex 和至少一个非 Codex Agent 客户端做安装与触发验证。
- Claude Code `2.1.251` 已确认能发现并注册项目 Skill；完成 `/login` 后仍需重跑一次真实触发。
- 兼容声明只列实际验证结果，不宣称“所有 Agent 通用”。

## P1：FINAL-007 `v0.3.0-rc.1` 候选

依赖：FINAL-001～006 全部完成。

- CodeBuddy 本地最终候选复审已完成：全量 pytest、Ruff、自测、compileall、uv lock、skill bundle 测试、git diff --check 全绿；wheel/sdist/Skill ZIP 重建并校验和 OK；报告 `.agent-workflow/reports/FINAL-007-local-recheck-codebuddy.md`。
- 真实矩阵覆盖抖音视频、B站视频、抖音非视频、截图、动态短片、视觉回退和 finalize——真实外部矩阵需用户在目标环境合规触发，平台风控阻断项明确标为外部阻断，不伪造成功。
- 更新版本、CHANGELOG、README、六份 Context 和用户验收清单。
- 准备 Skill ZIP、wheel、sdist 与校验和（已完成重建与校验和写入 `dist/SHA256SUMS.txt`）。
- 执行 Agent 到此只提交候选结果与风险，不得自行宣称最终通过。

## 主 Agent 最终审核门禁

1. `.agent-workflow` 校验通过且所有任务都有独立 `pass` 报告。
2. 审查候选提交和仍未跟踪文件，确认无密钥、Cookie、个人路径、原视频或越权修改。
3. 重跑总体验证和真实样本矩阵；区分平台风控失败与代码缺陷。
4. 确认完整 Skill ZIP 可移植，兼容声明有实际证据。
5. `v0.3.0-rc.1` GitHub Pre-release 已按用户授权创建；后续仅在验收通过后晋升稳定版。
6. 稳定版 `v0.3.0` 等待用户验收确认，不在本轮自动发布。

## 不在本轮范围

- Obsidian 入库、分类和知识库目录管理。
- 为每个视频自动生成独立 Skill。
- B站非视频内容。
- 绕过验证码、登录、付费、私密、DRM 或地域限制。
- 把 Ego Browser 增加为第三个 CLI 来源适配器。
