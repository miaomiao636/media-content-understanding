#!/usr/bin/env python3
"""Initialize and validate media-analysis-package version 1.0."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from console import configure_utf8_stdio

configure_utf8_stdio()

VALID_STATUS = {"initialized", "partial", "completed", "failed_acquisition", "failed_visual"}
VALID_KIND = {"video", "gallery", "long_text", "mixed"}
VALID_MEDIA_TYPE = {"image", "clip"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_relative(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        raise ValueError(f"路径越出理解包：{value}")
    return path


def initialize(args: argparse.Namespace) -> int:
    root = Path(args.package_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "media" / "images").mkdir(parents=True, exist_ok=True)
    (root / "media" / "clips").mkdir(parents=True, exist_ok=True)
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
            "source_content_file": "source-content.md",
            "transcript_file": "" if args.content_type in {"gallery", "long_text"} else "transcript.md",
        },
        "chapters": [],
        "media": [],
        "limitations": [],
        "processing": {
            "acquisition_method": "",
            "transcription_method": "",
            "vision_provider": "",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        },
        "errors_file": "errors.json",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "summary.md").write_text("# 内容提炼\n\n", encoding="utf-8")
    (root / "source-content.md").write_text("# 来源内容\n\n", encoding="utf-8")
    if manifest["content"]["transcript_file"]:
        (root / "transcript.md").write_text("# 时间戳转写\n\n", encoding="utf-8")
    (root / "errors.json").write_text("[]\n", encoding="utf-8")
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
    if content.get("kind") not in VALID_KIND:
        errors.append("content.kind 无效")
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
    args = parser.parse_args()
    if args.command == "init":
        return initialize(args)
    result = validate(Path(args.package_dir).expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
