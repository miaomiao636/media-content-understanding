# Agent 交接记录

## 日期

2026-08-30

## 当前任务

完成 `0.2.2` 视觉配置语义、真实双模型复核、跨平台 CI、GitHub 正式发布和本机 Skill 升级。

## 已完成内容

- `vision.max_visual_calls` 现在是一次分析的共享 provider 尝试预算，覆盖原生视频转写、重试、故障切换、摘要和复核。
- 原生视频转写按剩余分段分配预算，并至少为最终故事板摘要保留一次尝试；未知子进程用量会保守耗尽预算。
- 显式 `mcu --config PATH analyze` 会把同一路径传给所有视觉子流程。
- `vision.max_upload_mb` 现在限制单次请求中所有本地 Base64 媒体的合计大小；provider 单项限制继续生效。
- 新增末尾结构化 `MCU_CONFIDENCE` 协议；只有明确的 `low` 才触发下一 provider 复核，标记不会出现在最终正文。
- 新增视觉路由、总预算、上传限制、配置传递和置信度测试；测试总数从 64 增至 75。
- 使用已有公开视频故事板完成真实复核：千问主模型 `low` → MiMo `high`，`api_calls_used=2`，未输出凭据。
- `v0.2.2` 发布提交为 `3ce70c7`，标签和 GitHub Release 已公开，wheel/sdist 已上传。

- 使用测试驱动方式修复 CLI 固定默认值遮蔽用户配置的问题。
- ASR 模式、模型、语言和 `vision.max_frames` 现在按“显式 CLI → 用户配置 → 内置默认值”解析。
- `acquisition.max_download_mb` 现在约束 `yt-dlp`、Playwright 下载及最终媒体。
- 配置加载增加关键数值校验，并修复全局默认配置被路径解析污染的问题。
- 任务目录增加 `running/completed/failed` 状态。
- 新任务启动前执行 TTL 与容量清理；容量清理不删除近期运行任务或未标记目录。
- `analyze` 只在包验证成功后清理；失败任务按独立时长保留；`acquire` 明确保留交付媒体。
- 新增配置、缓存和主流程生命周期测试，并同步 README、Skill、参考文档、CHANGELOG 和 Context。
- 上一版本 `0.2.1` 已完成配置、缓存和持久登录可靠性修复。
- 公开抖音样本：`yt-dlp` 因 Cookie 失败后，可见 Playwright 在用户主动登录后获取成功；千问完成原生视频转写和视觉综合；10 张故事板；包校验通过。
- 公开 B站样本：`yt-dlp` 直接获取成功；千问完成原生视频转写和视觉综合；6 张故事板；包校验通过。
- 使用临时配置模拟千问缺少凭据，确认路由记录 `AUTHENTICATION_ERROR` 和建议后自动调用 MiMo；MiMo 成功识别测试图片。临时配置已删除。
- 两个成功任务的受控缓存均按配置自动清理，输出包保留在配置的 `output_root`。
- 新增 `acquisition.browser_profile_dir`：非空时使用 Skill 专用 Playwright 持久上下文，空值时继续使用一次性隔离上下文。
- 新增 `mcu browser-profile status/reset`；重置默认只预览，必须加 `--yes` 才删除配置的专用档案。
- 专用档案不能与缓存、输出、用户主目录或文件系统根目录重叠；类 Unix 权限收紧为 `700`，并发占用报告 `BROWSER_PROFILE_IN_USE`。
- 本机已启用独立档案，并连续两次获取同一 310.8 秒抖音视频；第二次无需重新登录。两份临时测试媒体已移入废纸篓。
- 发布审查新增有效管理标记：非空未标记目录和项目目录不会被浏览器适配器采用，也不会被 `reset --yes` 删除。
- 修复部分已有配置仍可能引用并污染全局默认对象的问题，并为五个顶层配置段增加明确类型错误。
- 新增版本一致性测试，固定 `pyproject.toml`、Python 包、`SKILL.md` 和 CHANGELOG 的版本同步。

## 当前项目结论

- 代码版本和最新 Git 标签均为 `0.2.2`；公开 Release 为 `v0.2.2`。
- 项目是 Agent Skill + Python CLI，不存在前端、常驻服务或数据库。
- 实际支持的是公开抖音和 B站视频；图文、长文本、混合内容只有设计和包契约。
- 获取、字幕/ASR、故事板、外部视觉回退、凭据管理和输出验证链已经实现。
- 统一分析入口有意把包保持为 `partial`，由宿主 Agent 完成最终视觉复核和摘要校订。
- 配置、缓存、重复登录和视觉配置语义技术债已经解决，真实平台、真实双模型复核与跨平台 CI 已通过；当前最重要的剩余工作是证据自动筛选、动态片段和数字一致性。

## 修改文件

业务与测试：

- `scripts/vision_router.py`
- `scripts/config_loader.py`
- `scripts/mcu.py`
- `tests/test_vision_router.py`
- `tests/test_cli_helpers.py`
- `tests/test_config_loader.py`
- `tests/test_runtime_config.py`

版本与说明：

- `pyproject.toml`、`uv.lock`、`scripts/__init__.py`
- `SKILL.md`、`README.md`、`CHANGELOG.md`
- `references/configuration.md`、`references/installation.md`、`references/platform-adapters.md`、`references/storage-retention.md`
- 六份项目 Context 文档

本机用户配置新增了非敏感的专用浏览器档案路径；未修改密钥、数据库或 CI 定义。发布相关提交、标签和 Release 已写入 Git/GitHub；构建产物位于已忽略的 `dist/`。

本机 Codex Skill 已升级到 `0.2.2`；原 `0.2.1` 已完整保存在本机 Codex Skill 备份目录。用户配置、Keychain 和浏览器档案未移动。

## 验证结果

- `uv run pytest`：75 项测试全部通过。
- `uv run ruff check scripts tests`：通过。
- `uv run python scripts/self_test.py`：通过。
- `uv run python -m compileall -q scripts`：通过。
- `uv run mcu doctor`：`ok: true`。
- `uv build`：成功生成 `0.2.2` wheel 和 sdist；wheel 关键脚本与 Swift 助手存在。
- 全新临时虚拟环境安装 wheel 后，`mcu --help` 和 `mcu browser-profile status` 均成功；临时环境已移入废纸篓。
- 真实低置信度链：千问和 MiMo 各调用一次，复核成功且内部标记从用户正文移除；临时输出已移入废纸篓。

本机预检确认：FFmpeg、FFprobe、`yt-dlp`、Ego Browser、Playwright 可用；`faster-whisper` 未安装；两个已配置视觉 provider 均通过静态检查，密钥来自 macOS Keychain。没有读取或写入密钥。

未执行：所有外部视觉 provider 均失败后的宿主实际接管，以及 Codex 之外其他 Agent 客户端的安装验收。

真实验收发现：

- 专用档案能复用当前登录，但平台仍可能主动让会话过期；这时需要用户在同一专用窗口重新登录一次。
- 两个测试视频均无平台字幕且本机没有 `faster-whisper`，因此完整视频被发送给外部视觉模型分段理解；这会增加调用成本和第三方数据传输。
- 抖音高分辨率源生成故事板明显偏慢。
- 自动摘要仍需宿主校订。抖音摘要中出现过疑似把 `300-3000` 写为 `300-30000` 的数量级错误；B站测试源实际是课程引流介绍，不是完整的“两天教程”。

## Git 与工作区状态

- 当前分支：`main`；`v0.2.2` 指向发布提交 `3ce70c7`。
- 远端：`origin https://github.com/miaomiao636/media-content-understanding.git`。
- 最新标签：`v0.2.2`，GitHub Release 已公开并附 wheel/sdist。
- `main` 已推送；发布后的 Context 状态更新会作为后续文档提交，不移动已发布标签。
- `AGENTS.md` 在任务开始前就是未跟踪用户文件，本轮只读取，未修改。
- Context 文档已纳入仓库；本机 `AGENTS.md` 仍未跟踪、未修改，也未进入发布。

## 遗留问题

优先阅读 `NEXT_TASKS.md`。最关键的是：

1. 自动动态短片提取和最终证据精简尚未集成。
2. 数字、专有名词和营销声明缺少跨模态一致性复核；低置信度复核不能替代证据校订。
3. 原生视频转写失败报告尚未聚合进最终 `errors.json`，目前主要保留在受控任务目录。
4. 图文/长文本/混合来源尚未实现。

## 下一步建议

下一位 Agent 应先读取 `MEMORY_INDEX.md`，然后按 `NEXT_TASKS.md` 处理证据筛选与动态片段。继续确保：

- 不改变 Obsidian 与本 Skill 分离的边界。
- 不削弱 URL 白名单、Cookie 明确授权、媒体完整性验证和缓存标记保护。
- 不接触或输出本机 Keychain 中的真实密钥。
- 修改后重新执行 pytest、Ruff、自测、compileall 和 `mcu doctor`。
