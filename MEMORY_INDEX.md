# 项目记忆索引

> 最近更新：2026-08-30

## 快速事实

- 项目：`media-content-understanding`
- 路径：以当前 Git 检出目录为准
- 仓库：`https://github.com/miaomiao636/media-content-understanding`
- 分支：`main`
- 工作区版本：`0.2.1`；最新已发布标签：`v0.2.1`
- 形态：Agent Skill + Python CLI，不是 Web 服务，不使用数据库。
- 当前支持：公开抖音和哔哩哔哩视频。
- 输出：`media-analysis-package` 1.0；统一入口默认生成 `partial`，等待宿主 Agent 最终校订。

## Context 导航

| 需要了解的内容 | 文件 |
| --- | --- |
| 项目目标、架构、数据、API、边界 | `PROJECT_CONTEXT.md` |
| 已完成能力、验证结果、已知问题 | `PROGRESS.md` |
| 已确认的架构与安全决策 | `DECISIONS.md` |
| 分优先级的后续工作 | `NEXT_TASKS.md` |
| 最近一次 Agent 交接 | `HANDOFF.md` |

## 源码导航

| 主题 | 首选文件 |
| --- | --- |
| 用户入口与编排 | `SKILL.md`、`scripts/mcu.py` |
| 来源适配 | `scripts/source_adapter.py`、`references/platform-adapters.md` |
| 字幕与 ASR | `scripts/asr_router.py` |
| 视觉路由 | `scripts/vision_router.py`、`references/visual-routing.md` |
| 配置与密钥 | `scripts/config_loader.py`、`scripts/credential_store.py`、`assets/config.example.json` |
| 输出契约 | `scripts/package_tool.py`、`references/package-contract.md` |
| 缓存安全 | `scripts/cleanup.py`、`references/storage-retention.md` |
| 安装与移植 | `README.md`、`references/installation.md`、`references/portability.md` |
| 自动测试 | `tests/`、`scripts/self_test.py`、`.github/workflows/test.yml` |

## 下一位 Agent 必须先知道

1. 不要把 Obsidian 入库功能合并进本项目；那属于后续独立流程。
2. 当前统一 CLI 只实现视频，不要因包契约支持 `gallery/long_text/mixed` 就宣称这些来源已可用。
3. 不要把本机 Keychain 中的密钥复制到仓库、Context、日志或聊天。
4. 配置优先级、下载上限和缓存生命周期已在工作区 `0.2.1` 修复；剩余视觉配置缺口见 `NEXT_TASKS.md`。
5. 平台适配修改必须保留 URL 白名单、短链二次校验、Cookie 明确授权和媒体完整性检查。
6. 当前 Context 文件和 `AGENTS.md` 在本次任务开始时均为未跟踪文件；不要未经用户同意自动提交或删除。
7. 抖音、B站、千问主模型、MiMo 故障接管和 Playwright 专用登录复用均已做真实验收。

## 当前最重要的后续方向

- 完善视觉上传上限、调用预算、低置信度复核、数字一致性校订和动态证据计划。
- 再决定是否实现抖音图文/长文本；该能力目前只有设计文档，没有统一 CLI 实现。
