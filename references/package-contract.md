# 媒体理解包契约 1.0

## 目录

```text
media-analysis-package/
├── manifest.json
├── summary.md
├── summary.html            # 浏览器阅读版
├── source-content.md
├── image-analysis.md      # 有图片的非视频内容
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
- 新生成的理解包声明 `content.summary_html_file`，默认为 `summary.html`。旧包不声明该字段时仍可验证；一旦声明，文件必须位于包内、存在且非空。
- `media`: 视觉证据数组。
- `limitations`: 缺失信息数组。

### 非视频字段约束

`long_text`、`gallery` 和抖音图文 `mixed` 都是无音视频包：

- 不创建 `transcript.md` 或 `media/clips/`。
- `content` 不声明 `transcript_file`，`processing` 不声明 `transcription_method`。
- `manifest.media` 只能登记 `image`，用从 1 开始的 `image_index` 表示原始顺序，不使用 `timestamp` 或 `time_range`。
- 有图片时 `content.image_analysis_file` 指向 `image-analysis.md`。`content.provenance_layers` 明确区分 `author_body`、`image_ocr`、`visual_inference` 和 `summary`；OCR 与推断都标记为派生内容。

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

非视频图片使用 `image_index` 替代时间字段：

```json
{
  "path": "media/images/001.webp",
  "type": "image",
  "image_index": 1,
  "reason": "保留作者图片 1 的原始顺序",
  "description": "图片证据；OCR 与视觉推断在派生层中记录"
}
```

## errors.json

必须是数组。每项至少包含 `stage`、`provider`、`type`、`message`、`suggestion`、`retryable` 和 `occurred_at`。错误信息必须脱敏。

## 验收

`completed` 包必须有非空 `summary.md`。所有 `media[].path` 必须位于包内且真实存在。`package_tool.py validate` 还会检查已声明的 HTML 阅读版，并拒绝非视频包中的 transcript 声明、短片、时间点和时间范围。

### HTML 阅读版

`summary.html` 是由 `summary.md` 和 `manifest.media` 生成的派生展示层：

- Markdown 标题、列表、表格、链接和包内图片会渲染为浏览器页面。
- `manifest.media` 中未在 Markdown 正文引用的图片会加入证据区。
- `clip` 证据会生成 `<video controls preload="metadata">`，用相对路径播放包内 MP4。
- 原始 HTML 默认被转义，页面使用限制性 CSP，不嵌入 JavaScript，不依赖在线 CDN。
- `analyze` 会在完成证据登记后生成 HTML；`finalize` 会根据最新 Markdown 重新生成。也可单独执行 `python3 scripts/package_tool.py render-html <package_dir>`。

### 完成状态门禁

宿主 Agent 校订摘要后必须运行：

```bash
python3 scripts/mcu.py finalize <package_dir>
```

门禁必须在同一次判定中同时通过：

1. `summary.md` 是实质性摘要，不是自动准备稿。
2. 摘要包含核心结论、问题/场景、章节/主题结构、步骤/参数、视觉证据、来源/推断/缺失信息和复刻前待验证事项。
3. 当来源文字、转写或 `content.visual_evidence_required` 表明必须看画面时，`manifest.media` 包含真实存在的必要截图或短片。`content.required_visual_evidence` 可显式声明 `image` 或 `clip`。
4. `gallery` 和 `mixed` 包的 `image-analysis.md` 不得仍含“尚未校订”等占位内容；只改写 `summary.md` 不能绕过图片 OCR 与画面分析复核。
5. 事实审计没有严重冲突。审计结构固定覆盖数字范围、金额、百分比、时长、版本号和模型/软件名（包括无版本号的 `Claude`、`Gemini`、`Microsoft Excel` 等常见名称）；例如来源的 `300-3000` 与摘要的 `300-30000` 会被判定为严重冲突。

事实审计的实际来源包括 `source-content.md`、`transcript.md`、声明的步骤文件或约定的 `steps.md`、`content.steps`/`manifest.steps`、`chapters`、来源元数据和 `media[].reason/description`。正文与转写属于直接来源，媒体描述属于派生来源。判定每个摘要声明时必须检查全部同主题证据；在派生来源中找到同值，不得忽略直接来源或其他来源中的矛盾值。版本号会绑定最近的软件对象；同一证据来源中并列出现且包含摘要精确值的多个正确版本、金额、时长、百分比或软件名称，不得互相制造严重冲突。不同证据来源仍按来源可信度和对象对齐检查真实矛盾。

程序在完成所有判定后，使用同目录临时文件与原子替换一次写入 `manifest.json`。通过时状态为 `completed`；失败时状态为 `partial`，并在 `finalization.blockers` 列出结构化阻断项。`finalization.claim_audit` 保留结构化声明、匹配、未支持项和冲突。

`--visual-evidence required` 可强制要求至少一项视觉证据；`--visual-evidence not-required` 只应在人工确认画面不承载必要信息时使用。
