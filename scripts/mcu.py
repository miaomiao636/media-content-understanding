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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from .asr_router import TranscriptionError, TranscriptResult, TranscriptSegment, get_transcript
    from .cleanup import clean_cache, ensure_managed_root, finish_job, register_job
    from .config_loader import load_config
    from .console import configure_utf8_stdio
    from .source_adapter import (
        AcquiredSource,
        AcquisitionError,
        SourceRouter,
        browser_profile_contains_project,
        default_adapters,
        is_managed_browser_profile,
    )
except ImportError:
    from asr_router import TranscriptionError, TranscriptResult, TranscriptSegment, get_transcript
    from cleanup import clean_cache, ensure_managed_root, finish_job, register_job
    from config_loader import load_config
    from console import configure_utf8_stdio
    from source_adapter import (
        AcquiredSource,
        AcquisitionError,
        SourceRouter,
        browser_profile_contains_project,
        default_adapters,
        is_managed_browser_profile,
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
        if "api_calls_used" not in report:
            self.used = self.limit
            return
        try:
            calls = int(report["api_calls_used"])
        except (TypeError, ValueError):
            self.used = self.limit
            return
        if calls < 0:
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


def initialize_package(package_dir: Path, source: AcquiredSource, focus: str) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
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
            "video",
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


def make_storyboard(media_path: Path, job: Path, interval: float, max_frames: int) -> List[Path]:
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
) -> Optional[TranscriptResult]:
    """Use configured native-video providers when captions and local ASR are unavailable."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    total = duration or media_duration(media_path)
    segment_seconds = 180.0
    needed = max(1, int((total + segment_seconds - 1) // segment_seconds))
    if needed + 1 > budget.remaining:
        return None
    segment_dir = job / "video-transcription"
    segment_dir.mkdir(parents=True, exist_ok=True)
    results: List[TranscriptSegment] = []
    for index in range(needed):
        calls_for_current_segment = budget.remaining - (needed - index - 1) - 1
        if calls_for_current_segment <= 0:
            return None
        start = index * segment_seconds
        length = min(segment_seconds, max(0.0, total - start))
        if length <= 0:
            break
        segment_path = segment_dir / f"segment-{index + 1:03d}.mp4"
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
        if encoded.returncode:
            return None
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
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
        if not report_path.exists():
            budget.exhaust_unknown()
            return None
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            budget.exhaust_unknown()
            return None
        budget.consume_report(report)
        if not output_path.exists():
            return None
        if report.get("status") != "external_success":
            return None
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


def copy_storyboard(package_dir: Path, frames: Sequence[Path], interval: float) -> List[Dict[str, Any]]:
    target = package_dir / "media" / "images"
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, frame in enumerate(frames, start=1):
        timestamp = (index - 1) * interval
        name = f"{index:03d}_{int(timestamp):06d}s_故事板.jpg"
        destination = target / name
        shutil.copy2(frame, destination)
        rows.append(
            {
                "type": "image",
                "path": f"media/images/{name}",
                "image_index": index,
                "timestamp": f"{int(timestamp // 60):02d}:{int(timestamp % 60):02d}",
                "reason": "用于建立全片视觉时间轴，后续应仅保留真正承载关键信息的画面",
                "description": f"约 {int(timestamp // 60):02d}:{int(timestamp % 60):02d} 的故事板帧",
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
        "> 当前为自动准备稿。来源、时间轴和故事板已经建立；如宿主 Agent 支持视觉，"
        "应继续检查关键画面、补充核心结论，并删除没有证据价值的故事板帧。\n\n"
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
        source = router.acquire(args.url, job)
    except AcquisitionError as exc:
        write_json(
            job / "errors.json", [{"stage": "acquisition", "type": exc.error_type, "message": str(exc)}]
        )
        retention = finalize_job(config, job, success=False)
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "acquisition",
                    "error": exc.error_type,
                    "message": str(exc),
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
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            transcript = None
            limitations.append(f"原生视频视觉转写失败：{exc}")
        if transcript is not None:
            limitations.append("平台字幕和本地 ASR 不可用，本次采用外部原生视频模型分段生成音画转写。")

    interval = options.storyboard_interval
    frames: List[Path] = []
    try:
        frames = make_storyboard(media_path, job, interval, options.max_frames)
    except RuntimeError as exc:
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
    media_rows = copy_storyboard(package_dir, frames, interval)
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
        source = SourceRouter(default_adapters(config)).acquire(args.url, job)
    except AcquisitionError as exc:
        retention = finalize_job(config, job, success=False) if managed_job else None
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": exc.error_type,
                    "message": str(exc),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcu", description="抖音与哔哩哔哩视频内容理解 Skill CLI")
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

    analyze_parser = sub.add_parser("analyze", help="获取、转写、抽帧、视觉综合并生成理解包")
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
                {"ok": False, "error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
