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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

try:
    from .asr_router import TranscriptionError, TranscriptResult, TranscriptSegment, get_transcript
    from .config_loader import load_config
    from .console import configure_utf8_stdio
    from .source_adapter import AcquiredSource, AcquisitionError, SourceRouter, default_adapters
except ImportError:
    from asr_router import TranscriptionError, TranscriptResult, TranscriptSegment, get_transcript
    from config_loader import load_config
    from console import configure_utf8_stdio
    from source_adapter import AcquiredSource, AcquisitionError, SourceRouter, default_adapters


configure_utf8_stdio()

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
ROOT_MARKER = ".media-content-understanding-managed"
JOB_MARKER = ".job-managed"


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
    root = Path(config["paths"]["temp_root"]).expanduser().resolve()
    output_root = Path(config["paths"]["output_root"]).expanduser().resolve()
    if root in {Path(root.anchor), Path.home().resolve(), output_root}:
        raise ValueError(f"不安全的缓存目录：{root}")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ROOT_MARKER
    if not marker.exists():
        marker.write_text("managed cache root\n", encoding="utf-8")
    return root


def create_job(config: Dict[str, Any], platform_hint: str = "media") -> Path:
    root = ensure_cache_root(config)
    job = Path(tempfile.mkdtemp(prefix=f"job-{platform_hint}-", dir=str(root))).resolve()
    (job / JOB_MARKER).write_text(now_iso() + "\n", encoding="utf-8")
    return job


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
    max_segments: int,
) -> Optional[TranscriptResult]:
    """Use configured native-video providers when captions and local ASR are unavailable."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    total = duration or media_duration(media_path)
    segment_seconds = 180.0
    needed = max(1, int((total + segment_seconds - 1) // segment_seconds))
    if needed > max_segments:
        return None
    segment_dir = job / "video-transcription"
    segment_dir.mkdir(parents=True, exist_ok=True)
    results: List[TranscriptSegment] = []
    for index in range(needed):
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
        )
        subprocess.run(
            [
                sys.executable,
                str(HERE / "vision_router.py"),
                "--prompt",
                prompt,
                "--video",
                str(segment_path),
                "--output",
                str(output_path),
                "--report",
                str(report_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
        if not report_path.exists() or not output_path.exists():
            return None
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
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
) -> Optional[Dict[str, Any]]:
    if not frames:
        return None
    transcript_text = transcript.markdown() if transcript else "没有可用转写，请主要依赖画面。"
    prompt = (
        "请根据视频的时间戳转写和按顺序抽取的故事板，生成中文 Markdown 内容提炼。\n"
        "要求包括：一分钟核心结论、解决的问题、章节结构、可执行步骤、关键参数、"
        "画面直接可见的信息、合理推断、缺失信息、复刻前仍需验证的事项。"
        "不要把故事板中出现的平台评论、推荐或导航当作视频正文。"
        "需要引用画面时使用对应文件名，并说明画面作用。\n"
        f"用户关注点：{focus or '完整理解视频内容'}\n\n"
        f"{transcript_text[:40000]}"
    )
    prompt_path = job / "summary-prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    report = package_dir / "vision-report.json"
    command = [
        sys.executable,
        str(HERE / "vision_router.py"),
        "--prompt-file",
        str(prompt_path),
        "--output",
        str(package_dir / "summary.md"),
        "--report",
        str(report),
    ]
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
            return json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
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
    config, _ = load_config(args.config)
    if args.output_root:
        config["paths"]["output_root"] = str(Path(args.output_root).expanduser().resolve())
    job = create_job(config)
    errors: List[Dict[str, Any]] = []
    try:
        router = SourceRouter(default_adapters(config))
        source = router.acquire(args.url, job)
    except AcquisitionError as exc:
        write_json(
            job / "errors.json", [{"stage": "acquisition", "type": exc.error_type, "message": str(exc)}]
        )
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "acquisition",
                    "error": exc.error_type,
                    "message": str(exc),
                    "job": str(job),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    errors.extend(acquisition_errors(source))
    media_path = Path(source.media_path or "")
    if not media_path.is_file():
        print(
            json.dumps(
                {"ok": False, "error": "MEDIA_NOT_FOUND", "job": str(job)}, ensure_ascii=False, indent=2
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
            mode=args.asr,
            model_name=args.asr_model,
            language=args.language,
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
        max_segments = max(1, int(config.get("vision", {}).get("max_visual_calls", 20)) - 1)
        try:
            transcript = transcribe_with_video_provider(
                media_path,
                job,
                duration=source.duration,
                max_segments=max_segments,
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            transcript = None
            limitations.append(f"原生视频视觉转写失败：{exc}")
        if transcript is not None:
            limitations.append("平台字幕和本地 ASR 不可用，本次采用外部原生视频模型分段生成音画转写。")

    interval = args.storyboard_interval
    frames: List[Path] = []
    try:
        frames = make_storyboard(media_path, job, interval, args.max_frames)
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
        visual_summary(package_dir, job, transcript, frames, args.focus) if args.vision != "none" else None
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
    result = {
        "ok": valid,
        "status": json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))["status"],
        "platform": source.platform,
        "package_dir": str(package_dir),
        "job_dir": str(job),
        "acquisition_method": source.acquisition_method,
        "transcription_method": transcript.method if transcript else "unavailable",
        "vision_status": vision_report.get("status") if vision_report else "host-agent-required",
        "validation": json.loads(validation.stdout)
        if validation.stdout.strip().startswith("{")
        else validation.stderr,
    }
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


def acquire_only(args: argparse.Namespace) -> int:
    config, _ = load_config(args.config)
    job = Path(args.work_dir).expanduser().resolve() if args.work_dir else create_job(config)
    try:
        source = SourceRouter(default_adapters(config)).acquire(args.url, job)
    except AcquisitionError as exc:
        print(
            json.dumps(
                {"ok": False, "error": exc.error_type, "message": str(exc), "job": str(job)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps({"ok": True, "job": str(job), "source": source.to_json()}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcu", description="抖音与哔哩哔哩视频内容理解 Skill CLI")
    parser.add_argument("--config", help="配置文件路径；也可使用 MEDIA_CONTENT_CONFIG")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="检查公共版运行环境")
    doctor_parser.set_defaults(func=doctor)

    acquire_parser = sub.add_parser("acquire", help="只获取并规范化来源")
    acquire_parser.add_argument("url")
    acquire_parser.add_argument("--work-dir")
    acquire_parser.set_defaults(func=acquire_only)

    analyze_parser = sub.add_parser("analyze", help="获取、转写、抽帧、视觉综合并生成理解包")
    analyze_parser.add_argument("url")
    analyze_parser.add_argument("--focus", default="")
    analyze_parser.add_argument("--output-root")
    analyze_parser.add_argument("--asr", choices=["auto", "local", "none"], default="auto")
    analyze_parser.add_argument("--asr-model", default="small")
    analyze_parser.add_argument("--language", default="zh")
    analyze_parser.add_argument("--vision", choices=["auto", "none"], default="auto")
    analyze_parser.add_argument("--storyboard-interval", type=float, default=30.0)
    analyze_parser.add_argument("--max-frames", type=int, default=20)
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
