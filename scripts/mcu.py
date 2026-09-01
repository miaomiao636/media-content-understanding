#!/usr/bin/env python3
"""Unified command-line entrypoint for the public Agent Skill."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from .asr_router import TranscriptionError, TranscriptResult, TranscriptSegment, get_transcript
    from .cleanup import clean_cache, ensure_managed_root, finish_job, register_job
    from .config_loader import load_config
    from .console import configure_utf8_stdio
    from .evidence_selector import build_evidence_plan
    from .package_tool import finalize_package, write_summary_html
    from .sanitization import sanitize_error_text
    from .source_adapter import (
        AcquiredSource,
        AcquisitionError,
        SourceRouter,
        browser_profile_contains_project,
        default_adapters,
        is_managed_browser_profile,
        resolve_share_url,
    )
except ImportError:
    from asr_router import TranscriptionError, TranscriptResult, TranscriptSegment, get_transcript
    from cleanup import clean_cache, ensure_managed_root, finish_job, register_job
    from config_loader import load_config
    from console import configure_utf8_stdio
    from evidence_selector import build_evidence_plan
    from package_tool import finalize_package, write_summary_html
    from sanitization import sanitize_error_text
    from source_adapter import (
        AcquiredSource,
        AcquisitionError,
        SourceRouter,
        browser_profile_contains_project,
        default_adapters,
        is_managed_browser_profile,
        resolve_share_url,
    )


configure_utf8_stdio()

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class AnalyzeOptions:
    asr_mode: str
    asr_model: str
    language: str
    storyboard_interval: float
    max_frames: int


@dataclass
class VisualCallBudget:
    """Shared conservative budget across transcription, summary, retries and failover."""

    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("vision.max_visual_calls 必须大于 0")

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def consume_report(self, report: Dict[str, Any]) -> None:
        calls = report.get("api_calls_used")
        if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
            self.used = self.limit
            return
        self.used = min(self.limit, self.used + calls)

    def exhaust_unknown(self) -> None:
        """Prevent further calls when a child may have called providers but did not report usage."""
        self.used = self.limit

    def snapshot(self) -> Dict[str, int]:
        return {"limit": self.limit, "used": self.used, "remaining": self.remaining}


def vision_router_command(
    config_path: Optional[Path], budget: VisualCallBudget, *, max_calls: Optional[int] = None
) -> List[str]:
    command = [sys.executable, str(HERE / "vision_router.py")]
    if config_path is not None:
        command.extend(["--config", str(config_path)])
    allowed = budget.remaining if max_calls is None else min(budget.remaining, max_calls)
    if allowed <= 0:
        raise ValueError("视觉调用预算已经耗尽")
    command.extend(["--max-api-calls", str(allowed)])
    return command


def resolve_analyze_options(args: argparse.Namespace, config: Dict[str, Any]) -> AnalyzeOptions:
    """Resolve CLI overrides over user configuration and built-in defaults."""
    asr_config = config.get("asr", {}) if isinstance(config.get("asr"), dict) else {}
    vision_config = config.get("vision", {}) if isinstance(config.get("vision"), dict) else {}
    asr_mode = str(args.asr if args.asr is not None else asr_config.get("mode", "auto"))
    if asr_mode not in {"auto", "local", "none"}:
        raise ValueError(f"未知 ASR 模式：{asr_mode}")
    asr_model = str(
        args.asr_model if args.asr_model is not None else asr_config.get("local_model", "small")
    ).strip()
    if not asr_model:
        raise ValueError("ASR 模型名不能为空")
    language = str(args.language if args.language is not None else asr_config.get("language", "zh")).strip()
    if not language:
        raise ValueError("ASR 语言不能为空")
    storyboard_interval = float(args.storyboard_interval if args.storyboard_interval is not None else 30.0)
    max_frames = int(args.max_frames if args.max_frames is not None else vision_config.get("max_frames", 20))
    if storyboard_interval <= 0:
        raise ValueError("--storyboard-interval 必须大于 0")
    if max_frames <= 0:
        raise ValueError("--max-frames 必须大于 0")
    return AnalyzeOptions(
        asr_mode=asr_mode,
        asr_model=asr_model,
        language=language,
        storyboard_interval=storyboard_interval,
        max_frames=max_frames,
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug(value: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned or "未命名视频")[:limit]


def normalize_source_entry_url(value: str) -> str:
    """Map the public IES share-note page to the canonical public note entry.

    The content adapter intentionally accepts only Douyin note hosts.  The IES
    share page is already in the project's public domain allowlist, so this
    narrow mapping avoids treating it as an unsupported content type without
    following a private API or weakening URL validation.
    """
    parsed = urllib.parse.urlparse(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or host not in {"iesdouyin.com", "www.iesdouyin.com"}
        or parsed.username
        or parsed.password
    ):
        return value
    try:
        port = parsed.port
    except ValueError:
        return value
    expected_port = 443 if parsed.scheme == "https" else 80
    if port not in {None, expected_port}:
        return value
    match = re.fullmatch(r"/share/note/(\d+)/?", parsed.path)
    if not match:
        return value
    return f"https://www.douyin.com/note/{match.group(1)}"


def prepare_source_entry_url(value: str) -> str:
    """Resolve public short links only when they identify one concrete work."""
    normalized = normalize_source_entry_url(value)
    parsed = urllib.parse.urlparse(normalized)
    if (parsed.hostname or "").lower().rstrip(".") != "v.douyin.com":
        return normalized
    resolved = normalize_source_entry_url(resolve_share_url(normalized))
    resolved_path = urllib.parse.urlparse(resolved).path.rstrip("/")
    if re.fullmatch(r"/(?:note|video)/\d+", resolved_path):
        return resolved
    raise AcquisitionError(
        "UNSUPPORTED_SOURCE",
        "抖音短链接未解析到具体的 /note/ 或 /video/ 作品页",
        adapter="source-entry",
    )


def run_helper(
    name: str, arguments: Sequence[str], *, timeout: int = 1800
) -> subprocess.CompletedProcess[str]:
    path = HERE / name
    return subprocess.run(
        [sys.executable, str(path), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def ensure_cache_root(config: Dict[str, Any]) -> Path:
    return ensure_managed_root(config)


def create_job(config: Dict[str, Any], platform_hint: str = "media") -> Path:
    root = ensure_cache_root(config)
    clean_cache(config, apply=True)
    job = Path(tempfile.mkdtemp(prefix=f"job-{platform_hint}-", dir=str(root))).resolve()
    return register_job(config, job)


def finalize_job(
    config: Dict[str, Any], job: Path, *, success: bool, retain_success: bool = False
) -> Dict[str, Any]:
    """Finish retention without allowing cleanup failures to hide the analysis result."""
    try:
        return finish_job(config, job, success=success, retain_success=retain_success)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "retained": job.exists(),
            "reason": "retention_error",
            "path": str(job),
            "error": str(exc),
        }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def acquisition_errors(source: AcquiredSource) -> List[Dict[str, Any]]:
    rows = []
    for attempt in source.attempts:
        if attempt.ok:
            continue
        rows.append(
            {
                "stage": "acquisition",
                "provider": attempt.adapter,
                "type": attempt.error_type,
                "fatal": False,
                "message": attempt.message,
                "suggestion": "检查网络、登录态和平台风控；必要时启用浏览器回退或提供本地媒体。",
                "retryable": attempt.error_type in {"NETWORK_ERROR", "TIMEOUT", "ACCESS_RESTRICTED"},
                "occurred_at": now_iso(),
            }
        )
    return rows


NATIVE_VIDEO_ERROR_SUGGESTIONS = {
    "PROVIDER_RETRY": "检查重试后的结果；若同一 provider 持续失败，请降低输入大小或调整超时。",
    "PROVIDER_SWITCHED": "检查备用 provider 的最终结果，并保留主 provider 的失败记录用于排障。",
    "VISUAL_SEGMENT_BUDGET_EXHAUSTED": "提高 vision.max_visual_calls，或减少分段、重试和复核次数。",
    "VISUAL_BUDGET_INSUFFICIENT": "提高 vision.max_visual_calls，或缩短视频后重新运行。",
    "VISION_REPORT_MISSING": "检查视觉路由进程是否异常退出；修复后从原生视频转写阶段重试。",
    "VISION_REPORT_INVALID": "检查视觉路由版本和磁盘写入；删除损坏报告后重新运行。",
    "VISION_OUTPUT_MISSING": "检查视觉路由输出路径和磁盘权限，然后重新处理该片段。",
    "VISION_SEGMENT_FAILED": "根据视觉路由状态检查 provider 配置、额度和媒体兼容性。",
    "VIDEO_SEGMENT_ENCODING_FAILED": "检查 FFmpeg、输入媒体完整性和可用磁盘空间。",
    "VISION_ROUTER_FAILED": "检查视觉路由运行环境；该调用的用量未知时应先核对额度。",
    "UNKNOWN_ERROR": "查看脱敏后的失败详情，再决定重试或切换 provider。",
}


def native_video_error(
    error_type: str,
    message: str,
    *,
    segment_index: int,
    start: float,
    end: float,
    provider: str = "vision-router",
    suggestion: Optional[str] = None,
    retryable: bool = False,
    fatal: bool = False,
    occurred_at: Optional[str] = None,
    attempt: Optional[int] = None,
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "stage": "native-video-transcription",
        "provider": sanitize_error_text(provider) or "unknown-provider",
        "type": sanitize_error_text(error_type) or "UNKNOWN_ERROR",
        "fatal": bool(fatal),
        "message": sanitize_error_text(message)[:1000] or "未提供错误详情",
        "suggestion": sanitize_error_text(
            suggestion
            or NATIVE_VIDEO_ERROR_SUGGESTIONS.get(
                error_type, NATIVE_VIDEO_ERROR_SUGGESTIONS["UNKNOWN_ERROR"]
            )
        )[:1000],
        "retryable": bool(retryable),
        "occurred_at": sanitize_error_text(occurred_at) or now_iso(),
        "segment_index": segment_index,
        "time_range": {
            "start_seconds": round(float(start), 3),
            "end_seconds": round(float(end), 3),
        },
    }
    if attempt is not None:
        record["attempt"] = attempt
    return record


def append_segment_report_errors(
    report: Dict[str, Any],
    target: List[Dict[str, Any]],
    *,
    segment_index: int,
    start: float,
    end: float,
) -> None:
    """Rebuild the ordered provider failure chain with segment context."""
    raw_errors = report.get("errors") or []
    if not isinstance(raw_errors, list):
        target.append(
            native_video_error(
                "VISION_REPORT_INVALID",
                "视觉路由报告的 errors 字段不是数组",
                segment_index=segment_index,
                start=start,
                end=end,
            )
        )
        raw_errors = []
    attempted = report.get("attempted_providers") or []
    if not isinstance(attempted, list):
        attempted = []
    attempted = [str(value) for value in attempted if value]
    selected = str(report.get("selected_provider") or "")
    status = str(report.get("status") or "")

    valid_errors = [item for item in raw_errors if isinstance(item, dict)]
    provider_order = attempted or list(
        dict.fromkeys(str(item.get("provider") or "unknown-provider") for item in valid_errors)
    )
    consumed: set[int] = set()
    for provider_position, provider in enumerate(provider_order):
        block = [
            (index, item)
            for index, item in enumerate(valid_errors)
            if index not in consumed and str(item.get("provider") or "unknown-provider") == provider
        ]
        for block_position, (raw_index, item) in enumerate(block):
            consumed.add(raw_index)
            raw_attempt = item.get("attempt")
            try:
                attempt = int(raw_attempt) if raw_attempt is not None else None
            except (TypeError, ValueError):
                attempt = None
            normalized = native_video_error(
                str(item.get("type") or "UNKNOWN_ERROR"),
                str(item.get("message") or "视觉 provider 返回未说明的错误"),
                segment_index=segment_index,
                start=start,
                end=end,
                provider=provider,
                suggestion=str(item.get("suggestion") or "") or None,
                retryable=bool(item.get("retryable", False)),
                fatal=bool(item.get("fatal", False)),
                occurred_at=str(item.get("occurred_at") or "") or None,
                attempt=attempt,
            )
            target.append(normalized)
            another_failed_attempt = block_position + 1 < len(block)
            successful_retry = (
                block_position + 1 == len(block)
                and selected == provider
                and status == "external_success"
            )
            if another_failed_attempt or successful_retry:
                target.append(
                    native_video_error(
                        "PROVIDER_RETRY",
                        f"provider {provider} 在失败后发起下一次尝试",
                        segment_index=segment_index,
                        start=start,
                        end=end,
                        provider=provider,
                        retryable=True,
                        occurred_at=normalized["occurred_at"],
                        attempt=(attempt + 1) if attempt is not None else None,
                    )
                )
        if block and provider_position + 1 < len(provider_order):
            next_provider = provider_order[provider_position + 1]
            target.append(
                native_video_error(
                    "PROVIDER_SWITCHED",
                    f"provider {provider} 失败后切换至 {next_provider}",
                    segment_index=segment_index,
                    start=start,
                    end=end,
                    provider=next_provider,
                    retryable=True,
                    occurred_at=target[-1]["occurred_at"],
                )
            )

    for raw_index, item in enumerate(valid_errors):
        if raw_index in consumed:
            continue
        target.append(
            native_video_error(
                str(item.get("type") or "UNKNOWN_ERROR"),
                str(item.get("message") or "视觉 provider 返回未说明的错误"),
                segment_index=segment_index,
                start=start,
                end=end,
                provider=str(item.get("provider") or "unknown-provider"),
                suggestion=str(item.get("suggestion") or "") or None,
                retryable=bool(item.get("retryable", False)),
                fatal=bool(item.get("fatal", False)),
                occurred_at=str(item.get("occurred_at") or "") or None,
            )
        )

    if report.get("budget_exhausted") or status == "external_budget_exhausted":
        target.append(
            native_video_error(
                "VISUAL_SEGMENT_BUDGET_EXHAUSTED",
                "该片段获分配的视觉 provider 调用预算已经耗尽",
                segment_index=segment_index,
                start=start,
                end=end,
                provider=attempted[-1] if attempted else "vision-router",
                retryable=True,
            )
        )
    elif status != "external_success" and not valid_errors:
        target.append(
            native_video_error(
                "VISION_SEGMENT_FAILED",
                f"视觉路由未完成该片段，状态为 {status or 'unknown'}",
                segment_index=segment_index,
                start=start,
                end=end,
                provider=attempted[-1] if attempted else "vision-router",
                retryable=True,
            )
        )


NONVIDEO_KINDS = {"long_text", "gallery", "mixed"}


def initialize_package(package_dir: Path, source: AcquiredSource, focus: str) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    content_kind = source.content_kind if source.content_kind in NONVIDEO_KINDS else "video"
    result = run_helper(
        "package_tool.py",
        [
            "init",
            str(package_dir),
            "--source-url",
            source.input_url,
            "--platform",
            source.platform,
            "--source-id",
            source.source_id,
            "--content-type",
            content_kind,
            "--focus",
            focus,
        ],
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def write_source_content(package_dir: Path, source: AcquiredSource) -> None:
    duration = "未知" if source.duration is None else f"{source.duration:.1f} 秒"
    text = (
        "# 来源内容\n\n"
        f"- 标题：{source.title}\n"
        f"- 作者：{source.author or '未获取'}\n"
        f"- 平台：{source.platform}\n"
        f"- 来源 ID：`{source.source_id}`\n"
        f"- 原始链接：{source.input_url}\n"
        f"- 规范链接：{source.canonical_url}\n"
        f"- 发布时间：{source.published_at or '未获取'}\n"
        f"- 时长：{duration}\n"
        f"- 获取方式：{source.acquisition_method}\n\n"
        "## 处理边界\n\n"
        "仅处理用户提供且可公开访问的内容；评论区、相关推荐和平台导航不视为视频正文。\n"
    )
    (package_dir / "source-content.md").write_text(text, encoding="utf-8")


def write_nonvideo_source_content(package_dir: Path, source: AcquiredSource) -> None:
    """Persist only direct source metadata and author text in the author layer."""
    body = source.body_text.strip() or "作者未提供独立正文。"
    text = (
        "# 来源内容\n\n"
        "## 来源元数据\n\n"
        f"- 标题：{source.title}\n"
        f"- 作者：{source.author or '未获取'}\n"
        f"- 平台：{source.platform}\n"
        f"- 来源 ID：`{source.source_id}`\n"
        f"- 原始链接：{source.input_url}\n"
        f"- 规范链接：{source.canonical_url}\n"
        f"- 发布时间：{source.published_at or '未获取'}\n"
        f"- 获取方式：{source.acquisition_method}\n\n"
        "## 作者正文（直接来源）\n\n"
        f"{body}\n\n"
        "## 分层边界\n\n"
        "本文件只保留作者直接提供的标题、元数据和正文。"
        "图片 OCR、画面事实、视觉推断和 Agent/自动摘要属于派生层，"
        "不得追加到作者正文。\n"
    )
    (package_dir / "source-content.md").write_text(text, encoding="utf-8")


def image_analysis_error(
    error_type: str,
    message: str,
    *,
    provider: str = "vision-router",
    suggestion: str = "检查视觉 provider 配置、输入大小和脱敏报告后重试；或由宿主 Agent 校订图片层。",
    retryable: bool = False,
    occurred_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "stage": "image-analysis",
        "provider": sanitize_error_text(provider)[:200] or "vision-router",
        "type": sanitize_error_text(error_type)[:200] or "UNKNOWN_ERROR",
        "fatal": False,
        "message": sanitize_error_text(message)[:1000] or "未提供错误详情",
        "suggestion": sanitize_error_text(suggestion)[:1000],
        "retryable": bool(retryable),
        "occurred_at": sanitize_error_text(occurred_at) or now_iso(),
    }


def append_image_report_errors(report: Dict[str, Any], target: List[Dict[str, Any]]) -> None:
    raw_errors = report.get("errors") or []
    if not isinstance(raw_errors, list):
        target.append(
            image_analysis_error(
                "VISION_REPORT_INVALID", "视觉路由报告的 errors 字段不是数组"
            )
        )
        return
    for item in raw_errors:
        if not isinstance(item, dict):
            target.append(
                image_analysis_error("VISION_REPORT_INVALID", "视觉路由报告包含非对象错误项")
            )
            continue
        target.append(
            image_analysis_error(
                str(item.get("type") or "UNKNOWN_ERROR"),
                str(item.get("message") or "视觉 provider 返回未说明的错误"),
                provider=str(item.get("provider") or "vision-router"),
                suggestion=str(item.get("suggestion") or "")
                or "按错误类型检查 provider 配置、额度和媒体限制。",
                retryable=bool(item.get("retryable", False)),
                occurred_at=str(item.get("occurred_at") or "") or None,
            )
        )


def materialize_nonvideo_images(
    source: AcquiredSource,
    package_dir: Path,
    limitations: List[str],
    errors: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Path]]:
    rows: List[Dict[str, Any]] = []
    copied: List[Path] = []
    raw_paths = [Path(value) for value in source.image_paths]
    if not raw_paths:
        return rows, copied
    image_dir = package_dir / "media" / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for index, source_path in enumerate(raw_paths, start=1):
        suffix = source_path.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            suffix = ".img"
        destination = image_dir / f"{index:03d}{suffix}"
        try:
            if not source_path.is_file() or source_path.stat().st_size <= 0:
                raise OSError("来源图片不存在或为空")
            shutil.copy2(source_path, destination)
        except OSError as exc:
            limitations.append(f"图片 {index} 未能保留：本地文件复制失败。")
            errors.append(
                image_analysis_error(
                    "IMAGE_COPY_FAILED",
                    f"图片 {index} 本地复制失败（{type(exc).__name__}）",
                    provider="local-filesystem",
                    suggestion="检查来源图片是否完整、可读，并检查输出目录空间。",
                )
            )
            continue
        copied.append(destination)
        retained_index = len(copied)
        rows.append(
            {
                "type": "image",
                "path": f"media/images/{destination.name}",
                "image_index": retained_index,
                "reason": f"保留作者图片 {index} 的原始顺序",
                "description": "来源图片证据；OCR 和视觉推断在 image-analysis.md 中分层记录",
            }
        )
    return rows, copied


def _json_object_from_text(text: str) -> Dict[str, Any]:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("图片分析输出不是 JSON 对象")
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("图片分析输出不是有效 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("images"), list):
        raise ValueError("图片分析输出缺少 images 数组")
    return payload


def _list_of_text(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def render_image_analysis(image_count: int, payload: Optional[Dict[str, Any]] = None) -> str:
    by_index: Dict[int, Dict[str, Any]] = {}
    if payload is not None:
        for item in payload.get("images") or []:
            if not isinstance(item, dict):
                continue
            raw_index = item.get("image_index")
            if isinstance(raw_index, int) and not isinstance(raw_index, bool):
                by_index[raw_index] = item
    lines = [
        "# 图片 OCR 与视觉推断",
        "",
        "> 本文件是派生分析层。作者正文仅在 `source-content.md`；"
        "OCR、画面事实和视觉推断均不得冒充作者表述。",
        "",
    ]
    for index in range(1, image_count + 1):
        item = by_index.get(index)
        item_was_analyzed = item is not None
        item = item or {}
        ocr = str(item.get("ocr_text") or "").strip()
        visible = _list_of_text(item.get("visible_facts"))
        inference_rows = item.get("visual_inferences")
        inferences: List[str] = []
        if isinstance(inference_rows, list):
            for inference in inference_rows:
                if isinstance(inference, dict):
                    text = str(inference.get("text") or "").strip()
                    confidence = str(inference.get("confidence") or "unknown").strip()
                    if text:
                        inferences.append(f"{text}（置信度：{confidence}）")
                elif str(inference).strip():
                    inferences.append(str(inference).strip())
        lines.extend(
            [
                f"## 图片 {index}",
                "",
                "### 图片 OCR（自动提取，非作者正文）",
                "",
                ocr or ("未识别到可读文字。" if item_was_analyzed else "尚未校订。"),
                "",
                "### 画面直接可见事实（自动识别）",
                "",
                *(f"- {value}" for value in visible),
                *(
                    []
                    if visible
                    else ["- 未识别到可确认的画面事实。" if item_was_analyzed else "- 尚未校订。"]
                ),
                "",
                "### 视觉推断（派生内容，非作者正文）",
                "",
                *(f"- {value}" for value in inferences),
                *(
                    []
                    if inferences
                    else ["- 无必要推断。" if item_was_analyzed else "- 尚未校订。"]
                ),
                "",
            ]
        )
    limitations = _list_of_text(payload.get("overall_limitations")) if payload else []
    lines.extend(
        [
            "## 自动分析限制",
            "",
            *(f"- {value}" for value in limitations),
            *(
                []
                if limitations
                else ["- 无额外限制。" if payload is not None else "- 当前结果需由宿主 Agent 或人工对照原图校订。"]
            ),
            "",
        ]
    )
    return "\n".join(lines)


def analyze_nonvideo_images(
    package_dir: Path,
    job: Path,
    images: Sequence[Path],
    focus: str,
    *,
    enabled: bool,
    budget: VisualCallBudget,
    config_path: Optional[Path],
    limitations: List[str],
    errors: List[Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], str]:
    analysis_path = package_dir / "image-analysis.md"
    analysis_path.write_text(render_image_analysis(len(images)), encoding="utf-8")
    if not enabled:
        limitations.append("未运行外部图片 OCR/视觉分析；当前为待宿主 Agent 校订的分层占位稿。")
        return None, "disabled"
    if budget.remaining <= 0:
        limitations.append("图片 OCR/视觉分析未执行：共享视觉调用预算已耗尽。")
        errors.append(
            image_analysis_error(
                "VISUAL_BUDGET_INSUFFICIENT",
                "图片分析启动前共享视觉调用预算已耗尽",
                retryable=True,
            )
        )
        return None, "host-agent-required"

    prompt = (
        "按 --image 的输入顺序分析每张图片。只返回 JSON 对象，不要 Markdown 解释。\n"
        "格式必须是：{\"images\":[{\"image_index\":1,\"ocr_text\":\"\","
        "\"visible_facts\":[\"\"],\"visual_inferences\":[{\"text\":\"\","
        "\"confidence\":\"high|medium|low\"}]}],\"overall_limitations\":[\"\"]}。\n"
        "ocr_text 只放图片中直接可见的文字；visible_facts 只放直接可见事实；"
        "visual_inferences 只放推断并给出置信度。不得将 OCR 或推断称为作者正文。\n"
        f"用户关注点：{focus or '完整理解非视频内容'}\n"
        f"必须返回 1 到 {len(images)} 的连续 image_index。"
    )
    prompt_path = job / "image-analysis-prompt.txt"
    output_path = job / "image-analysis-output.json"
    report_path = job / "image-analysis-report.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    command = vision_router_command(config_path, budget)
    command.extend(
        [
            "--prompt-file",
            str(prompt_path),
            "--output",
            str(output_path),
            "--report",
            str(report_path),
        ]
    )
    for image in images:
        command.extend(["--image", str(image)])
    try:
        subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        budget.exhaust_unknown()
        limitations.append("图片 OCR/视觉分析子进程失败，实际调用次数未知，已保守耗尽预算。")
        errors.append(
            image_analysis_error(
                "VISION_ROUTER_FAILED",
                str(exc),
                retryable=isinstance(exc, subprocess.TimeoutExpired),
            )
        )
        return None, "host-agent-required"
    if not report_path.is_file():
        budget.exhaust_unknown()
        limitations.append("图片视觉路由未生成可信报告，已保守耗尽共享预算。")
        errors.append(
            image_analysis_error(
                "VISION_REPORT_MISSING", "图片视觉路由进程结束后未生成报告", retryable=True
            )
        )
        return None, "host-agent-required"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        budget.exhaust_unknown()
        limitations.append("图片视觉路由报告无效，已保守耗尽共享预算。")
        errors.append(
            image_analysis_error("VISION_REPORT_INVALID", "图片视觉路由报告不是有效 JSON", retryable=True)
        )
        return None, "host-agent-required"
    if not isinstance(report, dict) or not isinstance(report.get("status"), str):
        budget.exhaust_unknown()
        limitations.append("图片视觉路由报告结构无效，已保守耗尽共享预算。")
        errors.append(
            image_analysis_error("VISION_REPORT_INVALID", "图片视觉路由报告缺少有效 status", retryable=True)
        )
        return None, "host-agent-required"
    api_calls_used = report.get("api_calls_used")
    valid_usage = (
        isinstance(api_calls_used, int)
        and not isinstance(api_calls_used, bool)
        and api_calls_used >= 0
    )
    budget.consume_report(report)
    append_image_report_errors(report, errors)
    if not valid_usage:
        limitations.append("图片视觉路由报告未提供可信调用计数，已保守耗尽共享预算。")
        errors.append(
            image_analysis_error(
                "VISION_REPORT_INVALID",
                "图片视觉路由报告缺少有效 api_calls_used",
                retryable=True,
            )
        )
        return report, "host-agent-required"
    if report.get("status") != "external_success":
        limitations.append("外部图片 OCR/视觉分析未成功；需要宿主 Agent 对照包内图片校订派生层。")
        return report, "host-agent-required"
    if not output_path.is_file():
        limitations.append("外部图片分析报告成功，但缺少结果文件。")
        errors.append(
            image_analysis_error(
                "VISION_OUTPUT_MISSING", "外部图片分析未生成输出文件", retryable=True
            )
        )
        return report, "host-agent-required"
    try:
        payload = _json_object_from_text(output_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        limitations.append("外部图片分析输出无法按分层结构解析，需要宿主 Agent 校订。")
        errors.append(image_analysis_error("VISION_OUTPUT_INVALID", str(exc), retryable=True))
        return report, "host-agent-required"
    analysis_path.write_text(render_image_analysis(len(images), payload), encoding="utf-8")
    for value in _list_of_text(payload.get("overall_limitations")):
        limitations.append(f"图片分析限制：{value}")
    return report, "external-vision"


def draft_nonvideo_summary(source: AcquiredSource, image_count: int, limitations: Sequence[str]) -> str:
    body = source.body_text.strip() or "未取得作者独立正文。"
    limitation_text = "\n".join(f"- {item}" for item in limitations) or "- 暂无。"
    image_text = (
        f"- 已保留 {image_count} 张来源图片；OCR 和视觉推断位于 `image-analysis.md`。"
        if image_count
        else "- 来源未包含图片，图片派生层不适用。"
    )
    return (
        f"# {source.title}\n\n"
        "> 当前为自动准备稿（Agent/自动摘要层）。作者正文、图片 OCR 与"
        "视觉推断已分层保存；完成前必须对照证据校订本摘要。\n\n"
        "## 作者正文摘录（直接来源）\n\n"
        f"{body}\n\n"
        "## 图片派生层\n\n"
        f"{image_text}\n\n"
        "## 当前限制\n\n"
        f"{limitation_text}\n"
    )


def make_storyboard(
    media_path: Path,
    job: Path,
    interval: float,
    max_frames: int,
    max_width: int = 1280,
    max_height: int = 720,
) -> List[Path]:
    output = job / "storyboard"
    result = run_helper(
        "media_tools.py",
        [
            "storyboard",
            str(media_path),
            "--interval",
            str(interval),
            "--max-frames",
            str(max_frames),
            "--max-width",
            str(max_width),
            "--max-height",
            str(max_height),
            "--output-dir",
            str(output),
        ],
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "故事板生成失败")
    try:
        return [Path(value) for value in json.loads(result.stdout)]
    except json.JSONDecodeError as exc:
        raise RuntimeError("故事板输出格式无效") from exc


def find_scene_changes(
    media_path: Path, *, threshold: float = 0.3, max_scenes: int = 120
) -> List[float]:
    result = run_helper(
        "media_tools.py",
        [
            "scenes",
            str(media_path),
            "--threshold",
            str(threshold),
            "--max-scenes",
            str(max_scenes),
        ],
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "场景变化检测失败")
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, list):
            raise ValueError
        return [float(value) for value in payload]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("场景变化检测输出格式无效") from exc


def media_duration(media_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("缺少 ffprobe，无法确定视频时长")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr[-1000:] or "无法读取视频时长")
    return float(completed.stdout.strip())


def transcribe_with_video_provider(
    media_path: Path,
    job: Path,
    *,
    duration: Optional[float],
    budget: VisualCallBudget,
    config_path: Optional[Path],
    errors: Optional[List[Dict[str, Any]]] = None,
) -> Optional[TranscriptResult]:
    """Use configured native-video providers when captions and local ASR are unavailable."""
    error_target = errors if errors is not None else []
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    total = duration or media_duration(media_path)
    segment_seconds = 180.0
    needed = max(1, int((total + segment_seconds - 1) // segment_seconds))
    if needed + 1 > budget.remaining:
        error_target.append(
            native_video_error(
                "VISUAL_BUDGET_INSUFFICIENT",
                f"剩余预算 {budget.remaining} 次无法处理 {needed} 个片段并保留最终综合调用",
                segment_index=1,
                start=0.0,
                end=total,
                retryable=True,
            )
        )
        return None
    segment_dir = job / "video-transcription"
    segment_dir.mkdir(parents=True, exist_ok=True)
    results: List[TranscriptSegment] = []
    for index in range(needed):
        calls_for_current_segment = budget.remaining - (needed - index - 1) - 1
        if calls_for_current_segment <= 0:
            error_target.append(
                native_video_error(
                    "VISUAL_BUDGET_INSUFFICIENT",
                    "剩余预算不足以处理该片段并保留最终综合调用",
                    segment_index=index + 1,
                    start=index * segment_seconds,
                    end=min(total, (index + 1) * segment_seconds),
                    retryable=True,
                )
            )
            break
        start = index * segment_seconds
        length = min(segment_seconds, max(0.0, total - start))
        if length <= 0:
            break
        segment_path = segment_dir / f"segment-{index + 1:03d}.mp4"
        try:
            encoded = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(start),
                    "-t",
                    str(length),
                    "-i",
                    str(media_path),
                    "-vf",
                    "fps=1,scale=640:-2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-b:v",
                    "140k",
                    "-maxrate",
                    "180k",
                    "-bufsize",
                    "360k",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "32k",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(segment_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            error_target.append(
                native_video_error(
                    "VIDEO_SEGMENT_ENCODING_FAILED",
                    str(exc),
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    provider="ffmpeg",
                )
            )
            continue
        if encoded.returncode:
            error_target.append(
                native_video_error(
                    "VIDEO_SEGMENT_ENCODING_FAILED",
                    encoded.stderr or "FFmpeg 未返回错误详情",
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    provider="ffmpeg",
                )
            )
            continue
        output_path = segment_dir / f"segment-{index + 1:03d}.md"
        report_path = segment_dir / f"segment-{index + 1:03d}.report.json"
        prompt = (
            "同时听取音频并查看画面。按相对当前片段的时间戳忠实转写中文讲话，听不清处标记；"
            "随后列出画面中直接可见的界面、代码、参数、图表和操作结果。"
            "不要把平台评论、导航或相关推荐当作视频正文。"
            "最后单独追加置信度标记：<!-- MCU_CONFIDENCE: high|medium|low -->，"
            "并将竖线中的值替换为唯一一个实际等级。"
        )
        command = vision_router_command(config_path, budget, max_calls=calls_for_current_segment)
        command.extend(
            [
                "--prompt",
                prompt,
                "--video",
                str(segment_path),
                "--output",
                str(output_path),
                "--report",
                str(report_path),
            ]
        )
        try:
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            budget.exhaust_unknown()
            error_target.append(
                native_video_error(
                    "VISION_ROUTER_FAILED",
                    str(exc),
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    retryable=isinstance(exc, subprocess.TimeoutExpired),
                )
            )
            break
        if not report_path.exists():
            budget.exhaust_unknown()
            error_target.append(
                native_video_error(
                    "VISION_REPORT_MISSING",
                    "视觉路由进程结束后未生成片段报告；实际调用次数无法确定",
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    retryable=True,
                )
            )
            break
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            budget.exhaust_unknown()
            error_target.append(
                native_video_error(
                    "VISION_REPORT_INVALID",
                    "片段报告不是有效 JSON；实际调用次数无法确定",
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    retryable=True,
                )
            )
            break
        if not isinstance(report, dict) or not isinstance(report.get("status"), str):
            budget.exhaust_unknown()
            error_target.append(
                native_video_error(
                    "VISION_REPORT_INVALID",
                    "片段报告缺少有效的 status 或顶层对象结构",
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    retryable=True,
                )
            )
            break
        api_calls_used = report.get("api_calls_used")
        valid_usage = (
            isinstance(api_calls_used, int)
            and not isinstance(api_calls_used, bool)
            and api_calls_used >= 0
        )
        budget.consume_report(report)
        append_segment_report_errors(
            report,
            error_target,
            segment_index=index + 1,
            start=start,
            end=start + length,
        )
        if not valid_usage:
            error_target.append(
                native_video_error(
                    "VISION_REPORT_INVALID",
                    "片段报告缺少有效的 api_calls_used，工作流已保守耗尽剩余预算",
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    retryable=True,
                )
            )
            break
        if report.get("status") != "external_success":
            continue
        if not output_path.exists():
            error_target.append(
                native_video_error(
                    "VISION_OUTPUT_MISSING",
                    "视觉路由报告成功，但未生成该片段的转写文件",
                    segment_index=index + 1,
                    start=start,
                    end=start + length,
                    provider=str(report.get("selected_provider") or "vision-router"),
                    retryable=True,
                )
            )
            continue
        results.append(
            TranscriptSegment(
                start=start,
                end=start + length,
                text=output_path.read_text(encoding="utf-8").strip(),
            )
        )
    if not results:
        return None
    return TranscriptResult(
        method="native-video-vision",
        language="zh",
        segments=results,
        source_path=str(segment_dir),
    )


def format_media_time(seconds: float) -> str:
    value = max(0.0, float(seconds))
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    remaining = value % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remaining:06.3f}"
    return f"{minutes:02d}:{remaining:06.3f}"


def evidence_error(error_type: str, message: str, suggestion: str) -> Dict[str, Any]:
    return {
        "stage": "evidence-extraction",
        "provider": "ffmpeg",
        "type": error_type,
        "fatal": False,
        "message": sanitize_error_text(message)[:1000],
        "suggestion": suggestion,
        "retryable": False,
        "occurred_at": now_iso(),
    }


def _usable_output(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def materialize_evidence(
    media_path: Path,
    package_dir: Path,
    plan: Sequence[Dict[str, Any]],
    limitations: List[str],
    errors: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extract planned artifacts and return only files that are safe to register in manifest.media."""
    image_dir = package_dir / "media" / "images"
    clip_dir = package_dir / "media" / "clips"
    image_dir.mkdir(parents=True, exist_ok=True)
    clip_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []

    def extract_frame(item: Dict[str, Any], index: int, *, fallback: bool = False) -> None:
        timestamp = float(item.get("timestamp_seconds", 0.0))
        suffix = "短片降级截图" if fallback else "关键截图"
        name = f"{index:03d}_{int(timestamp * 1000):09d}ms_{suffix}.jpg"
        destination = image_dir / name
        try:
            result = run_helper(
                "media_tools.py",
                [
                    "frame",
                    str(media_path),
                    "--at",
                    str(timestamp),
                    "--output",
                    str(destination),
                ],
            )
            failure = result.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = None
            failure = str(exc)
        if result is None or result.returncode or not _usable_output(destination):
            errors.append(
                evidence_error(
                    "EVIDENCE_FRAME_FAILED",
                    failure or f"未生成可用截图：{destination.name}",
                    "检查 FFmpeg、源媒体完整性和输出目录权限后重试。",
                )
            )
            limitations.append(f"关键截图提取失败（{format_media_time(timestamp)}）。")
            return
        reason = str(item.get("reason") or "关键视觉证据")
        if fallback:
            reason = f"{reason}；短片失败后降级保留中间状态"
        rows.append(
            {
                "type": "image",
                "path": f"media/images/{name}",
                "timestamp": format_media_time(timestamp),
                "reason": reason,
                "description": str(item.get("description") or "关键静态画面"),
                "signals": list(item.get("signals") or []),
            }
        )

    for index, item in enumerate(plan, start=1):
        if item.get("type") == "image":
            extract_frame(item, index)
            continue
        if item.get("type") != "clip":
            continue
        start = float(item.get("start_seconds", 0.0))
        end = float(item.get("end_seconds", start))
        destination = clip_dir / f"{index:03d}_{int(start * 1000):09d}-{int(end * 1000):09d}ms_关键短片.mp4"
        try:
            result = run_helper(
                "media_tools.py",
                [
                    "clip",
                    str(media_path),
                    "--start",
                    str(start),
                    "--end",
                    str(end),
                    "--output",
                    str(destination),
                ],
            )
            failure = result.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = None
            failure = str(exc)
        if result is None or result.returncode or not _usable_output(destination):
            errors.append(
                evidence_error(
                    "EVIDENCE_CLIP_FAILED",
                    failure or f"未生成可用短片：{destination.name}",
                    "已自动降级为中间时点截图；检查编码器和源媒体后可重试动态提取。",
                )
            )
            limitations.append(
                f"短片提取失败（{format_media_time(start)}–{format_media_time(end)}），"
                "已降级为中间时点截图，动态过程可能不完整。"
            )
            fallback_item = dict(item)
            fallback_item["timestamp_seconds"] = float(
                item.get("timestamp_seconds", (start + end) / 2)
            )
            extract_frame(fallback_item, index, fallback=True)
            continue
        rows.append(
            {
                "type": "clip",
                "path": f"media/clips/{destination.name}",
                "time_range": {
                    "start": format_media_time(start),
                    "end": format_media_time(end),
                    "start_seconds": round(start, 3),
                    "end_seconds": round(end, 3),
                },
                "reason": str(item.get("reason") or "保留关键动态过程"),
                "description": str(item.get("description") or "关键动态证据"),
                "signals": list(item.get("signals") or []),
            }
        )
    return rows


def draft_summary(
    transcript: Optional[TranscriptResult], source: AcquiredSource, limitations: Sequence[str]
) -> str:
    excerpt = ""
    if transcript:
        excerpt = "\n".join(
            f"- `{int(item.start // 60):02d}:{int(item.start % 60):02d}` {item.text}"
            for item in transcript.segments[:80]
        )
    if not excerpt:
        excerpt = "- 尚未取得可用字幕或 ASR 结果。"
    limitation_text = "\n".join(f"- {item}" for item in limitations) or "- 暂无。"
    return (
        f"# {source.title}\n\n"
        "> 当前为自动准备稿。来源、时间轴和有上限的候选证据已经建立；"
        "如宿主 Agent 支持视觉，应继续复核最终截图与短片并补充核心结论。\n\n"
        "## 时间轴内容\n\n"
        f"{excerpt}\n\n"
        "## 当前限制\n\n"
        f"{limitation_text}\n"
    )


def visual_summary(
    package_dir: Path,
    job: Path,
    transcript: Optional[TranscriptResult],
    frames: Sequence[Path],
    focus: str,
    *,
    budget: VisualCallBudget,
    config_path: Optional[Path],
) -> Optional[Dict[str, Any]]:
    if not frames:
        return None
    if budget.remaining <= 0:
        return {
            "status": "external_budget_exhausted",
            "api_calls_limit": 0,
            "api_calls_used": 0,
            "budget_exhausted": True,
            "workflow_budget": budget.snapshot(),
            "errors": [],
        }
    transcript_text = transcript.markdown() if transcript else "没有可用转写，请主要依赖画面。"
    prompt = (
        "请根据视频的时间戳转写和按顺序抽取的故事板，生成中文 Markdown 内容提炼。\n"
        "要求包括：一分钟核心结论、解决的问题、章节结构、可执行步骤、关键参数、"
        "画面直接可见的信息、合理推断、缺失信息、复刻前仍需验证的事项。"
        "不要把故事板中出现的平台评论、推荐或导航当作视频正文。"
        "需要引用画面时使用对应文件名，并说明画面作用。\n"
        "最后单独追加置信度标记：<!-- MCU_CONFIDENCE: high|medium|low -->，"
        "并将竖线中的值替换为唯一一个实际等级。\n"
        f"用户关注点：{focus or '完整理解视频内容'}\n\n"
        f"{transcript_text[:40000]}"
    )
    prompt_path = job / "summary-prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    report = package_dir / "vision-report.json"
    command = vision_router_command(config_path, budget)
    command.extend(
        [
        "--prompt-file",
        str(prompt_path),
        "--output",
        str(package_dir / "summary.md"),
        "--report",
        str(report),
        ]
    )
    for frame in frames[:12]:
        command.extend(["--image", str(frame)])
    try:
        subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        budget.exhaust_unknown()
        return {
            "status": "external_router_failed",
            "api_calls_limit": budget.limit,
            "api_calls_used": None,
            "budget_exhausted": True,
            "workflow_budget": budget.snapshot(),
            "errors": [
                {
                    "stage": "visual-summary",
                    "provider": "vision-router",
                    "type": "VISION_ROUTER_FAILED",
                    "fatal": False,
                    "message": sanitize_error_text(exc)[:1000],
                    "suggestion": "检查视觉模型服务、网络与超时配置；当前保留部分结果并可由宿主 Agent 复核故事板。",
                    "retryable": isinstance(exc, subprocess.TimeoutExpired),
                    "occurred_at": now_iso(),
                }
            ],
        }
    if report.exists():
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            budget.exhaust_unknown()
            return None
        budget.consume_report(payload)
        payload["workflow_budget"] = budget.snapshot()
        return payload
    budget.exhaust_unknown()
    return None


def update_manifest(
    package_dir: Path,
    source: AcquiredSource,
    transcript: Optional[TranscriptResult],
    media: Sequence[Dict[str, Any]],
    limitations: Sequence[str],
    vision_report: Optional[Dict[str, Any]],
) -> None:
    path = package_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    vision_ok = bool(vision_report and vision_report.get("status") == "external_success")
    manifest["status"] = "partial"
    manifest["source"].update(
        {
            "canonical_url": source.canonical_url,
            "title": source.title,
            "author": source.author,
            "published_at": source.published_at,
        }
    )
    manifest["content"]["language"] = transcript.language if transcript else ""
    manifest["media"] = list(media)
    manifest["limitations"] = list(limitations)
    manifest["processing"].update(
        {
            "acquisition_method": source.acquisition_method,
            "transcription_method": transcript.method if transcript else "unavailable",
            "vision_provider": (
                f"{vision_report.get('selected_provider')}/{vision_report.get('selected_model')}"
                if vision_ok
                else "host-agent-required"
            ),
            "updated_at": now_iso(),
        }
    )
    write_json(path, manifest)


def update_nonvideo_manifest(
    package_dir: Path,
    source: AcquiredSource,
    media: Sequence[Dict[str, Any]],
    limitations: Sequence[str],
    vision_report: Optional[Dict[str, Any]],
    image_analysis_method: str,
) -> None:
    path = package_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    vision_ok = bool(vision_report and vision_report.get("status") == "external_success")
    content = manifest["content"]
    content["kind"] = source.content_kind
    content.pop("transcript_file", None)
    content["provenance_layers"] = {
        "author_body": {
            "file": "source-content.md",
            "provenance": "author-direct",
            "derived": False,
        },
        "summary": {
            "file": "summary.md",
            "provenance": "agent-or-automatic-draft",
            "derived": True,
            "requires_evidence_review": True,
        },
    }
    if media:
        content["image_analysis_file"] = "image-analysis.md"
        content["provenance_layers"].update(
            {
                "image_ocr": {
                    "file": "image-analysis.md",
                    "provenance": "automated-or-host-vision",
                    "derived": True,
                    "author_text": False,
                },
                "visual_inference": {
                    "file": "image-analysis.md",
                    "provenance": "automated-or-host-vision",
                    "derived": True,
                    "author_text": False,
                },
            }
        )
    else:
        content.pop("image_analysis_file", None)
    manifest["status"] = "partial"
    manifest["source"].update(
        {
            "canonical_url": source.canonical_url,
            "title": source.title,
            "author": source.author,
            "published_at": source.published_at,
        }
    )
    manifest["media"] = list(media)
    manifest["limitations"] = list(limitations)
    processing = manifest["processing"]
    processing.pop("transcription_method", None)
    processing.update(
        {
            "acquisition_method": source.acquisition_method,
            "image_analysis_method": image_analysis_method,
            "vision_provider": (
                sanitize_error_text(
                    f"{vision_report.get('selected_provider')}/{vision_report.get('selected_model')}"
                )[:300]
                if vision_ok
                else "host-agent-required"
            ),
            "updated_at": now_iso(),
        }
    )
    write_json(path, manifest)


def analyze_nonvideo_job(
    args: argparse.Namespace,
    config: Dict[str, Any],
    source: AcquiredSource,
    job: Path,
    errors: List[Dict[str, Any]],
    visual_budget: VisualCallBudget,
    *,
    config_path: Optional[Path],
) -> int:
    limitations: List[str] = []
    if source.content_kind == "long_text" and source.image_paths:
        raise RuntimeError("long_text 来源不应同时声明图片")
    if source.content_kind == "gallery" and source.body_text.strip():
        raise RuntimeError("gallery 来源不应同时声明作者正文")
    if source.content_kind == "mixed" and (not source.body_text.strip() or not source.image_paths):
        raise RuntimeError("mixed 来源必须同时包含作者正文和图片")
    if source.media_path:
        raise RuntimeError("非视频来源不得同时声明音视频媒体路径")

    output_root = Path(config["paths"]["output_root"]).expanduser().resolve()
    package_dir = output_root / f"{source.platform}_{source.source_id}_{slug(source.title)}"
    if package_dir.exists():
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
        package_dir = package_dir.with_name(package_dir.name + "_" + suffix)
    initialize_package(package_dir, source, args.focus)
    write_nonvideo_source_content(package_dir, source)
    media_rows, copied_images = materialize_nonvideo_images(
        source, package_dir, limitations, errors
    )

    vision_report: Optional[Dict[str, Any]] = None
    image_analysis_method = "not-applicable"
    if copied_images:
        vision_report, image_analysis_method = analyze_nonvideo_images(
            package_dir,
            job,
            copied_images,
            args.focus,
            enabled=args.vision != "none",
            budget=visual_budget,
            config_path=config_path,
            limitations=limitations,
            errors=errors,
        )
    (package_dir / "summary.md").write_text(
        draft_nonvideo_summary(source, len(copied_images), limitations), encoding="utf-8"
    )
    write_json(package_dir / "errors.json", errors)
    update_nonvideo_manifest(
        package_dir,
        source,
        media_rows,
        limitations,
        vision_report,
        image_analysis_method,
    )
    write_summary_html(package_dir)
    validation = run_helper("package_tool.py", ["validate", str(package_dir)])
    try:
        validation_payload = json.loads(validation.stdout)
        valid = bool(validation_payload.get("ok"))
    except json.JSONDecodeError:
        validation_payload = validation.stderr
        valid = False
    retention = finalize_job(config, job, success=valid)
    result = {
        "ok": valid,
        "status": json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))["status"],
        "platform": source.platform,
        "content_kind": source.content_kind,
        "package_dir": str(package_dir),
        "job_dir": str(job) if retention["retained"] else None,
        "job_retained": retention["retained"],
        "job_retention_reason": retention["reason"],
        "acquisition_method": source.acquisition_method,
        "image_analysis_status": (
            vision_report.get("status") if vision_report else image_analysis_method
        ),
        "visual_call_budget": visual_budget.snapshot(),
        "validation": validation_payload,
    }
    if retention.get("error"):
        result["retention_error"] = retention["error"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if valid else 3


def analyze(args: argparse.Namespace) -> int:
    config, config_path = load_config(args.config)
    options = resolve_analyze_options(args, config)
    if args.output_root:
        config["paths"]["output_root"] = str(Path(args.output_root).expanduser().resolve())
    job = create_job(config)
    try:
        return analyze_job(args, config, options, job, config_path=config_path)
    except BaseException:
        finalize_job(config, job, success=False)
        raise


def analyze_job(
    args: argparse.Namespace,
    config: Dict[str, Any],
    options: AnalyzeOptions,
    job: Path,
    *,
    config_path: Optional[Path] = None,
) -> int:
    errors: List[Dict[str, Any]] = []
    visual_budget = VisualCallBudget(int(config.get("vision", {}).get("max_visual_calls", 20)))
    try:
        router = SourceRouter(default_adapters(config))
        source = router.acquire(prepare_source_entry_url(args.url), job)
        source.input_url = args.url
    except AcquisitionError as exc:
        safe_message = sanitize_error_text(exc)
        write_json(
            job / "errors.json",
            [{"stage": "acquisition", "type": exc.error_type, "message": safe_message}],
        )
        retention = finalize_job(config, job, success=False)
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "acquisition",
                    "error": exc.error_type,
                    "message": safe_message,
                    "job": str(job),
                    "job_retained": retention["retained"],
                    "job_retention_reason": retention["reason"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    errors.extend(acquisition_errors(source))
    if source.content_kind in NONVIDEO_KINDS:
        return analyze_nonvideo_job(
            args,
            config,
            source,
            job,
            errors,
            visual_budget,
            config_path=config_path,
        )
    media_path = Path(source.media_path or "")
    if not media_path.is_file():
        retention = finalize_job(config, job, success=False)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "MEDIA_NOT_FOUND",
                    "job": str(job),
                    "job_retained": retention["retained"],
                    "job_retention_reason": retention["reason"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    transcript: Optional[TranscriptResult] = None
    limitations: List[str] = []
    try:
        transcript = get_transcript(
            [Path(path) for path in source.subtitle_paths],
            media_path,
            job,
            mode=options.asr_mode,
            model_name=options.asr_model,
            language=options.language,
        )
    except TranscriptionError as exc:
        limitations.append(str(exc))
        errors.append(
            {
                "stage": "transcription",
                "provider": "subtitle/asr",
                "type": exc.error_type,
                "fatal": False,
                "message": str(exc),
                "suggestion": "安装本地 ASR 可选依赖、配置字幕来源，或让支持原生视频的视觉模型分段转写。",
                "retryable": exc.error_type in {"ASR_FAILED", "EMPTY_TRANSCRIPT"},
                "occurred_at": now_iso(),
            }
        )

    if transcript is None and args.vision != "none":
        try:
            transcript = transcribe_with_video_provider(
                media_path,
                job,
                duration=source.duration,
                budget=visual_budget,
                config_path=config_path,
                errors=errors,
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            transcript = None
            limitations.append(f"原生视频视觉转写失败：{exc}")
        if transcript is not None:
            limitations.append("平台字幕和本地 ASR 不可用，本次采用外部原生视频模型分段生成音画转写。")

    interval = options.storyboard_interval
    evidence_config = (
        config.get("evidence", {}) if isinstance(config.get("evidence"), dict) else {}
    )
    storyboard_width = int(evidence_config.get("storyboard_max_width", 1280))
    storyboard_height = int(evidence_config.get("storyboard_max_height", 720))
    frames: List[Path] = []
    try:
        frames = make_storyboard(
            media_path,
            job,
            interval,
            options.max_frames,
            storyboard_width,
            storyboard_height,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        limitations.append(str(exc))
        errors.append(
            {
                "stage": "visual-preparation",
                "provider": "ffmpeg",
                "type": "STORYBOARD_FAILED",
                "fatal": False,
                "message": str(exc),
                "suggestion": "安装 FFmpeg，或由宿主 Agent 使用等价视频抽帧能力。",
                "retryable": False,
                "occurred_at": now_iso(),
            }
        )

    try:
        scene_changes = find_scene_changes(
            media_path,
            threshold=float(evidence_config.get("scene_threshold", 0.3)),
            max_scenes=int(evidence_config.get("max_scene_changes", 120)),
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        scene_changes = []
        limitations.append(f"场景变化检测不可用：{exc}")
        errors.append(
            {
                "stage": "visual-preparation",
                "provider": "ffmpeg",
                "type": "SCENE_DETECTION_FAILED",
                "fatal": False,
                "message": sanitize_error_text(str(exc))[:1000],
                "suggestion": "检查 FFmpeg 和源媒体完整性；当前仍会根据字幕触发词选择证据。",
                "retryable": False,
                "occurred_at": now_iso(),
            }
        )

    try:
        evidence_duration = float(source.duration or 0.0)
    except (TypeError, ValueError):
        evidence_duration = 0.0
    if evidence_duration <= 0:
        try:
            evidence_duration = media_duration(media_path)
        except (RuntimeError, ValueError):
            evidence_duration = max(scene_changes, default=0.0)
    evidence_plan = build_evidence_plan(
        transcript.segments if transcript else [],
        scene_changes,
        duration=evidence_duration,
        max_images=int(evidence_config.get("max_images", 6)),
        max_clips=int(evidence_config.get("max_clips", 3)),
        dedupe_seconds=float(evidence_config.get("dedupe_seconds", 4)),
        clip_seconds=float(evidence_config.get("clip_seconds", 12)),
    )

    output_root = Path(config["paths"]["output_root"]).expanduser().resolve()
    package_dir = output_root / f"{source.platform}_{source.source_id}_{slug(source.title)}"
    if package_dir.exists():
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
        package_dir = package_dir.with_name(package_dir.name + "_" + suffix)
    initialize_package(package_dir, source, args.focus)
    write_source_content(package_dir, source)
    if transcript:
        (package_dir / "transcript.md").write_text(transcript.markdown(), encoding="utf-8")
    else:
        (package_dir / "transcript.md").write_text("# 时间戳转写\n\n未取得可用转写。\n", encoding="utf-8")
    media_rows = materialize_evidence(media_path, package_dir, evidence_plan, limitations, errors)
    (package_dir / "summary.md").write_text(draft_summary(transcript, source, limitations), encoding="utf-8")
    vision_report = (
        visual_summary(
            package_dir,
            job,
            transcript,
            frames,
            args.focus,
            budget=visual_budget,
            config_path=config_path,
        )
        if args.vision != "none"
        else None
    )
    if vision_report:
        for item in vision_report.get("errors") or []:
            if isinstance(item, dict):
                errors.append(item)
    if not vision_report or vision_report.get("status") != "external_success":
        limitations.append("外部视觉模型未完成最终综合；需要由支持视觉的宿主 Agent 检查故事板并完善摘要。")
        if not (package_dir / "summary.md").read_text(encoding="utf-8").strip():
            (package_dir / "summary.md").write_text(
                draft_summary(transcript, source, limitations), encoding="utf-8"
            )
    write_json(package_dir / "errors.json", errors)
    update_manifest(package_dir, source, transcript, media_rows, limitations, vision_report)
    write_summary_html(package_dir)
    validation = run_helper("package_tool.py", ["validate", str(package_dir)])
    valid = False
    try:
        valid = bool(json.loads(validation.stdout).get("ok"))
    except json.JSONDecodeError:
        valid = False
    retention = finalize_job(config, job, success=valid)
    result = {
        "ok": valid,
        "status": json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))["status"],
        "platform": source.platform,
        "package_dir": str(package_dir),
        "job_dir": str(job) if retention["retained"] else None,
        "job_retained": retention["retained"],
        "job_retention_reason": retention["reason"],
        "acquisition_method": source.acquisition_method,
        "transcription_method": transcript.method if transcript else "unavailable",
        "vision_status": vision_report.get("status") if vision_report else "host-agent-required",
        "visual_call_budget": visual_budget.snapshot(),
        "validation": json.loads(validation.stdout)
        if validation.stdout.strip().startswith("{")
        else validation.stderr,
    }
    if retention.get("error"):
        result["retention_error"] = retention["error"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if valid else 3


def doctor(args: argparse.Namespace) -> int:
    command = ["--content-type", "video", "--json"]
    if args.config:
        command = ["--config", args.config, *command]
    completed = run_helper("preflight.py", command)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "errors": [completed.stderr or completed.stdout]}
    try:
        import yt_dlp  # noqa: F401

        yt_dlp_available = True
    except ImportError:
        yt_dlp_available = shutil.which("yt-dlp") is not None
    try:
        import playwright  # noqa: F401

        playwright_available = True
    except ImportError:
        playwright_available = False
    try:
        import faster_whisper  # noqa: F401

        asr_available = True
    except ImportError:
        asr_available = False
    payload["public_cli"] = {
        "yt_dlp": yt_dlp_available,
        "playwright_browser_fallback": playwright_available,
        "faster_whisper_asr": asr_available,
        "supported_platforms": ["douyin", "bilibili"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 2


def _configured_browser_profile(config: Dict[str, Any]) -> Optional[Path]:
    raw = str(config.get("acquisition", {}).get("browser_profile_dir") or "").strip()
    if not raw:
        return None
    profile = Path(raw).expanduser().resolve()

    def contains(parent: Path, child: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    if profile in {Path(profile.anchor), Path.home().resolve()}:
        raise ValueError("专用浏览器档案不能是文件系统根目录或用户主目录")
    for path in (
        Path(config["paths"]["temp_root"]).resolve(),
        Path(config["paths"]["output_root"]).resolve(),
    ):
        if contains(profile, path) or contains(path, profile):
            raise ValueError(f"专用浏览器档案必须与受保护目录完全分离：{path}")
    return profile


def browser_profile(args: argparse.Namespace) -> int:
    config, _ = load_config(args.config)
    profile = _configured_browser_profile(config)
    if profile is None:
        print(
            json.dumps(
                {
                    "ok": args.profile_action == "status",
                    "configured": False,
                    "message": "尚未配置 acquisition.browser_profile_dir",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if args.profile_action == "status" else 2
    if profile.is_symlink():
        raise ValueError("专用浏览器档案目录不能是符号链接")
    if profile.exists() and not profile.is_dir():
        raise ValueError("专用浏览器档案路径不是目录")
    if args.profile_action == "status":
        print(
            json.dumps(
                {
                    "ok": True,
                    "configured": True,
                    "path": str(profile),
                    "exists": profile.is_dir(),
                    "managed": profile.is_dir() and is_managed_browser_profile(profile),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not profile.exists():
        print(
            json.dumps(
                {"ok": True, "configured": True, "path": str(profile), "deleted": False},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if browser_profile_contains_project(profile) or not is_managed_browser_profile(profile):
        print(
            json.dumps(
                {
                    "ok": False,
                    "configured": True,
                    "path": str(profile),
                    "deleted": False,
                    "error": "UNMANAGED_BROWSER_PROFILE",
                    "message": "拒绝删除：目录缺少本 Skill 的有效管理标记，或看起来是项目目录",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if not args.yes:
        print(
            json.dumps(
                {
                    "ok": True,
                    "configured": True,
                    "path": str(profile),
                    "deleted": False,
                    "confirmation_required": True,
                    "message": "重新运行并添加 --yes 才会清除该专用登录档案",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    shutil.rmtree(profile)
    print(
        json.dumps(
            {"ok": True, "configured": True, "path": str(profile), "deleted": True},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def acquire_only(args: argparse.Namespace) -> int:
    config, _ = load_config(args.config)
    managed_job = not bool(args.work_dir)
    job = Path(args.work_dir).expanduser().resolve() if args.work_dir else create_job(config)
    try:
        source = SourceRouter(default_adapters(config)).acquire(
            prepare_source_entry_url(args.url), job
        )
        source.input_url = args.url
    except AcquisitionError as exc:
        safe_message = sanitize_error_text(exc)
        retention = finalize_job(config, job, success=False) if managed_job else None
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": exc.error_type,
                    "message": safe_message,
                    "job": str(job),
                    "job_managed": managed_job,
                    "job_retention_reason": retention["reason"] if retention else "user_work_dir",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    retention = finalize_job(config, job, success=True, retain_success=True) if managed_job else None
    print(
        json.dumps(
            {
                "ok": True,
                "job": str(job),
                "job_managed": managed_job,
                "job_retention_reason": retention["reason"] if retention else "user_work_dir",
                "source": source.to_json(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def finalize_package_command(args: argparse.Namespace) -> int:
    result = finalize_package(
        Path(args.package_dir).expanduser().resolve(), visual_mode=args.visual_evidence
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcu", description="抖音与哔哩哔哩公开内容理解 Skill CLI")
    parser.add_argument("--config", help="配置文件路径；也可使用 MEDIA_CONTENT_CONFIG")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="检查公共版运行环境")
    doctor_parser.set_defaults(func=doctor)

    profile_parser = sub.add_parser("browser-profile", help="查看或清除专用浏览器登录档案")
    profile_sub = profile_parser.add_subparsers(dest="profile_action", required=True)
    profile_status = profile_sub.add_parser("status", help="查看专用浏览器档案状态")
    profile_status.set_defaults(func=browser_profile, yes=False)
    profile_reset = profile_sub.add_parser("reset", help="清除专用浏览器登录状态")
    profile_reset.add_argument("--yes", action="store_true", help="确认删除配置的专用档案目录")
    profile_reset.set_defaults(func=browser_profile)

    acquire_parser = sub.add_parser("acquire", help="只获取并规范化来源")
    acquire_parser.add_argument("url")
    acquire_parser.add_argument("--work-dir")
    acquire_parser.set_defaults(func=acquire_only)

    finalize_parser = sub.add_parser("finalize", help="审计理解包并在所有门禁通过后标记完成")
    finalize_parser.add_argument("package_dir")
    finalize_parser.add_argument(
        "--visual-evidence",
        choices=("auto", "required", "not-required"),
        default="auto",
        help="必要视觉证据的判定方式",
    )
    finalize_parser.add_argument(
        "--require-visual-evidence",
        action="store_const",
        const="required",
        dest="visual_evidence",
        help="强制要求至少一项视觉证据",
    )
    finalize_parser.add_argument(
        "--no-require-visual-evidence",
        action="store_const",
        const="not-required",
        dest="visual_evidence",
        help="明确声明当前内容不需要视觉证据",
    )
    finalize_parser.set_defaults(func=finalize_package_command)

    analyze_parser = sub.add_parser("analyze", help="按内容类型获取、分层分析并生成理解包")
    analyze_parser.add_argument("url")
    analyze_parser.add_argument("--focus", default="")
    analyze_parser.add_argument("--output-root")
    analyze_parser.add_argument("--asr", choices=["auto", "local", "none"])
    analyze_parser.add_argument("--asr-model")
    analyze_parser.add_argument("--language")
    analyze_parser.add_argument("--vision", choices=["auto", "none"], default="auto")
    analyze_parser.add_argument("--storyboard-interval", type=float)
    analyze_parser.add_argument("--max-frames", type=int)
    analyze_parser.set_defaults(func=analyze)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": sanitize_error_text(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
