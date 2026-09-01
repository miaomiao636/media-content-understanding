#!/usr/bin/env python3
"""Deterministically select a small final evidence plan from transcript and scene cues."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

STATIC_VISUAL_TERMS = (
    "这里可以看到",
    "可以看到",
    "看这里",
    "画面",
    "界面",
    "页面",
    "布局",
    "图表",
    "代码",
    "参数",
    "字段",
    "按钮",
    "菜单",
    "截图",
    "最终结果",
    "as shown",
    "on screen",
    "interface",
    "layout",
    "chart",
)

DYNAMIC_VISUAL_TERMS = (
    "动画",
    "动效",
    "过渡",
    "转场",
    "交互",
    "点击",
    "滑动",
    "拖动",
    "切换",
    "状态变化",
    "前后变化",
    "演示",
    "效果",
    "animation",
    "transition",
    "interaction",
    "click",
    "swipe",
)


def _segment_value(segment: Any, key: str, default: Any) -> Any:
    if isinstance(segment, dict):
        return segment.get(key, default)
    return getattr(segment, key, default)


def _normalize_segments(segments: Sequence[Any], duration: float) -> List[Tuple[float, float, str]]:
    normalized = []
    for segment in segments:
        try:
            start = max(0.0, float(_segment_value(segment, "start", 0.0)))
            end = max(start, float(_segment_value(segment, "end", start)))
        except (TypeError, ValueError):
            continue
        text = str(_segment_value(segment, "text", "") or "").strip()
        if duration > 0:
            start = min(duration, start)
            end = min(duration, end)
        normalized.append((start, end, text))
    return normalized


def _terms(text: str, vocabulary: Sequence[str]) -> List[str]:
    lowered = text.casefold()
    return [term for term in vocabulary if term.casefold() in lowered]


def _near_scene(moment: float, scenes: Sequence[float], window: float) -> Optional[float]:
    nearby = [scene for scene in scenes if abs(scene - moment) <= window]
    return min(nearby, key=lambda scene: abs(scene - moment)) if nearby else None


def _clip_bounds(moment: float, duration: float, clip_seconds: float) -> Tuple[float, float]:
    if duration <= clip_seconds:
        return 0.0, max(0.0, duration)
    start = max(0.0, moment - clip_seconds / 2)
    end = min(duration, start + clip_seconds)
    start = max(0.0, end - clip_seconds)
    return round(start, 3), round(end, 3)


def _choose_images(candidates: List[Dict[str, Any]], limit: int, window: float) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (-item["score"], item["timestamp_seconds"])):
        if any(abs(candidate["timestamp_seconds"] - item["timestamp_seconds"]) < window for item in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _choose_clips(candidates: List[Dict[str, Any]], limit: int, window: float) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (-item["score"], item["start_seconds"])):
        center = candidate["timestamp_seconds"]
        if any(
            abs(center - item["timestamp_seconds"]) < window
            or (
                candidate["start_seconds"] < item["end_seconds"]
                and candidate["end_seconds"] > item["start_seconds"]
            )
            for item in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def build_evidence_plan(
    transcript_segments: Sequence[Any],
    scene_changes: Sequence[float],
    *,
    duration: float,
    max_images: int = 6,
    max_clips: int = 3,
    dedupe_seconds: float = 4.0,
    clip_seconds: float = 12.0,
) -> List[Dict[str, Any]]:
    """Build a bounded, de-duplicated image/clip plan without touching media files."""
    duration = max(0.0, float(duration or 0.0))
    if max_images < 0 or max_clips < 0:
        raise ValueError("max_images 和 max_clips 不能为负数")
    if dedupe_seconds <= 0 or clip_seconds <= 0:
        raise ValueError("dedupe_seconds 和 clip_seconds 必须大于 0")

    normalized_scenes = set()
    for value in scene_changes:
        try:
            scene = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= scene <= duration:
            normalized_scenes.add(round(scene, 3))
    scenes = sorted(normalized_scenes)
    segments = _normalize_segments(transcript_segments, duration)
    image_candidates: List[Dict[str, Any]] = []
    clip_candidates: List[Dict[str, Any]] = []

    for start, end, text in segments:
        moment = min(duration, (start + end) / 2)
        static_terms = _terms(text, STATIC_VISUAL_TERMS)
        dynamic_terms = _terms(text, DYNAMIC_VISUAL_TERMS)
        nearby_scene = _near_scene(moment, scenes, max(2.0, dedupe_seconds))
        if static_terms:
            timestamp = nearby_scene if nearby_scene is not None else moment
            signals = ["visual-language"]
            score = 5
            if nearby_scene is not None:
                signals.append("scene-change")
                score += 2
            image_candidates.append(
                {
                    "type": "image",
                    "timestamp_seconds": round(timestamp, 3),
                    "reason": "字幕包含视觉指代或界面信息",
                    "description": text[:120] or "关键静态画面",
                    "signals": signals,
                    "score": score,
                }
            )
        if dynamic_terms:
            timestamp = nearby_scene if nearby_scene is not None else moment
            clip_start, clip_end = _clip_bounds(timestamp, duration, clip_seconds)
            if clip_end <= clip_start:
                continue
            signals = ["dynamic-language"]
            score = 7
            if nearby_scene is not None:
                signals.append("scene-change")
                score += 2
            clip_candidates.append(
                {
                    "type": "clip",
                    "start_seconds": clip_start,
                    "end_seconds": clip_end,
                    "timestamp_seconds": round(timestamp, 3),
                    "reason": "保留动态交互、转场或状态变化",
                    "description": text[:120] or "关键动态过程",
                    "signals": signals,
                    "score": score,
                }
            )

    for scene in scenes:
        covering_text = next((text for start, end, text in segments if start - 1 <= scene <= end + 1), "")
        signal_count = len(_terms(covering_text, STATIC_VISUAL_TERMS + DYNAMIC_VISUAL_TERMS))
        image_candidates.append(
            {
                "type": "image",
                "timestamp_seconds": scene,
                "reason": "场景变化后的代表性画面",
                "description": covering_text[:120] or "场景切换点的画面状态",
                "signals": ["scene-change"],
                "score": 3 + min(signal_count, 2),
            }
        )

    if not image_candidates and max_images and duration >= 0:
        image_candidates.append(
            {
                "type": "image",
                "timestamp_seconds": round(duration / 2, 3),
                "reason": "保留全片代表性基线画面",
                "description": "无可用视觉触发词或场景切换时的中点画面",
                "signals": ["baseline"],
                "score": 1,
            }
        )

    images = _choose_images(image_candidates, max_images, dedupe_seconds) if max_images else []
    clips = _choose_clips(clip_candidates, max_clips, dedupe_seconds) if max_clips else []
    plan = images + clips
    plan.sort(key=lambda item: (item["timestamp_seconds"], item["type"]))
    for item in plan:
        item.pop("score", None)
    return plan


select_evidence = build_evidence_plan
