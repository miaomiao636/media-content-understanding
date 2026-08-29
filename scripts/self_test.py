#!/usr/bin/env python3
"""Offline behavioral smoke tests for deterministic helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from console import configure_utf8_stdio
from vision_router import MediaInput, parse_sse, prepare_request, sanitize_message

configure_utf8_stdio()

HERE = Path(__file__).resolve().parent


def run(command, expected=(0,)):
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode not in expected:
        raise AssertionError(
            f"命令失败 {completed.returncode}: {' '.join(map(str, command))}\n{completed.stdout}\n{completed.stderr}"
        )
    return completed


def main() -> int:
    sanitized = sanitize_message(
        "authorization: Bearer-secret https://example.com/media?token=secret-value",
        ["Bearer-secret"],
    )
    assert "Bearer-secret" not in sanitized and "secret-value" not in sanitized
    with tempfile.TemporaryDirectory(prefix="media-skill-test-") as temp:
        root = Path(temp)
        package = root / "package"
        run(
            [
                sys.executable,
                str(HERE / "package_tool.py"),
                "init",
                str(package),
                "--source-url",
                "https://example.com/item/1",
                "--platform",
                "test",
                "--source-id",
                "1",
                "--content-type",
                "long_text",
            ]
        )
        validation = run([sys.executable, str(HERE / "package_tool.py"), "validate", str(package)])
        assert json.loads(validation.stdout)["ok"] is True
        manifest_path = package / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "completed"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (package / "summary.md").write_text("# 内容提炼\n\n测试摘要。\n", encoding="utf-8")
        validation = run([sys.executable, str(HERE / "package_tool.py"), "validate", str(package)])
        assert json.loads(validation.stdout)["ok"] is True

        config = root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "paths": {"temp_root": str(root / "cache"), "output_root": str(root / "output")},
                    "vision": {
                        "host_fallback": True,
                        "providers": [
                            {
                                "id": "broken-auth",
                                "enabled": True,
                                "priority": 1,
                                "adapter": "openai-compatible",
                                "model": "vision-test",
                                "base_url": "https://example.invalid/v1",
                                "api_key_env": "MISSING_TEST_KEY",
                                "capabilities": ["image"],
                                "max_retries": 0,
                            },
                            {
                                "id": "broken-adapter",
                                "enabled": True,
                                "priority": 2,
                                "adapter": "unsupported",
                                "model": "vision-test-2",
                                "base_url": "https://example.invalid/v1",
                                "api_key_env": "TEST_ONLY_API_KEY",
                                "capabilities": ["image"],
                                "max_retries": 0,
                            },
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        image = root / "tiny.png"
        image.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        report = root / "report.json"
        os.environ["TEST_ONLY_API_KEY"] = "test-only"
        routed = run(
            [
                sys.executable,
                str(HERE / "vision_router.py"),
                "--config",
                str(config),
                "--prompt",
                "describe",
                "--image",
                str(image),
                "--report",
                str(report),
            ],
            expected=(21,),
        )
        os.environ.pop("TEST_ONLY_API_KEY", None)
        payload = json.loads(routed.stdout)
        assert payload["errors"][0]["type"] == "AUTHENTICATION_ERROR"
        assert payload["errors"][0]["suggestion"]
        assert payload["errors"][1]["type"] == "CONFIGURATION_ERROR"
        assert payload["attempted_providers"] == ["broken-auth", "broken-adapter"]

        media = MediaInput(kind="image", images=[image])
        qwen_headers, qwen_body, qwen_stream = prepare_request(
            {
                "adapter": "openai-compatible",
                "request_profile": "qwen-omni",
                "model": "qwen3.5-omni-plus",
                "max_output_tokens": 50,
            },
            "describe",
            media,
            "secret",
        )
        assert qwen_stream is True
        assert qwen_headers["Authorization"] == "Bearer secret"
        assert qwen_body["stream"] is True and qwen_body["modalities"] == ["text"]
        mimo_headers, mimo_body, mimo_stream = prepare_request(
            {
                "adapter": "openai-compatible",
                "request_profile": "xiaomi-mimo",
                "model": "mimo-v2.5",
                "max_output_tokens": 50,
            },
            "describe",
            media,
            "secret",
        )
        assert mimo_stream is False
        assert mimo_headers["api-key"] == "secret"
        assert mimo_body["thinking"] == {"type": "disabled"}
        sse = parse_sse(
            [
                b'data: {"model":"qwen3.5-omni-plus","choices":[{"delta":{"content":"ok"}}]}\n',
                b'data: {"choices":[],"usage":{"total_tokens":2}}\n',
                b"data: [DONE]\n",
            ]
        )
        assert sse.text == "ok" and sse.usage == {"total_tokens": 2}

        run([sys.executable, str(HERE / "cleanup.py"), "--config", str(config), "init-root"])
        job = root / "cache" / "job-1"
        run([sys.executable, str(HERE / "cleanup.py"), "--config", str(config), "register", str(job)])
        os.utime(job, (1, 1))
        preview = run(
            [
                sys.executable,
                str(HERE / "cleanup.py"),
                "--config",
                str(config),
                "clean",
                "--dry-run",
                "--older-than-hours",
                "0",
            ]
        )
        assert str(job.resolve()) in json.loads(preview.stdout)["candidates"], preview.stdout

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            video = root / "input.mp4"
            run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x240:rate=10:duration=3",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:duration=3",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(video),
                ]
            )
            frame = root / "frame.jpg"
            clip = root / "clip.mp4"
            audio = root / "audio.wav"
            board = root / "storyboard"
            run(
                [
                    sys.executable,
                    str(HERE / "media_tools.py"),
                    "frame",
                    str(video),
                    "--at",
                    "1",
                    "--output",
                    str(frame),
                ]
            )
            run(
                [
                    sys.executable,
                    str(HERE / "media_tools.py"),
                    "clip",
                    str(video),
                    "--start",
                    "0.5",
                    "--end",
                    "2",
                    "--output",
                    str(clip),
                ]
            )
            run([sys.executable, str(HERE / "media_tools.py"), "audio", str(video), "--output", str(audio)])
            run(
                [
                    sys.executable,
                    str(HERE / "media_tools.py"),
                    "storyboard",
                    str(video),
                    "--interval",
                    "1",
                    "--max-frames",
                    "3",
                    "--output-dir",
                    str(board),
                ]
            )
            assert frame.stat().st_size > 0 and clip.stat().st_size > 0 and audio.stat().st_size > 0
            assert len(list(board.glob("*.jpg"))) == 3
    print("media-content-understanding self-test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
