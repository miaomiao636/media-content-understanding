import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.evidence_selector import build_evidence_plan
from scripts.mcu import materialize_evidence


def _run(command):
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    return completed


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
def test_synthetic_video_generates_final_screenshot_and_probeable_clip(tmp_path):
    source = tmp_path / "synthetic.mp4"
    _run(
        [
            shutil.which("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=12:duration=5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ]
    )
    plan = build_evidence_plan(
        [
            {"start": 0.5, "end": 1.5, "text": "这里可以看到最终界面"},
            {"start": 2.0, "end": 3.5, "text": "接下来点击按钮展示状态变化动画"},
        ],
        scene_changes=[1.0, 2.5],
        duration=5.0,
        max_images=2,
        max_clips=1,
        clip_seconds=2.0,
    )
    package = tmp_path / "package"
    limitations = []
    errors = []

    rows = materialize_evidence(source, package, plan, limitations, errors)

    images = [row for row in rows if row["type"] == "image"]
    clips = [row for row in rows if row["type"] == "clip"]
    assert images and clips
    assert limitations == []
    assert errors == []
    assert all((package / row["path"]).stat().st_size > 0 for row in rows)
    probe = _run(
        [
            shutil.which("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(package / clips[0]["path"]),
        ]
    )
    assert json.loads(probe.stdout)["streams"][0]["codec_name"] == "h264"


def test_clip_failure_falls_back_to_screenshot_and_records_limitation(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    package = tmp_path / "package"
    limitations = []
    errors = []

    def fake_helper(name, arguments, **kwargs):
        if arguments[0] == "clip":
            return subprocess.CompletedProcess(arguments, 2, "", "encoding failed")
        output = Path(arguments[arguments.index("--output") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"jpeg")
        return subprocess.CompletedProcess(arguments, 0, str(output), "")

    monkeypatch.setattr("scripts.mcu.run_helper", fake_helper)
    plan = [
        {
            "type": "clip",
            "start_seconds": 4.0,
            "end_seconds": 10.0,
            "timestamp_seconds": 7.0,
            "reason": "保留动态交互与状态变化",
            "description": "动态过程",
            "signals": ["dynamic-language"],
        }
    ]

    rows = materialize_evidence(source, package, plan, limitations, errors)

    assert [row["type"] for row in rows] == ["image"]
    assert "短片提取失败" in limitations[0]
    assert errors[0]["type"] == "EVIDENCE_CLIP_FAILED"
    assert "降级" in rows[0]["reason"]


def test_storyboard_filter_scales_inside_ffmpeg_pipeline():
    from scripts.media_tools import storyboard_filter

    value = storyboard_filter(interval=10.0, max_width=1280, max_height=720)

    assert "fps=1/10.0" in value
    assert "scale=" in value
    assert "force_original_aspect_ratio=decrease" in value
