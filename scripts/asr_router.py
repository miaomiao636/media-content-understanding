#!/usr/bin/env python3
"""Subtitle normalization and optional local ASR fallback."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


class TranscriptionError(RuntimeError):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    method: str
    language: str
    segments: List[TranscriptSegment]
    source_path: str

    def markdown(self) -> str:
        lines = ["# 时间戳转写", ""]
        for segment in self.segments:
            lines.append(f"- `{format_timestamp(segment.start)}` {segment.text}")
        return "\n".join(lines).rstrip() + "\n"


def parse_timestamp(value: str) -> float:
    normalized = value.replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    raise ValueError(f"无效字幕时间：{value}")


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, second = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{second:02d}"
    return f"{minutes:02d}:{second:02d}"


def clean_caption(text: str) -> str:
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_vtt_or_srt(path: Path) -> List[TranscriptSegment]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = raw.splitlines()
    segments: List[TranscriptSegment] = []
    index = 0
    previous = ""
    while index < len(lines):
        match = TIMESTAMP_RE.search(lines[index])
        if not match:
            index += 1
            continue
        start = parse_timestamp(match.group("start"))
        end = parse_timestamp(match.group("end"))
        index += 1
        body: List[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index])
            index += 1
        text = clean_caption(" ".join(body))
        if text and text != previous:
            segments.append(TranscriptSegment(start=start, end=end, text=text))
            previous = text
        index += 1
    return segments


def choose_subtitle(paths: Sequence[Path]) -> Optional[Path]:
    if not paths:
        return None
    priorities = ("zh-hans", "zh-cn", ".zh.", "chinese", "zh", "en")
    lowered = [(path, path.name.lower()) for path in paths]
    for marker in priorities:
        for path, name in lowered:
            if marker in name:
                return path
    return paths[0]


def transcript_from_subtitles(paths: Sequence[Path]) -> Optional[TranscriptResult]:
    selected = choose_subtitle(paths)
    if selected is None:
        return None
    if selected.suffix.lower() not in {".vtt", ".srt"}:
        raise TranscriptionError("UNSUPPORTED_SUBTITLE", f"暂不支持字幕格式：{selected.suffix}")
    segments = parse_vtt_or_srt(selected)
    if not segments:
        raise TranscriptionError("EMPTY_TRANSCRIPT", f"字幕文件没有有效内容：{selected.name}")
    language = "zh" if "zh" in selected.name.lower() else "unknown"
    return TranscriptResult(
        method="platform-subtitle", language=language, segments=segments, source_path=str(selected)
    )


def extract_audio(media_path: Path, output_path: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TranscriptionError("MISSING_DEPENDENCY", "缺少 ffmpeg，无法提取音频")
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(media_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise TranscriptionError("AUDIO_EXTRACTION_FAILED", completed.stderr[-2000:])
    return output_path


def faster_whisper_available() -> bool:
    try:
        __import__("faster_whisper")
        return True
    except ImportError:
        return False


def transcribe_local(
    media_path: Path,
    work_dir: Path,
    *,
    model_name: str = "small",
    language: str = "zh",
    device: str = "auto",
) -> TranscriptResult:
    if not faster_whisper_available():
        raise TranscriptionError(
            "MISSING_DEPENDENCY",
            "没有平台字幕且未安装 faster-whisper；运行 `pip install -e .[asr]` 或配置其他 ASR",
        )
    audio_path = extract_audio(media_path, work_dir / "asr-audio.wav")
    try:
        from faster_whisper import WhisperModel  # type: ignore

        compute_type = "int8" if device in {"auto", "cpu"} else "float16"
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        iterator, info = model.transcribe(
            str(audio_path),
            language=language or None,
            vad_filter=True,
            beam_size=5,
        )
        segments = [
            TranscriptSegment(start=float(item.start), end=float(item.end), text=clean_caption(item.text))
            for item in iterator
            if clean_caption(item.text)
        ]
    except Exception as exc:
        raise TranscriptionError("ASR_FAILED", f"faster-whisper 转写失败：{exc}") from exc
    if not segments:
        raise TranscriptionError("EMPTY_TRANSCRIPT", "ASR 没有产生有效文字")
    detected = str(getattr(info, "language", None) or language or "unknown")
    return TranscriptResult(
        method=f"faster-whisper:{model_name}",
        language=detected,
        segments=segments,
        source_path=str(audio_path),
    )


def get_transcript(
    subtitle_paths: Sequence[Path],
    media_path: Path,
    work_dir: Path,
    *,
    mode: str = "auto",
    model_name: str = "small",
    language: str = "zh",
) -> TranscriptResult:
    subtitle_result = transcript_from_subtitles(subtitle_paths)
    if subtitle_result is not None:
        return subtitle_result
    if mode == "none":
        raise TranscriptionError("TRANSCRIPTION_DISABLED", "未发现字幕，且 ASR 已禁用")
    if mode not in {"auto", "local"}:
        raise TranscriptionError("CONFIGURATION_ERROR", f"未知 ASR 模式：{mode}")
    return transcribe_local(media_path, work_dir, model_name=model_name, language=language)
