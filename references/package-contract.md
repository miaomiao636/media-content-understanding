# 媒体理解包契约 1.0

## 目录

```text
media-analysis-package/
├── manifest.json
├── summary.md
├── source-content.md
├── transcript.md          # 没有音视频时可省略
├── transcript.raw.json    # 有原始时间轴时保留
├── errors.json
└── media/
    ├── images/
    └── clips/
```

## manifest.json 必填字段

- `schema_version`: 当前为 `1.0`。
- `package_type`: `media-analysis-package`。
- `status`: `initialized`、`partial`、`completed`、`failed_acquisition` 或 `failed_visual`。
- `source.input_url`。
- `source.platform`。
- `source.source_id`：平台无法提供时可使用内容哈希，但要标注生成方式。
- `content.kind`: `video`、`gallery`、`long_text` 或 `mixed`。
- `content.summary_file`。
- `media`: 视觉证据数组。
- `limitations`: 缺失信息数组。

## media 项

每项至少包含：

```json
{
  "path": "media/images/001-final-effect.png",
  "type": "image",
  "timestamp": "00:42",
  "reason": "展示文字无法表达的最终布局",
  "description": "三列卡片在深色背景上呈现蓝紫色外发光"
}
```

动态片段使用 `time_range` 替代 `timestamp`。

## errors.json

必须是数组。每项至少包含 `stage`、`provider`、`type`、`message`、`suggestion`、`retryable` 和 `occurred_at`。错误信息必须脱敏。

## 验收

`completed` 包必须有非空 `summary.md`。所有 `media[].path` 必须位于包内且真实存在。运行 `package_tool.py validate` 进行确定性检查。
