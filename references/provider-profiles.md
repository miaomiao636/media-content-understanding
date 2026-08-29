# 视觉 Provider 请求配置

本文件记录会改变路由器请求方式的供应商差异。模型限制可能变化，升级模型或遇到 400 错误时先复核官方文档。

## `qwen-omni`

- 当前模型：`qwen3.5-omni-plus`。
- 认证：`Authorization: Bearer <key>`。
- Qwen-Omni 所有请求使用 SSE 流式返回；输出只需文字时设置 `modalities: ["text"]`。
- 图片使用 `image_url`；原生视频使用 `video_url`，支持公网 URL 或 Base64 Data URL。
- Qwen3.5-Omni 视频公网 URL 最大 2GB、最长 1 小时；Base64 编码结果需小于 10MB。
- 官方文档：[Qwen-Omni](https://help.aliyun.com/zh/model-studio/qwen-omni)。

## `xiaomi-mimo`

- 当前模型：`mimo-v2.5`。
- 认证：`api-key: <key>`。
- 使用 `max_completion_tokens`；视觉提炼默认关闭 thinking，避免为确定性画面描述增加成本和延迟。
- 图片使用 `image_url`；原生视频使用 `video_url`，可配置 `fps` 与 `media_resolution`。
- 公网视频最大 300MB；Base64 编码结果最大 50MB。
- 官方文档：[OpenAI 兼容接口](https://platform.xiaomimimo.com/docs/en-US/api/chat/openai-api)、[视频理解](https://platform.xiaomimimo.com/docs/en-US/usage-guide/multimodal-understanding/video-understanding)。

## 新增 Provider

优先使用 `standard`，仅当认证头、流式协议、媒体字段或输出结构确实不同，才新增 `request_profile`。新增后必须测试：单图、多图、原生视频（若声明支持）、认证失败、服务商失败后的下一模型切换。
