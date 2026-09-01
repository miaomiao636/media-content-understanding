# 视觉错误类型与建议

| 错误类型 | 常见原因 | 建议处理方法 | 是否重试 |
| --- | --- | --- | --- |
| `CONFIGURATION_ERROR` | 缺少模型名、地址或适配器不支持 | 核对 provider 配置和适配器名称 | 否 |
| `AUTHENTICATION_ERROR` | API Key 缺失、错误、过期或钥匙串不可读 | 更新本机钥匙串或对应环境变量，不要把密钥发到聊天 | 否 |
| `PERMISSION_ERROR` | 账户无模型权限、地区或项目限制 | 检查账户授权、模型白名单和服务区域 | 否 |
| `RATE_LIMITED` | 请求频率或额度超限 | 等待限流恢复、降低并发或更换模型 | 是，短退避 |
| `TIMEOUT` | 模型响应过慢或上传过大 | 减少帧数、压缩图片、延长超时或换模型 | 是 |
| `NETWORK_ERROR` | DNS、代理、断网或 TLS 错误 | 检查网络和代理，确认 Base URL 可访问 | 是 |
| `SERVER_ERROR` | 服务商 5xx 或临时故障 | 稍后重试或切换其他 provider | 是 |
| `INPUT_TOO_LARGE` | 图片数量、尺寸或上下文超限 | 减少帧数、分批分析或压缩媒体 | 修改输入后重试 |
| `UNSUPPORTED_MEDIA` | 格式或图片输入不被模型支持 | 转为 JPEG/PNG，视频先抽关键帧 | 修改输入后重试 |
| `CONTENT_POLICY` | 服务商安全策略拒绝 | 检查内容范围；如属正常内容可换获准模型 | 通常否 |
| `INVALID_RESPONSE` | 返回为空、结构错误或非预期格式 | 用结构化提示重试一次，再切换模型 | 是一次 |
| `UNKNOWN_ERROR` | 未分类错误 | 查看脱敏详情、服务商状态和日志，再决定是否停用 | 否，默认切换 |

## 原生视频分段转写错误

原生视频转写会把每个片段的 provider 失败链按实际处理顺序追加到最终 `errors.json`。每项除通用字段外，还包含：

- `segment_index`：从 1 开始的片段编号。
- `time_range.start_seconds` / `time_range.end_seconds`：该片段在原视频中的时间范围。
- `attempt`：provider 尝试序号；仅在视觉路由报告提供时出现。

| 错误类型 | 含义 | 处理建议 |
| --- | --- | --- |
| `PROVIDER_RETRY` | 同一 provider 在上一次失败后实际发起了下一次尝试 | 检查下一条失败或最终成功结果；持续失败时降低输入大小或调整超时 |
| `PROVIDER_SWITCHED` | 当前 provider 失败后切换到下一备用 provider | 核对备用结果，同时保留前序失败链用于排障 |
| `VISUAL_SEGMENT_BUDGET_EXHAUSTED` | 当前片段获分配的调用次数已用完 | 提高 `vision.max_visual_calls`，或减少分段、重试和复核次数 |
| `VISUAL_BUDGET_INSUFFICIENT` | 工作流剩余预算不足以继续片段转写并保留最终综合调用 | 提高总预算或缩短视频 |
| `VISION_REPORT_MISSING` | 视觉路由结束后没有生成报告，实际调用数未知 | 检查子进程异常；为防重复计费，当前运行会保守耗尽剩余预算 |
| `VISION_REPORT_INVALID` | 报告 JSON 损坏、结构错误或缺少调用计数 | 检查版本与磁盘写入，删除损坏报告后重试 |
| `VISION_OUTPUT_MISSING` | 报告显示成功，但片段转写文件不存在 | 检查输出路径和磁盘权限后重试该片段 |
| `VISION_SEGMENT_FAILED` | 路由未成功，且报告没有给出更具体的 provider 错误 | 检查 provider 配置、额度和媒体兼容性 |
| `VIDEO_SEGMENT_ENCODING_FAILED` | FFmpeg 无法生成供原生视频模型读取的片段 | 检查 FFmpeg、源文件和磁盘空间 |
| `VISION_ROUTER_FAILED` | 视觉路由子进程启动失败或超时，调用数未知 | 核对运行环境和服务商额度后重试 |

报告中的凭据字段、Bearer token、常见 `sk-` 密钥和 URL 查询参数会在写入最终错误文件前再次脱敏。脱敏同时覆盖请求头、普通 `key=value` 以及 Python/JSON 字典中的 Cookie、API Key、Access/Refresh Token、Password、Client Secret 和 Secret Key。每份有效分段报告只会结算一次 `api_calls_used`；报告缺失或无法确定用量时，不会猜测并继续调用，而是保守耗尽当前工作流剩余预算。

## 终止报告

视觉链耗尽时至少说明：

- 已尝试的模型顺序。
- 每个模型的错误类型。
- 每类错误的建议处理方法。
- 宿主视觉是否可用、是否尝试、结果如何。
- 哪些文字或转写成果已经保留。
- 修复配置后应从哪个阶段恢复。
