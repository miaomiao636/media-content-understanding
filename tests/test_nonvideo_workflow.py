import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.mcu as mcu_module
from scripts.mcu import analyze, build_parser
from scripts.package_tool import validate
from scripts.source_adapter import AcquiredSource, AcquisitionError


def _runtime_config(tmp_path: Path) -> dict:
    return {
        "paths": {
            "temp_root": str(tmp_path / "cache"),
            "output_root": str(tmp_path / "output"),
        },
        "acquisition": {
            "browser_fallback": False,
            "browser_headless": True,
            "browser_profile_dir": "",
            "cookie_browser": "",
            "max_download_mb": 10,
        },
        "asr": {"mode": "none", "local_model": "small", "language": "zh"},
        "retention": {
            "cleanup_on_success": True,
            "failed_job_retention_hours": 72,
            "cache_ttl_days": 7,
            "max_cache_gb": 20,
            "keep_source_media": False,
        },
        "vision": {
            "host_fallback": True,
            "verification_mode": "low-confidence",
            "max_visual_calls": 4,
            "max_frames": 20,
            "max_upload_mb": 5,
            "providers": [],
        },
        "evidence": {
            "max_images": 6,
            "max_clips": 3,
            "dedupe_seconds": 4,
            "clip_seconds": 12,
            "scene_threshold": 0.3,
            "max_scene_changes": 120,
            "storyboard_max_width": 1280,
            "storyboard_max_height": 720,
        },
    }


def _source(tmp_path: Path, kind: str) -> AcquiredSource:
    image_paths = []
    if kind in {"gallery", "mixed"}:
        source_images = tmp_path / f"{kind}-source-images"
        source_images.mkdir()
        for index in range(1, 3):
            image = source_images / f"{index:03d}.webp"
            image.write_bytes(b"RIFF\x00\x00\x00\x00WEBP" + bytes([index]))
            image_paths.append(str(image))
    body_text = "作者正文：三步整理旅行照片。" if kind in {"long_text", "mixed"} else ""
    return AcquiredSource(
        platform="douyin",
        input_url=f"https://www.douyin.com/note/{kind}",
        canonical_url=f"https://www.douyin.com/note/{kind}",
        source_id=f"note-{kind}",
        title=f"{kind} 测试",
        author="示例作者",
        duration=None,
        published_at="2026-08-31T00:00:00+00:00",
        media_path=None,
        acquisition_method="offline-fixture",
        content_kind=kind,
        body_text=body_text,
        image_paths=image_paths,
    )


def _run_analyze(monkeypatch, tmp_path: Path, capsys, source: AcquiredSource, *extra: str):
    config = _runtime_config(tmp_path)

    class OfflineRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            return source

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", OfflineRouter)
    args = build_parser().parse_args(
        ["analyze", source.input_url, "--vision", "none", *extra]
    )
    exit_code = analyze(args)
    payload = json.loads(capsys.readouterr().out)
    return exit_code, payload, config


@pytest.mark.parametrize("kind", ["long_text", "gallery", "mixed"])
def test_nonvideo_analyze_routes_kind_without_transcript_or_temporal_video_fields(
    monkeypatch, tmp_path, capsys, kind
):
    source = _source(tmp_path, kind)

    exit_code, payload, _config = _run_analyze(
        monkeypatch, tmp_path, capsys, source
    )

    package = Path(payload["package_dir"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["ok"] is True
    assert manifest["content"]["kind"] == kind
    assert "transcript_file" not in manifest["content"]
    assert "transcription_method" not in manifest["processing"]
    assert "transcription_method" not in payload
    assert not (package / "transcript.md").exists()
    assert not (package / "media" / "clips").exists()
    assert validate(package)["ok"] is True

    source_content = (package / "source-content.md").read_text(encoding="utf-8")
    assert "## 作者正文（直接来源）" in source_content
    if source.body_text:
        assert source.body_text in source_content
    else:
        assert "作者未提供独立正文" in source_content

    summary = (package / "summary.md").read_text(encoding="utf-8")
    assert "当前为自动准备稿" in summary
    assert "Agent/自动摘要" in summary

    if kind == "long_text":
        assert manifest["media"] == []
        assert "image_analysis_file" not in manifest["content"]
    else:
        assert manifest["content"]["image_analysis_file"] == "image-analysis.md"
        image_analysis = (package / "image-analysis.md").read_text(encoding="utf-8")
        assert "图片 OCR（自动提取，非作者正文）" in image_analysis
        assert "视觉推断（派生内容，非作者正文）" in image_analysis
        assert [item["image_index"] for item in manifest["media"]] == [1, 2]
        assert all(item["type"] == "image" for item in manifest["media"])
        assert all("timestamp" not in item and "time_range" not in item for item in manifest["media"])


def test_nonvideo_visual_analysis_uses_shared_budget_and_keeps_provenance_layers(
    monkeypatch, tmp_path, capsys
):
    source = _source(tmp_path, "mixed")
    config = _runtime_config(tmp_path)
    config_path = tmp_path / "isolated-config.json"
    real_run = subprocess.run
    observed_command = []

    class OfflineRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            return source

    def fake_run(command, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("vision_router.py"):
            observed_command.extend(command)
            output = Path(command[command.index("--output") + 1])
            report = Path(command[command.index("--report") + 1])
            output.write_text(
                json.dumps(
                    {
                        "images": [
                            {
                                "image_index": 1,
                                "ocr_text": "旅行照片",
                                "visible_facts": ["蓝色背景"],
                                "visual_inferences": [
                                    {"text": "可能是旅行主题", "confidence": "medium"}
                                ],
                            },
                            {
                                "image_index": 2,
                                "ocr_text": "按地点归档",
                                "visible_facts": ["文件夹列表"],
                                "visual_inferences": [],
                            },
                        ],
                        "overall_limitations": ["仅分析静态图片"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report.write_text(
                json.dumps(
                    {
                        "status": "external_success",
                        "selected_provider": "offline-provider",
                        "selected_model": "offline-model",
                        "api_calls_used": 1,
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, config_path))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", OfflineRouter)
    monkeypatch.setattr("scripts.mcu.subprocess.run", fake_run)
    args = build_parser().parse_args(["analyze", source.input_url])

    exit_code = analyze(args)
    payload = json.loads(capsys.readouterr().out)
    package = Path(payload["package_dir"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    analysis = (package / "image-analysis.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert observed_command[observed_command.index("--max-api-calls") + 1] == "4"
    assert observed_command[observed_command.index("--config") + 1] == str(config_path)
    assert observed_command.count("--image") == 2
    assert payload["visual_call_budget"] == {"limit": 4, "used": 1, "remaining": 3}
    assert manifest["processing"]["image_analysis_method"] == "external-vision"
    assert manifest["processing"]["vision_provider"] == "offline-provider/offline-model"
    assert "作者正文仅在 `source-content.md`" in analysis
    assert "旅行照片" in analysis
    assert "可能是旅行主题（置信度：medium）" in analysis


def test_nonvideo_visual_failure_is_redacted_and_budgeted(monkeypatch, tmp_path, capsys):
    source = _source(tmp_path, "gallery")
    config = _runtime_config(tmp_path)
    real_run = subprocess.run

    class OfflineRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            return source

    def fake_run(command, **kwargs):
        if len(command) > 1 and str(command[1]).endswith("vision_router.py"):
            report = Path(command[command.index("--report") + 1])
            report.write_text(
                json.dumps(
                    {
                        "status": "external_exhausted",
                        "selected_provider": None,
                        "selected_model": None,
                        "api_calls_used": 1,
                        "errors": [
                            {
                                "stage": "visual_analysis",
                                "provider": "broken-provider",
                                "type": "SERVER_ERROR",
                                "message": (
                                    "api_key=sk-secret-value "
                                    "https://api.example/v1?signature=secret"
                                ),
                                "suggestion": "retry Authorization: Bearer hidden-token",
                                "retryable": True,
                                "occurred_at": "2026-08-31T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 21, "", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", OfflineRouter)
    monkeypatch.setattr("scripts.mcu.subprocess.run", fake_run)
    args = build_parser().parse_args(["analyze", source.input_url])

    exit_code = analyze(args)
    payload = json.loads(capsys.readouterr().out)
    package = Path(payload["package_dir"])
    errors_text = (package / "errors.json").read_text(encoding="utf-8")
    errors = json.loads(errors_text)

    assert exit_code == 0
    assert payload["visual_call_budget"] == {"limit": 4, "used": 1, "remaining": 3}
    assert "sk-secret-value" not in errors_text
    assert "hidden-token" not in errors_text
    assert "signature=secret" not in errors_text
    assert "[REDACTED]" in errors_text
    assert errors[0]["stage"] == "image-analysis"
    assert errors[0]["type"] == "SERVER_ERROR"
    assert errors[0]["suggestion"]


def test_unreviewed_nonvideo_image_analysis_blocks_finalize(
    monkeypatch, tmp_path, capsys
):
    source = _source(tmp_path, "mixed")
    exit_code, payload, _config = _run_analyze(
        monkeypatch, tmp_path, capsys, source
    )
    assert exit_code == 0
    package = Path(payload["package_dir"])
    (package / "summary.md").write_text(
        """# 内容提炼

## 一分钟核心结论

作者介绍了整理旅行照片的方法；包中图片仅作为顺序和画面证据。

## 解决的问题与适用场景

解决旅行照片难以快速分类的问题，适用于个人相册整理。

## 主题结构

先说明目标，再展示图片，最后提醒人工核对。

## 可执行步骤与关键参数

先筛选照片，再按地点建立分类，最后检查重复项；来源未提供可核对的数值参数。

## 视觉证据与证据作用

图片 1 和图片 2 保留了原始顺序；本摘要不将未校订 OCR 当作作者正文。

## 来源与推断、缺失信息

方法概述来自作者正文；适用场景是 Agent 推断。图片 OCR 和细节尚未人工校订。

## 复刻前仍需验证

需要人工检查每张图片的文字和分类结果，再决定是否复刻。
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "mcu.py"),
            "finalize",
            str(package),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 3
    assert result["ok"] is False
    assert result["status"] == "partial"
    assert {item["code"] for item in result["blockers"]} == {
        "IMAGE_ANALYSIS_REVIEW_REQUIRED"
    }
    assert result["gates"]["visual_evidence"] is False
    assert validate(package)["ok"] is True


def test_reviewed_nonvideo_image_analysis_can_finalize_to_completed(
    monkeypatch, tmp_path, capsys
):
    source = _source(tmp_path, "mixed")
    exit_code, payload, _config = _run_analyze(monkeypatch, tmp_path, capsys, source)
    assert exit_code == 0
    package = Path(payload["package_dir"])
    (package / "summary.md").write_text(
        """# 内容提炼

## 一分钟核心结论
作者介绍了整理旅行照片的方法，图片证据确认了照片顺序。
## 解决的问题与适用场景
解决旅行照片难分类的问题，适用于个人相册整理。
## 主题结构
先说明目标，再展示图片，最后说明核对边界。
## 可执行步骤与关键参数
先筛选照片，再按地点分类，最后检查重复项；来源未给数值参数。
## 视觉证据与证据作用
图片 1 和图片 2 已逐张核对，分别用于确认旅行场景和原始顺序。
## 来源与推断、缺失信息
方法来自作者正文；画面事实来自逐图核对；适用场景属于 Agent 推断。
## 复刻前仍需验证
复刻前需确认用户自己的目录结构与命名规则。
""",
        encoding="utf-8",
    )
    (package / "image-analysis.md").write_text(
        """# 图片 OCR 与视觉推断

## 图片 1
### 图片 OCR（自动提取，非作者正文）
未识别到可读文字。
### 画面直接可见事实（已核对）
- 旅行照片。
### 视觉推断（派生内容，非作者正文）
- 可能用于按地点分类（置信度：中）。

## 图片 2
### 图片 OCR（自动提取，非作者正文）
未识别到可读文字。
### 画面直接可见事实（已核对）
- 第二张旅行照片。
### 视觉推断（派生内容，非作者正文）
- 无必要推断。
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "scripts" / "mcu.py"), "finalize", str(package)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    result = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert result["ok"] is True
    assert result["status"] == "completed"


def test_analyze_normalizes_public_iesdouyin_share_note_and_preserves_input_url(
    monkeypatch, tmp_path, capsys
):
    original_url = "https://www.iesdouyin.com/share/note/7659275356428852849/"
    expected_route_url = "https://www.douyin.com/note/7659275356428852849"
    source = _source(tmp_path, "long_text")
    observed = []
    config = _runtime_config(tmp_path)

    class ShareRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            observed.append(url)
            return source

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", ShareRouter)
    args = build_parser().parse_args(["analyze", original_url, "--vision", "none"])

    exit_code = analyze(args)
    payload = json.loads(capsys.readouterr().out)
    package = Path(payload["package_dir"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert observed == [expected_route_url]
    assert manifest["source"]["input_url"] == original_url


def test_public_short_link_must_resolve_to_a_specific_content_page(monkeypatch):
    short_url = "https://v.douyin.com/Cei74dPN/"
    monkeypatch.setattr(
        mcu_module,
        "resolve_share_url",
        lambda value: (
            "https://www.douyin.com/user/"
            "MS4wLjABAAAAWdRwGedPfxvQsxpuAinLfk1U1eNLIj64TBfG1mnphdvdQHbmbzTlan1fL72PX-9F"
        ),
        raising=False,
    )

    with pytest.raises(AcquisitionError, match="作品页") as exc_info:
        mcu_module.prepare_source_entry_url(short_url)

    assert exc_info.value.error_type == "UNSUPPORTED_SOURCE"
