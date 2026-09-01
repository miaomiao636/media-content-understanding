#!/usr/bin/env python3
"""Deterministic FFmpeg helpers for evidence extraction."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from .console import configure_utf8_stdio
except ImportError:
    from console import configure_utf8_stdio

configure_utf8_stdio()


def require_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"缺少依赖：{name}")
    return path


def parse_time(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        parts = value.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"无效时间：{value}")
        numbers = [float(part) for part in parts]
        if len(numbers) == 2:
            return numbers[0] * 60 + numbers[1]
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def run(command: list) -> None:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        tail = completed.stderr[-3000:]
        raise RuntimeError(f"媒体处理失败：{tail}")


def ensure_output(path: str) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def storyboard_filter(interval: float, max_width: int, max_height: int) -> str:
    """Resize selected storyboard frames inside FFmpeg instead of post-processing full-size JPEGs."""
    scale = (
        f"scale=w='min(iw,{max_width})':h='min(ih,{max_height})':"
        "force_original_aspect_ratio=decrease"
    )
    return f"fps=1/{interval},{scale}"


def detect_scene_changes(source: Path, threshold: float, max_scenes: int) -> list[float]:
    ffmpeg = require_bin("ffmpeg")
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-an",
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode:
        raise RuntimeError(f"场景检测失败：{completed.stderr[-3000:]}")
    values = []
    for match in re.finditer(r"\bpts_time:([-+0-9.eE]+)", completed.stderr):
        try:
            value = round(float(match.group(1)), 3)
        except ValueError:
            continue
        if not values or value != values[-1]:
            values.append(value)
        if len(values) >= max_scenes:
            break
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe")
    probe.add_argument("input")

    frame = sub.add_parser("frame")
    frame.add_argument("input")
    frame.add_argument("--at", required=True)
    frame.add_argument("--output", required=True)

    clip = sub.add_parser("clip")
    clip.add_argument("input")
    clip.add_argument("--start", required=True)
    clip.add_argument("--end", required=True)
    clip.add_argument("--output", required=True)

    storyboard = sub.add_parser("storyboard")
    storyboard.add_argument("input")
    storyboard.add_argument("--interval", type=float, default=10.0)
    storyboard.add_argument("--max-frames", type=int, default=60)
    storyboard.add_argument("--max-width", type=int, default=1280)
    storyboard.add_argument("--max-height", type=int, default=720)
    storyboard.add_argument("--output-dir", required=True)

    scenes = sub.add_parser("scenes")
    scenes.add_argument("input")
    scenes.add_argument("--threshold", type=float, default=0.3)
    scenes.add_argument("--max-scenes", type=int, default=120)

    audio = sub.add_parser("audio")
    audio.add_argument("input")
    audio.add_argument("--output", required=True)

    args = parser.parse_args()
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        print(f"输入文件不存在：{source}", file=sys.stderr)
        return 2

    try:
        if args.command == "probe":
            ffprobe = require_bin("ffprobe")
            completed = subprocess.run(
                [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode:
                raise RuntimeError(completed.stderr[-3000:])
            payload = json.loads(completed.stdout)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "scenes":
            if not 0 < args.threshold <= 1:
                raise ValueError("threshold 必须大于 0 且不大于 1")
            if args.max_scenes <= 0:
                raise ValueError("max-scenes 必须大于 0")
            print(
                json.dumps(
                    detect_scene_changes(source, args.threshold, args.max_scenes),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        ffmpeg = require_bin("ffmpeg")
        if args.command == "frame":
            output = ensure_output(args.output)
            run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    str(parse_time(args.at)),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(output),
                ]
            )
            print(output)
        elif args.command == "clip":
            start = parse_time(args.start)
            end = parse_time(args.end)
            if end <= start:
                raise ValueError("--end 必须大于 --start")
            output = ensure_output(args.output)
            run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    str(start),
                    "-i",
                    str(source),
                    "-t",
                    str(end - start),
                    "-c:v",
                    "libx264",
                    "-crf",
                    "20",
                    "-preset",
                    "fast",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(output),
                ]
            )
            print(output)
        elif args.command == "storyboard":
            if (
                args.interval <= 0
                or args.max_frames <= 0
                or args.max_width <= 0
                or args.max_height <= 0
            ):
                raise ValueError("interval、max-frames、max-width 和 max-height 必须大于 0")
            output_dir = Path(args.output_dir).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            pattern = output_dir / "%03d.jpg"
            run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    storyboard_filter(args.interval, args.max_width, args.max_height),
                    "-frames:v",
                    str(args.max_frames),
                    "-q:v",
                    "3",
                    str(pattern),
                ]
            )
            files = sorted(str(path) for path in output_dir.glob("*.jpg"))
            print(json.dumps(files, ensure_ascii=False, indent=2))
        elif args.command == "audio":
            output = ensure_output(args.output)
            run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(output),
                ]
            )
            print(output)
        return 0
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
