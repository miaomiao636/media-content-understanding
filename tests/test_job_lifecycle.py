import json
from pathlib import Path

from scripts.asr_router import TranscriptResult, TranscriptSegment
from scripts.cleanup import JOB_STATE
from scripts.mcu import analyze, build_parser
from scripts.source_adapter import AcquiredSource, AcquisitionError


def runtime_config(tmp_path):
    return {
        "paths": {
            "temp_root": str(tmp_path / "cache"),
            "output_root": str(tmp_path / "output"),
        },
        "acquisition": {
            "browser_fallback": False,
            "browser_headless": False,
            "cookie_browser": "",
            "max_download_mb": 10,
        },
        "asr": {"mode": "auto", "local_model": "small", "language": "zh"},
        "retention": {
            "cleanup_on_success": True,
            "failed_job_retention_hours": 72,
            "cache_ttl_days": 7,
            "max_cache_gb": 20,
            "keep_source_media": False,
        },
        "vision": {"max_frames": 20, "max_visual_calls": 20, "providers": []},
    }


def test_analyze_removes_successful_job_after_package_validation(monkeypatch, tmp_path, capsys):
    config = runtime_config(tmp_path)
    media = tmp_path / "input.mp4"
    media.write_bytes(b"test-media")
    source = AcquiredSource(
        platform="bilibili",
        input_url="https://www.bilibili.com/video/BV1Ab411c7De",
        canonical_url="https://www.bilibili.com/video/BV1Ab411c7De",
        source_id="BV1Ab411c7De",
        title="测试视频",
        author="tester",
        duration=10,
        published_at="",
        media_path=str(media),
        acquisition_method="test",
    )

    class SuccessfulRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            return source

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", SuccessfulRouter)
    monkeypatch.setattr("scripts.mcu.make_storyboard", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "scripts.mcu.get_transcript",
        lambda *args, **kwargs: TranscriptResult(
            method="test",
            language="zh",
            segments=[TranscriptSegment(start=0, end=1, text="测试内容")],
            source_path="test",
        ),
    )
    args = build_parser().parse_args(["analyze", source.input_url, "--vision", "none"])

    exit_code = analyze(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["job_retained"] is False
    assert payload["job_dir"] is None
    assert list(Path(config["paths"]["temp_root"]).glob("job-*")) == []
    package = Path(payload["package_dir"])
    assert package.is_dir()
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["content"]["summary_html_file"] == "summary.html"
    assert (package / "summary.html").is_file()


def test_analyze_marks_failed_acquisition_for_retention(monkeypatch, tmp_path, capsys):
    config = runtime_config(tmp_path)

    class FailingRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            raise AcquisitionError("NETWORK_ERROR", "temporary", adapter="test")

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", FailingRouter)
    args = build_parser().parse_args(
        ["analyze", "https://www.bilibili.com/video/BV1Ab411c7De", "--vision", "none"]
    )

    exit_code = analyze(args)
    payload = json.loads(capsys.readouterr().out)
    jobs = list(Path(config["paths"]["temp_root"]).glob("job-*"))

    assert exit_code == 2
    assert payload["job_retained"] is True
    assert len(jobs) == 1
    state = json.loads((jobs[0] / JOB_STATE).read_text(encoding="utf-8"))
    assert state["status"] == "failed"


def test_analyze_redacts_acquisition_error_before_persisting_and_printing(
    monkeypatch, tmp_path, capsys
):
    config = runtime_config(tmp_path)

    class FailingRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            raise AcquisitionError(
                "NETWORK_ERROR",
                "request failed https://media.example/image.jpg?signature=SECRET api_key=sk-secret-value",
                adapter="douyin-content",
            )

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", FailingRouter)
    args = build_parser().parse_args(
        ["analyze", "https://www.douyin.com/note/123", "--vision", "none"]
    )

    assert analyze(args) == 2
    output = capsys.readouterr().out
    jobs = list(Path(config["paths"]["temp_root"]).glob("job-*"))
    persisted = (jobs[0] / "errors.json").read_text(encoding="utf-8")

    for text in (output, persisted):
        assert "SECRET" not in text
        assert "sk-secret-value" not in text
        assert "[REDACTED" in text


def test_analyze_writes_native_video_segment_errors_to_final_package(monkeypatch, tmp_path, capsys):
    config = runtime_config(tmp_path)
    media = tmp_path / "input.mp4"
    media.write_bytes(b"test-media")
    source = AcquiredSource(
        platform="bilibili",
        input_url="https://www.bilibili.com/video/BV1Ab411c7De",
        canonical_url="https://www.bilibili.com/video/BV1Ab411c7De",
        source_id="BV1Ab411c7De",
        title="测试视频",
        author="tester",
        duration=10,
        published_at="",
        media_path=str(media),
        acquisition_method="test",
    )

    class SuccessfulRouter:
        def __init__(self, adapters):
            pass

        def acquire(self, url, work_dir):
            return source

    segment_error = {
        "stage": "native-video-transcription",
        "provider": "primary",
        "type": "SERVER_ERROR",
        "fatal": False,
        "message": "temporary",
        "suggestion": "retry",
        "retryable": True,
        "occurred_at": "2026-08-30T00:00:00+00:00",
        "segment_index": 1,
        "time_range": {"start_seconds": 0.0, "end_seconds": 10.0},
    }

    def failed_native_transcription(*args, errors, **kwargs):
        errors.append(segment_error)
        return None

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))
    monkeypatch.setattr("scripts.mcu.default_adapters", lambda current: [])
    monkeypatch.setattr("scripts.mcu.SourceRouter", SuccessfulRouter)
    monkeypatch.setattr("scripts.mcu.get_transcript", lambda *args, **kwargs: None)
    monkeypatch.setattr("scripts.mcu.transcribe_with_video_provider", failed_native_transcription)
    monkeypatch.setattr("scripts.mcu.make_storyboard", lambda *args, **kwargs: [])
    args = build_parser().parse_args(["analyze", source.input_url])

    exit_code = analyze(args)
    payload = json.loads(capsys.readouterr().out)
    final_errors = json.loads(
        (Path(payload["package_dir"]) / "errors.json").read_text(encoding="utf-8")
    )

    assert exit_code == 0
    assert segment_error in final_errors
