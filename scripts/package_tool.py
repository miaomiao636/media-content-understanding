#!/usr/bin/env python3
"""Initialize and validate media-analysis-package version 1.0."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt

try:
    from .claim_audit import audit_claims
except ImportError:
    from claim_audit import audit_claims

try:
    from .console import configure_utf8_stdio
except ImportError:
    from console import configure_utf8_stdio

configure_utf8_stdio()

VALID_STATUS = {"initialized", "partial", "completed", "failed_acquisition", "failed_visual"}
VALID_KIND = {"video", "gallery", "long_text", "mixed"}
VALID_MEDIA_TYPE = {"image", "clip"}

SUMMARY_SECTIONS: Sequence[Tuple[str, re.Pattern[str], str]] = (
    ("core_conclusion", re.compile(r"核心结论|一分钟", re.IGNORECASE), "缺少核心结论"),
    (
        "problem_and_scenario",
        re.compile(r"解决的问题|问题与适用场景|适用场景", re.IGNORECASE),
        "缺少解决的问题或适用场景",
    ),
    (
        "content_structure",
        re.compile(r"章节(?:结构)?|主题结构|内容结构", re.IGNORECASE),
        "缺少章节或主题结构",
    ),
    (
        "actions_and_parameters",
        re.compile(r"可执行步骤|操作步骤|关键步骤|步骤与参数|关键参数", re.IGNORECASE),
        "缺少可执行步骤或关键参数",
    ),
    (
        "visual_evidence",
        re.compile(r"视觉证据|截图与短片|截图或短片|证据作用", re.IGNORECASE),
        "缺少截图、短片或视觉证据说明",
    ),
    (
        "provenance_and_gaps",
        re.compile(r"来源与推断|来源说明|事实与推断|缺失信息", re.IGNORECASE),
        "缺少来源、推断或缺失信息说明",
    ),
    (
        "remaining_verification",
        re.compile(r"仍需验证|待验证|待确认|复刻前", re.IGNORECASE),
        "缺少复刻前仍需验证的事项",
    ),
)

VISUAL_TRIGGER_RE = re.compile(
    r"这个效果|像这样|这里可以看到|画面|界面|布局|图表|代码|"
    r"动画|转场|交互|前后变化|状态变化|演示过程"
)
DYNAMIC_TRIGGER_RE = re.compile(r"动画|转场|交互|前后变化|状态变化|演示过程")
UNREVIEWED_IMAGE_ANALYSIS_MARKERS = (
    "尚未校订。",
    "尚未校订或无必要推断。",
    "未取得可校订的 OCR 结果。",
    "当前结果需由宿主 Agent 或人工对照原图校订。",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: Any) -> None:
    """Replace a JSON file in one filesystem operation after the full payload is ready."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def safe_relative(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"路径越出理解包：{value}")
    return path


def _media_url(value: str) -> str:
    """Return a browser-safe package-relative URL without changing its filesystem path."""
    return quote(value.replace("\\", "/"), safe="/._-~")


def _media_caption(item: Dict[str, Any]) -> str:
    location = item.get("time_range") or item.get("timestamp")
    if not location and item.get("image_index"):
        location = f"第 {item['image_index']} 张"
    pieces = [str(value).strip() for value in (location, item.get("reason"), item.get("description")) if str(value or "").strip()]
    return " · ".join(pieces)


def _configure_safe_image_renderer(markdown: MarkdownIt, root: Path) -> None:
    default_image = markdown.renderer.rules["image"]

    def render_image(tokens: Any, index: int, options: Any, env: Any) -> str:
        token = tokens[index]
        source = str(token.attrGet("src") or "")
        parsed = urlsplit(source)
        normalized = unquote(parsed.path).replace("\\", "/")
        safe = bool(normalized) and not (
            parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
        )
        if safe:
            try:
                safe = safe_relative(root, normalized).is_file()
            except ValueError:
                safe = False
        if not safe:
            alt = html.escape(str(token.content or "图片"))
            return f'<span class="blocked-image" role="note">[已阻止不安全图片：{alt}]</span>'
        token.attrSet("src", _media_url(normalized))
        token.attrSet("loading", "lazy")
        return default_image(tokens, index, options, env)

    markdown.renderer.rules["image"] = render_image


def render_summary_html(root: Path, manifest: Dict[str, Any]) -> str:
    """Render a safe, standalone HTML reading view for one analysis package."""
    root = root.expanduser().resolve()
    content = manifest.get("content") if isinstance(manifest.get("content"), dict) else {}
    summary_value = str(content.get("summary_file") or "summary.md")
    summary_path = safe_relative(root, summary_value)
    summary = summary_path.read_text(encoding="utf-8")

    markdown = MarkdownIt(
        "commonmark",
        {"html": False, "linkify": False, "typographer": False},
    ).enable("table")
    _configure_safe_image_renderer(markdown, root)
    rendered_summary = markdown.render(summary)
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    title = str(source.get("title") or "内容提炼").strip() or "内容提炼"

    evidence: List[str] = []
    for item in manifest.get("media") or []:
        if not isinstance(item, dict) or item.get("type") not in VALID_MEDIA_TYPE:
            continue
        value = str(item.get("path") or "").strip()
        if not value:
            continue
        media_path = safe_relative(root, value)
        if not media_path.is_file():
            continue
        url = html.escape(_media_url(value), quote=True)
        caption = html.escape(_media_caption(item))
        if item["type"] == "image":
            # Images already embedded in Markdown need not be repeated in the evidence gallery.
            if value in summary or _media_url(value) in summary:
                continue
            evidence.append(
                '<figure class="evidence-item evidence-image">'
                f'<img src="{url}" alt="{html.escape(str(item.get("reason") or "视觉证据"), quote=True)}" loading="lazy">'
                f"<figcaption>{caption}</figcaption>"
                "</figure>"
            )
        else:
            evidence.append(
                '<figure class="evidence-item evidence-clip">'
                '<video controls preload="metadata">'
                f'<source src="{url}" type="video/mp4">'
                f'当前浏览器无法直接播放该短片。<a href="{url}">单独打开视频</a>。'
                "</video>"
                f"<figcaption>{caption}</figcaption>"
                "</figure>"
            )
    evidence_section = ""
    if evidence:
        evidence_section = (
            '<section class="evidence-gallery" aria-labelledby="evidence-title">'
            '<h2 id="evidence-title">媒体证据</h2>'
            + "".join(evidence)
            + "</section>"
        )

    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
    body {{ margin: 0; background: #f5f7fa; color: #18202a; line-height: 1.72; }}
    main {{ box-sizing: border-box; width: min(980px, calc(100% - 32px)); margin: 32px auto; padding: clamp(24px, 5vw, 56px); background: #fff; border-radius: 18px; box-shadow: 0 14px 42px rgba(26, 38, 56, .10); }}
    h1, h2, h3 {{ line-height: 1.3; color: #111827; }}
    h2 {{ margin-top: 2em; padding-bottom: .35em; border-bottom: 1px solid #e5e7eb; }}
    a {{ color: #1268c4; }}
    code {{ padding: .12em .38em; border-radius: 5px; background: #eef2f7; }}
    pre {{ overflow-x: auto; padding: 16px; border-radius: 10px; background: #111827; color: #e5e7eb; }}
    pre code {{ padding: 0; background: transparent; }}
    blockquote {{ margin-inline: 0; padding: .2em 1em; border-left: 4px solid #8aa4c2; color: #4b5563; }}
    table {{ width: 100%; border-collapse: collapse; margin: 1.25em 0; }}
    th, td {{ padding: 10px 12px; border: 1px solid #dfe4ea; text-align: left; vertical-align: top; }}
    th {{ background: #f0f4f8; }}
    img, video {{ display: block; width: 100%; height: auto; max-height: 76vh; margin: 18px auto; border-radius: 12px; background: #0b0f14; object-fit: contain; }}
    .evidence-gallery {{ margin-top: 3em; }}
    .evidence-item {{ margin: 24px 0 36px; }}
    figcaption {{ color: #596579; font-size: .93rem; }}
    .blocked-image {{ display: inline-block; padding: .3em .55em; border: 1px solid #d7a35b; border-radius: 6px; color: #8a5715; }}
    .reading-note {{ margin-top: 48px; color: #778195; font-size: .86rem; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #0d1117; color: #dbe3ec; }}
      main {{ background: #161b22; box-shadow: none; }}
      h1, h2, h3 {{ color: #f0f6fc; }}
      h2 {{ border-color: #30363d; }}
      th, td {{ border-color: #30363d; }}
      th, code {{ background: #21262d; }}
      blockquote, figcaption {{ color: #9da7b3; }}
    }}
    @media print {{ body {{ background: #fff; }} main {{ width: 100%; margin: 0; padding: 0; box-shadow: none; }} video {{ max-height: 420px; }} }}
  </style>
</head>
<body>
  <main>
    <article>{rendered_summary}</article>
    {evidence_section}
    <p class="reading-note">本页由理解包中的 summary.md 和媒体证据自动生成；修改 Markdown 后请重新执行 finalize。</p>
  </main>
</body>
</html>
"""


def write_summary_html(root: Path, manifest: Optional[Dict[str, Any]] = None) -> Path:
    """Write summary.html and declare it in the in-memory manifest."""
    root = root.expanduser().resolve()
    if manifest is None:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    content = manifest.setdefault("content", {})
    if not isinstance(content, dict):
        raise ValueError("manifest.content 必须是对象")
    html_value = str(content.get("summary_html_file") or "summary.html")
    html_path = safe_relative(root, html_value)
    content["summary_html_file"] = html_value
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_summary_html(root, manifest), encoding="utf-8", newline="\n")
    return html_path


def initialize(args: argparse.Namespace) -> int:
    root = Path(args.package_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.content_type == "video":
        (root / "media" / "images").mkdir(parents=True, exist_ok=True)
        (root / "media" / "clips").mkdir(parents=True, exist_ok=True)
    elif args.content_type in {"gallery", "mixed"}:
        (root / "media" / "images").mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists() and not args.force:
        print(f"manifest.json 已存在：{manifest_path}", file=sys.stderr)
        return 2
    manifest = {
        "schema_version": "1.0",
        "package_type": "media-analysis-package",
        "status": "initialized",
        "source": {
            "input_url": args.source_url,
            "canonical_url": "",
            "platform": args.platform,
            "source_id": args.source_id,
            "title": "",
            "author": "",
            "published_at": "",
        },
        "content": {
            "kind": args.content_type,
            "language": "",
            "focus": args.focus or "",
            "summary_file": "summary.md",
            "summary_html_file": "summary.html",
            "source_content_file": "source-content.md",
        },
        "chapters": [],
        "media": [],
        "limitations": [],
        "processing": {
            "acquisition_method": "",
            "vision_provider": "",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
        "errors_file": "errors.json",
    }
    if args.content_type == "video":
        manifest["content"]["transcript_file"] = "transcript.md"
        manifest["processing"]["transcription_method"] = ""
    atomic_write_json(manifest_path, manifest)
    (root / "summary.md").write_text("# 内容提炼\n\n", encoding="utf-8")
    (root / "source-content.md").write_text("# 来源内容\n\n", encoding="utf-8")
    if manifest["content"].get("transcript_file"):
        (root / "transcript.md").write_text("# 时间戳转写\n\n", encoding="utf-8")
    (root / "errors.json").write_text("[]\n", encoding="utf-8")
    write_summary_html(root, manifest)
    print(root)
    return 0


def validate(root: Path) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "errors": ["缺少 manifest.json"], "warnings": []}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "errors": [f"manifest.json 无效：{exc}"], "warnings": []}

    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version 必须是 1.0")
    if manifest.get("package_type") != "media-analysis-package":
        errors.append("package_type 必须是 media-analysis-package")
    if manifest.get("status") not in VALID_STATUS:
        errors.append("status 无效")
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    for field in ("input_url", "platform", "source_id"):
        if not str(source.get(field) or "").strip():
            errors.append(f"source.{field} 不能为空")
    content = manifest.get("content") if isinstance(manifest.get("content"), dict) else {}
    content_kind = content.get("kind")
    if content_kind not in VALID_KIND:
        errors.append("content.kind 无效")
    processing = manifest.get("processing") if isinstance(manifest.get("processing"), dict) else {}
    if content_kind in {"gallery", "long_text", "mixed"}:
        if "transcript_file" in content:
            errors.append("非视频包不得声明 content.transcript_file")
        if (root / "transcript.md").exists():
            errors.append("非视频包不得包含 transcript.md")
        if "transcription_method" in processing:
            errors.append("非视频包不得声明 processing.transcription_method")
    summary_value = str(content.get("summary_file") or "")
    if not summary_value:
        errors.append("content.summary_file 不能为空")
    else:
        try:
            summary_path = safe_relative(root, summary_value)
            if not summary_path.is_file():
                errors.append(f"摘要文件不存在：{summary_value}")
            elif manifest.get("status") == "completed":
                body = re.sub(
                    r"^#.*$", "", summary_path.read_text(encoding="utf-8"), flags=re.MULTILINE
                ).strip()
                if not body:
                    errors.append("completed 包的 summary.md 不能为空")
        except ValueError as exc:
            errors.append(str(exc))

    html_value = content.get("summary_html_file")
    if html_value is not None:
        if not isinstance(html_value, str) or not html_value.strip():
            errors.append("content.summary_html_file 必须是非空字符串")
        else:
            try:
                html_path = safe_relative(root, html_value)
                if not html_path.is_file():
                    errors.append(f"HTML 阅读版不存在：{html_value}")
                elif not html_path.read_text(encoding="utf-8").strip():
                    errors.append(f"HTML 阅读版为空：{html_value}")
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                errors.append(f"HTML 阅读版无效：{exc}")

    for index, item in enumerate(manifest.get("media") or []):
        if not isinstance(item, dict):
            errors.append(f"media[{index}] 必须是对象")
            continue
        if item.get("type") not in VALID_MEDIA_TYPE:
            errors.append(f"media[{index}].type 无效")
        if not item.get("reason") or not item.get("description"):
            errors.append(f"media[{index}] 缺少 reason 或 description")
        if not item.get("timestamp") and not item.get("time_range") and not item.get("image_index"):
            errors.append(f"media[{index}] 缺少时间点、时间范围或图片序号")
        if content_kind in {"gallery", "long_text", "mixed"}:
            if item.get("type") != "image":
                errors.append(f"media[{index}] 非视频包只能登记 image 证据")
            if not item.get("image_index"):
                errors.append(f"media[{index}] 非视频图片缺少 image_index")
            if "timestamp" in item or "time_range" in item:
                errors.append(f"media[{index}] 非视频图片不得声明时间点或时间范围")
        try:
            media_path = safe_relative(root, str(item.get("path") or ""))
            if not media_path.is_file():
                errors.append(f"媒体文件不存在：{item.get('path')}")
        except ValueError as exc:
            errors.append(str(exc))

    errors_file = str(manifest.get("errors_file") or "errors.json")
    try:
        error_path = safe_relative(root, errors_file)
        payload = json.loads(error_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            errors.append("errors.json 必须是数组")
        else:
            for index, item in enumerate(payload):
                if not isinstance(item, dict):
                    errors.append(f"errors[{index}] 必须是对象")
                    continue
                for field in ("type", "suggestion"):
                    if not item.get(field):
                        errors.append(f"errors[{index}] 缺少 {field}")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"错误报告无效：{exc}")

    if manifest.get("status") in {"partial", "failed_acquisition", "failed_visual"} and not manifest.get(
        "limitations"
    ):
        warnings.append("非完成状态建议填写 limitations")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _read_optional_text(root: Path, value: Any) -> str:
    if not str(value or "").strip():
        return ""
    try:
        path = safe_relative(root, str(value))
    except ValueError:
        return ""
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""


def _evidence_source(
    text: str, source_type: str, label: str, trust: int
) -> Optional[Dict[str, Any]]:
    if not text.strip():
        return None
    return {"text": text, "source_type": source_type, "label": label, "trust": trust}


def _collect_claim_evidence(
    root: Path, manifest: Dict[str, Any], content: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Collect actual package sources without allowing derived media prose to mask originals."""
    sources: List[Dict[str, Any]] = []

    def add(text: str, source_type: str, label: str, trust: int) -> None:
        source = _evidence_source(text, source_type, label, trust)
        if source is not None:
            sources.append(source)

    source_path = content.get("source_content_file") or "source-content.md"
    transcript_path = content.get("transcript_file")
    if transcript_path is None and content.get("kind") == "video":
        transcript_path = "transcript.md"
    add(_read_optional_text(root, source_path), "source_content", str(source_path), 100)
    if transcript_path:
        add(_read_optional_text(root, transcript_path), "transcript", str(transcript_path), 100)

    image_analysis_path = content.get("image_analysis_file")
    if isinstance(image_analysis_path, str) and image_analysis_path.strip():
        add(
            _read_optional_text(root, image_analysis_path),
            "image_analysis",
            image_analysis_path,
            60,
        )

    declared_step_files: List[str] = []
    for owner in (content, manifest):
        step_file = owner.get("steps_file")
        if isinstance(step_file, str) and step_file.strip():
            declared_step_files.append(step_file)
        step_files = owner.get("steps_files")
        if isinstance(step_files, list):
            declared_step_files.extend(str(item) for item in step_files if str(item).strip())
    conventional_steps = root / "steps.md"
    if conventional_steps.is_file():
        declared_step_files.append("steps.md")
    seen_step_files = set()
    for step_file in declared_step_files:
        if step_file in seen_step_files:
            continue
        seen_step_files.add(step_file)
        add(_read_optional_text(root, step_file), "steps", step_file, 90)

    for owner_name, owner in (("content", content), ("manifest", manifest)):
        steps = owner.get("steps")
        if isinstance(steps, (list, dict)):
            add(
                json.dumps(steps, ensure_ascii=False),
                "steps",
                f"manifest.json:{owner_name}.steps",
                90,
            )
    chapters = manifest.get("chapters")
    if isinstance(chapters, list):
        add(
            json.dumps(chapters, ensure_ascii=False),
            "chapters",
            "manifest.json:chapters",
            85,
        )

    source_metadata = manifest.get("source")
    if isinstance(source_metadata, dict):
        selected_metadata = {
            key: source_metadata.get(key)
            for key in ("title", "author", "published_at")
            if source_metadata.get(key)
        }
        if selected_metadata:
            add(
                json.dumps(selected_metadata, ensure_ascii=False),
                "source_metadata",
                "manifest.json:source",
                95,
            )

    media = manifest.get("media")
    if isinstance(media, list):
        for index, item in enumerate(media):
            if not isinstance(item, dict):
                continue
            derived_text = "\n".join(
                str(item.get(key) or "") for key in ("reason", "description") if item.get(key)
            )
            add(
                derived_text,
                "media_description",
                f"manifest.json:media[{index}]",
                60,
            )
    return sources


def _summary_gate(summary: str) -> Tuple[List[Dict[str, str]], List[str]]:
    blockers: List[Dict[str, str]] = []
    body = re.sub(r"^#.*$", "", summary, flags=re.MULTILINE)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
    if len(body) < 40:
        blockers.append({"code": "SUMMARY_INCOMPLETE", "message": "summary.md 缺少足够的实质内容"})
    if "当前为自动准备稿" in summary:
        blockers.append({"code": "SUMMARY_IS_DRAFT", "message": "summary.md 仍是自动准备稿"})

    heading_matches = list(re.finditer(r"^#{1,6}\s+(.+?)\s*$", summary, re.MULTILINE))
    headings = [
        (
            match.group(1).strip(),
            match.end(),
            heading_matches[index + 1].start() if index + 1 < len(heading_matches) else len(summary),
        )
        for index, match in enumerate(heading_matches)
    ]
    present: List[str] = []
    for code, pattern, missing_message in SUMMARY_SECTIONS:
        matching = [(title, start, end) for title, start, end in headings if pattern.search(title)]
        if not matching:
            blockers.append({"code": f"STRUCTURE_{code.upper()}_MISSING", "message": missing_message})
            continue
        present.append(code)
        if not any(re.sub(r"\s+", "", summary[start:end]) for _title, start, end in matching):
            blockers.append(
                {"code": f"STRUCTURE_{code.upper()}_EMPTY", "message": f"{matching[0][0]} 章节不能为空"}
            )
    return blockers, present


def _required_visual_types(
    manifest: Dict[str, Any], evidence_text: str, visual_mode: str
) -> List[str]:
    content = manifest.get("content") if isinstance(manifest.get("content"), dict) else {}
    declared = content.get("required_visual_evidence")
    if isinstance(declared, str):
        declared_types = [declared]
    elif isinstance(declared, list):
        declared_types = [str(item) for item in declared]
    else:
        declared_types = []
    declared_types = [item for item in declared_types if item in VALID_MEDIA_TYPE]
    if declared_types:
        return sorted(set(declared_types))
    if visual_mode == "required":
        return ["image_or_clip"]
    if visual_mode == "not-required":
        return []
    explicit = content.get("visual_evidence_required")
    if explicit is False:
        return []
    if explicit is True:
        return ["image_or_clip"]
    if content.get("kind") in {"gallery", "mixed"}:
        return ["image_or_clip"]
    if DYNAMIC_TRIGGER_RE.search(evidence_text):
        return ["clip"]
    if VISUAL_TRIGGER_RE.search(evidence_text):
        return ["image_or_clip"]
    return []


def _image_analysis_gate(
    root: Path, content: Dict[str, Any]
) -> List[Dict[str, str]]:
    if content.get("kind") not in {"gallery", "mixed"}:
        return []
    analysis_path = content.get("image_analysis_file")
    if not isinstance(analysis_path, str) or not analysis_path.strip():
        return [
            {
                "code": "IMAGE_ANALYSIS_MISSING",
                "message": "图文包缺少 image-analysis.md 派生分析层",
            }
        ]
    analysis_text = _read_optional_text(root, analysis_path)
    if not analysis_text.strip():
        return [
            {
                "code": "IMAGE_ANALYSIS_MISSING",
                "message": "图文包的图片分析文件为空或不可读",
            }
        ]
    if any(marker in analysis_text for marker in UNREVIEWED_IMAGE_ANALYSIS_MARKERS):
        return [
            {
                "code": "IMAGE_ANALYSIS_REVIEW_REQUIRED",
                "message": "图片 OCR 或画面分析仍含未校订占位内容",
            }
        ]
    return []


def finalize_package(
    root: Path,
    *,
    visual_mode: str = "auto",
    require_visual_evidence: Optional[bool] = None,
) -> Dict[str, Any]:
    """Evaluate every completion gate, then atomically publish one manifest status."""
    root = root.expanduser().resolve()
    manifest_path = root / "manifest.json"
    if require_visual_evidence is not None:
        visual_mode = "required" if require_visual_evidence else "not-required"
    if visual_mode not in {"auto", "required", "not-required"}:
        raise ValueError("未知视觉证据模式")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "partial", "blockers": [{"code": "MANIFEST_INVALID", "message": str(exc)}]}
    if not isinstance(manifest, dict):
        return {
            "ok": False,
            "status": "partial",
            "blockers": [{"code": "MANIFEST_INVALID", "message": "manifest.json 顶层必须是对象"}],
        }

    content = manifest.get("content") if isinstance(manifest.get("content"), dict) else {}
    summary = _read_optional_text(root, content.get("summary_file") or "summary.md")
    evidence_sources = _collect_claim_evidence(root, manifest, content)
    source_text = next(
        (item["text"] for item in evidence_sources if item["source_type"] == "source_content"), ""
    )
    transcript_text = next(
        (item["text"] for item in evidence_sources if item["source_type"] == "transcript"), ""
    )

    html_blockers: List[Dict[str, str]] = []
    try:
        write_summary_html(root, manifest)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        html_blockers.append(
            {"code": "HTML_RENDER_FAILED", "message": f"HTML 阅读版生成失败：{exc}"}
        )

    blockers, present_sections = _summary_gate(summary)
    blockers[:0] = html_blockers
    blockers.extend(_image_analysis_gate(root, content))
    required_visual_types = _required_visual_types(
        manifest, "\n".join((source_text, transcript_text)), visual_mode
    )
    media = [item for item in manifest.get("media") or [] if isinstance(item, dict)]
    available_types = {str(item.get("type")) for item in media}
    for required_type in required_visual_types:
        if required_type == "image_or_clip" and not (available_types & VALID_MEDIA_TYPE):
            blockers.append(
                {"code": "VISUAL_EVIDENCE_MISSING", "message": "内容需要视觉证据，但包内没有可用截图或短片"}
            )
        elif required_type in VALID_MEDIA_TYPE and required_type not in available_types:
            blockers.append(
                {
                    "code": "DYNAMIC_VISUAL_EVIDENCE_MISSING"
                    if required_type == "clip"
                    else "VISUAL_EVIDENCE_MISSING",
                    "message": f"内容需要 {required_type} 证据，但 manifest.media 中没有该类型",
                }
            )

    claim_audit = audit_claims(summary, evidence_sources)
    if claim_audit["severe_conflict_count"]:
        blockers.append(
            {
                "code": "SEVERE_CLAIM_CONFLICT",
                "message": f"事实审计发现 {claim_audit['severe_conflict_count']} 个严重冲突",
            }
        )

    # Structural validation is evaluated before publishing completed, but the current partial
    # status is intentionally not treated as a failure during this transition.
    structural = validate(root)
    for message in structural.get("errors", []):
        blockers.append({"code": "PACKAGE_STRUCTURE_INVALID", "message": str(message)})

    passed = not blockers
    finalization = {
        "schema_version": "package-finalization/v1",
        "status": "passed" if passed else "blocked",
        "checked_at": now_iso(),
        "blockers": blockers,
        "gates": {
            "summary": not any(item["code"].startswith("SUMMARY_") for item in blockers),
            "structure": not any(
                item["code"].startswith(("STRUCTURE_", "PACKAGE_STRUCTURE_")) for item in blockers
            ),
            "visual_evidence": not any(
                "VISUAL_EVIDENCE" in item["code"]
                or item["code"].startswith("IMAGE_ANALYSIS_")
                for item in blockers
            ),
            "severe_claim_conflicts": claim_audit["severe_conflict_count"] == 0,
        },
        "summary_sections": present_sections,
        "required_visual_types": required_visual_types,
        "claim_audit": claim_audit,
    }
    manifest["status"] = "completed" if passed else "partial"
    manifest["finalization"] = finalization
    processing = manifest.get("processing")
    if isinstance(processing, dict):
        processing["updated_at"] = finalization["checked_at"]
    atomic_write_json(manifest_path, manifest)
    return {
        "ok": passed,
        "status": manifest["status"],
        "package_dir": str(root),
        "blockers": blockers,
        "gates": finalization["gates"],
        "claim_audit": claim_audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("package_dir")
    init.add_argument("--source-url", required=True)
    init.add_argument("--platform", required=True)
    init.add_argument("--source-id", required=True)
    init.add_argument("--content-type", choices=sorted(VALID_KIND), required=True)
    init.add_argument("--focus")
    init.add_argument("--force", action="store_true")
    check = sub.add_parser("validate")
    check.add_argument("package_dir")
    finalize = sub.add_parser("finalize")
    finalize.add_argument("package_dir")
    finalize.add_argument(
        "--visual-evidence",
        choices=("auto", "required", "not-required"),
        default="auto",
        help="必要视觉证据的判定方式",
    )
    finalize.add_argument(
        "--require-visual-evidence",
        action="store_const",
        const="required",
        dest="visual_evidence",
        help="强制要求至少一项视觉证据",
    )
    finalize.add_argument(
        "--no-require-visual-evidence",
        action="store_const",
        const="not-required",
        dest="visual_evidence",
        help="明确声明当前内容不需要视觉证据",
    )
    render_html = sub.add_parser("render-html")
    render_html.add_argument("package_dir")
    args = parser.parse_args()
    if args.command == "init":
        return initialize(args)
    if args.command == "finalize":
        result = finalize_package(
            Path(args.package_dir).expanduser().resolve(), visual_mode=args.visual_evidence
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 3
    if args.command == "render-html":
        root = Path(args.package_dir).expanduser().resolve()
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        html_path = write_summary_html(root, manifest)
        processing = manifest.get("processing")
        if isinstance(processing, dict):
            processing["updated_at"] = now_iso()
        atomic_write_json(manifest_path, manifest)
        print(json.dumps({"ok": True, "path": str(html_path)}, ensure_ascii=False, indent=2))
        return 0
    result = validate(Path(args.package_dir).expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
