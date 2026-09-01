# v0.3.0-rc.1 候选验收清单

> 制定日期：2026-08-31；最后本地复审：2026-09-01
> 稳定版：`v0.2.2`
> 候选版本：`v0.3.0-rc.1`（GitHub Pre-release 已发布）
> Python 版本格式：`0.3.0rc1`（PEP 440）
> Git 标签：`v0.3.0-rc.1`，指向 `310a7ad`

> **Codex 最新审核结论：代码、产物和远程矩阵门禁通过，候选已进入外部验收。** FINAL-005 的真实抖音图文 `analyze → 校订 → finalize completed` 保持通过；本地 212 项测试及 GitHub Actions 18/18 个任务通过，4 个发布附件的远程 SHA-256 与本地一致。Claude Code `2.1.251` 已发现并注册该 Skill，但真实模型触发仍需登录后验证；稳定版不得在用户验收前发布。

## 状态概览

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| FINAL-001 失败报告聚合 | completed | attempt 2 独立验收通过 |
| FINAL-002 关键截图与短片自动化 | completed | attempt 1 独立验收通过 |
| FINAL-003 事实审计与 finalize 门禁 | completed | attempt 2 独立验收通过 |
| FINAL-004 抖音非视频来源获取 | completed | attempt 2 独立验收通过 |
| FINAL-005 抖音非视频分析包 | **completed** | 真实抖音图文 `analyze → 校订 → finalize completed` 通过 |
| FINAL-006 Skill ZIP 分发 | pending | Bundle、本地安装和远程九组合通过；真实非 Codex 模型触发待目标客户端登录后验证 |
| FINAL-007 最终验收候选 | pending | 本地门禁、远程 CI 和 Pre-release 完成；等待用户跨设备/跨 Agent 验收 |

## 已验证能力

### 通过独立验收的新增能力（FINAL-001～004）

1. **失败报告聚合**（FINAL-001）：原生视频分段失败、provider 切换、预算和报告异常可按顺序聚合到最终 `errors.json`。
2. **关键截图与动态短片**（FINAL-002）：基于字幕视觉触发词与场景变化规划关键截图和动态短片，短片失败可降级为截图。
3. **事实审计与 finalize**（FINAL-003）：`claim_audit.py` 提取数字、金额、百分比、时长、版本和模型名称；跨来源冲突会阻断完成状态；`mcu finalize` 原子写入 `completed`。
4. **抖音非视频来源获取**（FINAL-004）：支持 `long_text`、`gallery`、`mixed` 三种类型，DNS 连接锁定、TLS、Host 和安全下载验证通过。

### 代码质量门禁

| 验证 | 结果 |
| --- | --- |
| pytest 全量 | 212 passed |
| Ruff | All checks passed |
| self_test | OK |
| compileall | OK |
| diff check | 无空白错误 |
| 版本元数据一致性 | 通过（含 PEP 440 规范化映射） |

### 分发基础设施

| 验证 | 结果 |
| --- | --- |
| Skill ZIP 构建 | 66 个文件；包含共享浏览器验证模块、对应回归测试和源码漂移校验器 |
| Bundle 安全测试 | 39 项通过（路径遍历、符号链接、敏感文件大小写/嵌套路径、绝对路径） |
| CI unit + Bundle | 九组合矩阵（Ubuntu/macOS/Windows × Python 3.9/3.11/3.13）共 18 个任务全部通过 |
| Codex 本地解压安装 | 最新 66 文件 ZIP 在全新 Python 3.9 环境通过锁定安装、212 项测试、自测、compileall、CLI 和 Skill 结构校验 |
| wheel 干净安装 | 最新 wheel 在全新 Python 3.9 环境通过，版本 `0.3.0rc1` |
| Claude Code 项目级注册 | `2.1.251` 已发现 1 个项目 Skill 并注册 1 个 Skill 命令；真实触发因客户端未登录而未完成 |

### Codex 审核修复

1. 未校订的图文图片分析层现在会阻断 `finalize`，不能只改摘要进入 `completed`。
2. 抖音结构化数据和 DOM 必须绑定原始请求作品 ID，不再采用推荐作品或通用 meta 描述冒充作者正文。
3. 获取阶段错误统一脱敏，覆盖 Authorization、Cookie、JSON 密钥、常见 `sk-` 密钥和 URL 查询参数。
4. Bundle 防护改为大小写不敏感并覆盖嵌套凭据、浏览器档案、Cookie、Token、私钥文件；CI 增加 frozen lock check。
5. 浏览器验证等待统一检查 URL、标题和可见正文，正文内登录/滑块会进入限时等待并明确超时。
6. 错误脱敏覆盖 Python/JSON 字典、请求头和普通键值中的 Cookie、Token、Client Secret 等格式。
7. 事实审计排除 ISO 日期、域名、媒体文件和元数据字段误报。
8. Playwright 只向对应候选 URL 发送该 URL 的 Cookie；跨域重定向丢弃 Cookie/授权头，下载全链路拒绝私网、回环和非公网目标。
9. 事实审计以同源完整声明与最近软件对象对齐，避免版本、金额、时长等并列正确事实互相冲突。
10. 视觉路由子进程超时会生成结构化非致命错误，保留已完成的草稿和证据，不再让整个任务异常退出。
11. Bundle 构建器新增现有 ZIP/清单与当前源码一致性校验，CI 会在构建后立即阻断过期产物。

### 当前候选产物

| 产物 | SHA-256 |
| --- | --- |
| `skill-bundle-v0.3.0-rc.1.zip` | `d3fa83f6a023085f7684c32c2e75abdc7939bf406ace93f0ddcde4d072d8f260` |
| `media_content_understanding-0.3.0rc1-py3-none-any.whl` | `d10bb8ff275dbce2ea437c562db93028956528b348a9961e751ab9225d3d3298` |
| `media_content_understanding-0.3.0rc1.tar.gz` | `903ce10b59baa179c9336c6b680affee660539ebe1f45df839c180a19e9b0c1c` |

## FINAL-005 实时通过证据

### FINAL-005 真实端到端

抖音图文真实端到端已通过：

- 真实样本：`https://www.douyin.com/note/7659275356428852849`
- 内容类型：`mixed`（图文混合）
- 作者：我是梓渝
- 获取方式：`douyin-content`（Playwright 专用浏览器档案）
- 包含：8 张来源图片、作者正文、元数据
- 所有 finalize 门禁通过：summary、structure、visual_evidence、severe_claim_conflicts
- `mcu finalize` 返回 `ok: true, status: completed`
- 脱敏独立报告：`.agent-workflow/reports/FINAL-005-attempt-3-codex.md`

修复内容：
1. 浏览器验证等待机制：统一检查 URL、标题和可见正文，正文中的登录/滑块也会等待，最长 120 秒
2. claim_audit 过滤：ISO 日期、域名、媒体文件和元数据字段不再被识别为范围或软件名
3. 错误脱敏：带引号字典、请求头和普通键值中的 Cookie/Token/Secret 均被覆盖
4. summary 标题精确匹配：使用 finalize 门禁期望的标题模式

### 未验证项

1. 所有外部视觉模型均失败后的宿主 Agent 视觉回退（只有路由报告和 Skill 约定验证）。
2. `cookie_browser` 读取个人浏览器 Cookie 的路径（本机选择更隔离的专用档案方案）。
3. Claude Code 已完成项目级发现与注册；模型真实触发仍因客户端未登录而未完成。
4. 用户在其他设备和 Agent 中的真实安装、模型触发与平台样本反馈。

## 变更文件清单

### 新增文件

- `scripts/claim_audit.py` — 结构化事实审计
- `scripts/douyin_content_adapter.py` — 抖音非视频来源适配器
- `scripts/evidence_selector.py` — 证据规划
- `scripts/build_skill_bundle.py` — Skill ZIP 确定性构建器
- `tests/test_claim_audit.py`
- `tests/test_douyin_content_adapter.py`
- `tests/test_evidence_selector.py`
- `tests/test_evidence_workflow.py`
- `tests/test_nonvideo_workflow.py`
- `tests/test_package_finalize.py`
- `tests/test_transcription_reporting.py`
- `tests/test_skill_bundle.py`
- `tests/fixtures/douyin/` — 离线测试夹具
- `docs/final-acceptance-v0.3.0-rc.1.md` — 本文档

### 修改文件

- `pyproject.toml` — 版本更新至 `0.3.0rc1`
- `scripts/__init__.py` — 版本更新至 `0.3.0rc1`
- `SKILL.md` — 版本更新至 `v0.3.0-rc.1`，新增非视频能力描述
- `CHANGELOG.md` — 新增 `0.3.0rc1` 条目
- `README.md` — 新增非视频功能说明
- `scripts/mcu.py` — 新增 `finalize` 命令和非视频分析编排
- `scripts/package_tool.py` — 非视频包结构校验
- `scripts/source_adapter.py` — share URL 规范化、短链守卫
- `scripts/media_tools.py` — 证据截取工具
- `scripts/config_loader.py` — 非视频配置支持
- `scripts/vision_router.py` — 图片视觉流程集成
- `.github/workflows/test.yml` — 新增 Bundle 验证 Job
- `tests/test_release_metadata.py` — 版本规范化映射测试
- `tests/test_source_adapter.py` — share URL 守卫测试
- 六份 Context 文档（PROJECT_CONTEXT.md、MEMORY_INDEX.md、PROGRESS.md、DECISIONS.md、NEXT_TASKS.md、HANDOFF.md）

### 未修改（用户原有文件）

- `AGENTS.md` — 只读，不修改、不提交、不删除

## 用户验收清单

- [x] 确认 `v0.2.2` 仍是当前稳定版；`v0.3.0-rc.1` 仅作为 Pre-release 发布。
- [x] 用公开抖音图文样本验证 `mcu analyze → 校订 → finalize completed`。
- [x] 确认 CHANGELOG 描述与实际验证结果一致。
- [x] 确认 Skill ZIP 不包含个人路径、密钥、Cookie、原始媒体或浏览器档案。
- [x] 候选提交、推送并创建 GitHub Pre-release；标签指向通过远程矩阵的提交。
- [ ] 在其他设备和非 Codex Agent 完成安装、触发和平台样本验收后，再决定是否晋升稳定版。
