# 视觉路由

## 证据准备

1. 先生成低频故事板，快速理解全局画面。
2. 用字幕触发词、场景切换、OCR 异常和用户关注点定位时间段。
3. 为命中时段生成连续关键帧；动态效果额外保存短片。
4. 所有模型复用同一证据批次。

## 外部模型路由

1. 读取已启用 provider。
2. 根据输入筛选 `image`、`multi_image` 或 `video` 能力。
3. 按 `priority` 升序调用。
4. 认证和配置错误不重试；超时、网络、429 和 5xx 按配置重试。
5. 空响应或无法解析的响应重试一次后切换。
6. 第一个有效结果作为主结果；结果明确带有结构化 `low` 置信度标记时，让下一模型基于同一证据复核。
7. 转写、摘要、重试、故障切换和复核共享 `vision.max_visual_calls` 预算，每次 provider 尝试扣减一次。

建议使用：

```bash
python3 <skill_dir>/scripts/vision_router.py \
  --prompt-file visual-prompt.md \
  --image frame-001.jpg \
  --image frame-002.jpg \
  --output visual-analysis.md \
  --report vision-report.json
```

独立调用时可用 `--max-api-calls N` 进一步缩小本进程预算。报告中的 `api_calls_limit`、`api_calls_used` 和 `budget_exhausted` 可供上层工作流继续分配预算。`mcu analyze --config PATH` 会把同一配置路径传给全部视觉子流程。

原生视频：

```bash
python3 <skill_dir>/scripts/vision_router.py \
  --prompt-file visual-prompt.md \
  --video input.mp4 \
  --output visual-analysis.md \
  --report vision-report.json
```

也可使用 `--video-url` 传入服务商可访问的公网地址，或用 `--provider <id>` 单独验证某个模型。

## 原生视频与关键帧选择

- 媒体同时满足 provider 单项 Base64 限制和 `vision.max_upload_mb` 单次合计限制时，可直接发送本地视频以同时理解画面、声音和时间关系。
- 来源已公开且直链稳定时可使用视频 URL；不要为了调用模型擅自公开本地或私密视频。
- 视频过大、超时、格式不支持或不需要音频时，降级为“ASR + 关键帧/连续帧”。
- 原生视频理解结果不代替证据保存；仍按需要输出截图或短片，并记录时间点。

退出码：

| 退出码 | 含义 | Agent 动作 |
| --- | --- | --- |
| `0` | 外部模型成功 | 使用结果，保留失败历史 |
| `20` | 没有可用或能力匹配的外部模型 | 尝试宿主视觉 |
| `21` | 所有外部模型调用失败 | 报告错误后尝试宿主视觉 |
| `2` | 输入或配置无法解析 | 修正配置；视觉必要时停止 |

## 宿主视觉回退

- 当前 Agent 支持视觉：外部链耗尽后使用宿主视觉工具查看相同关键帧。
- 当前 Agent 不支持视觉：视觉必要时停止；视觉非必要时输出降级结果。
- 宿主视觉同样要返回画面描述、证据位置、置信度和未确认事项。

## 用户可见错误

每个 provider 失败后立即给出一条简短进度：

```text
视觉模型 <id> 失败：<错误类型>。
建议：<可执行处理方法>。
任务继续，下一步尝试 <next-id 或宿主视觉>。
```

最终 `errors.json` 保存完整但已脱敏的失败链。
