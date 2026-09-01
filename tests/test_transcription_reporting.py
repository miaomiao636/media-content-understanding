import json
import subprocess
from pathlib import Path

import pytest

from scripts.mcu import VisualCallBudget, transcribe_with_video_provider

REQUIRED_ERROR_FIELDS = {
    "stage",
    "provider",
    "type",
    "fatal",
    "message",
    "suggestion",
    "retryable",
    "occurred_at",
    "segment_index",
    "time_range",
}


def _router_report(*, status, calls, attempted, selected=None, errors=None, exhausted=False):
    return {
        "status": status,
        "attempted_providers": attempted,
        "selected_provider": selected,
        "api_calls_used": calls,
        "budget_exhausted": exhausted,
        "errors": errors or [],
    }


def _run_segmented_transcription(monkeypatch, tmp_path, reports, *, duration, budget_limit=10):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")
    errors = []
    router_calls = 0

    monkeypatch.setattr("scripts.mcu.shutil.which", lambda name: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        nonlocal router_calls
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"segment")
            return subprocess.CompletedProcess(command, 0, "", "")

        report_path = Path(command[command.index("--report") + 1])
        output_path = Path(command[command.index("--output") + 1])
        report_payload = reports[router_calls]
        router_calls += 1
        output_path.write_text(f"segment {router_calls}", encoding="utf-8")
        if report_payload == "corrupt":
            report_path.write_text("{not-json", encoding="utf-8")
        elif report_payload is not None:
            report_path.write_text(json.dumps(report_payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.mcu.subprocess.run", fake_run)
    budget = VisualCallBudget(budget_limit)
    transcript = transcribe_with_video_provider(
        media,
        tmp_path / "job",
        duration=duration,
        budget=budget,
        config_path=None,
        errors=errors,
    )
    return transcript, errors, budget, router_calls


def test_primary_failures_retry_then_backup_success_are_ordered_and_redacted(
    monkeypatch, tmp_path
):
    report = _router_report(
        status="external_success",
        calls=3,
        attempted=["primary", "backup"],
        selected="backup",
        errors=[
            {
                "stage": "visual_analysis",
                "provider": "primary",
                "type": "SERVER_ERROR",
                "message": "api_key=sk-secret-value https://api.example/v1?token=secret",
                "suggestion": "retry Authorization: Bearer hidden-token",
                "retryable": True,
                "attempt": 1,
                "occurred_at": "2026-08-30T00:00:00+00:00",
            },
            {
                "stage": "visual_analysis",
                "provider": "primary",
                "type": "TIMEOUT",
                "message": "second failure",
                "suggestion": "switch provider",
                "retryable": True,
                "attempt": 2,
                "occurred_at": "2026-08-30T00:00:01+00:00",
            },
        ],
    )

    transcript, errors, budget, _ = _run_segmented_transcription(
        monkeypatch, tmp_path, [report], duration=30
    )

    assert transcript is not None
    assert budget.used == 3
    assert [item["type"] for item in errors] == [
        "SERVER_ERROR",
        "PROVIDER_RETRY",
        "TIMEOUT",
        "PROVIDER_SWITCHED",
    ]
    assert all(REQUIRED_ERROR_FIELDS <= item.keys() for item in errors)
    assert all(item["segment_index"] == 1 for item in errors)
    assert all(item["time_range"] == {"start_seconds": 0.0, "end_seconds": 30.0} for item in errors)
    serialized = json.dumps(errors, ensure_ascii=False)
    assert "sk-secret-value" not in serialized
    assert "hidden-token" not in serialized
    assert "token=secret" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.parametrize(
    ("report_payload", "expected_type"),
    [(None, "VISION_REPORT_MISSING"), ("corrupt", "VISION_REPORT_INVALID")],
)
def test_missing_or_corrupt_report_is_recorded_and_conservatively_exhausts_budget(
    monkeypatch, tmp_path, report_payload, expected_type
):
    transcript, errors, budget, router_calls = _run_segmented_transcription(
        monkeypatch,
        tmp_path,
        [report_payload],
        duration=30,
        budget_limit=4,
    )

    assert transcript is None
    assert router_calls == 1
    assert budget.snapshot() == {"limit": 4, "used": 4, "remaining": 0}
    assert [item["type"] for item in errors] == [expected_type]
    assert REQUIRED_ERROR_FIELDS <= errors[0].keys()
    assert errors[0]["segment_index"] == 1


@pytest.mark.parametrize("invalid_usage", [True, "2", 2.5, None, -1])
def test_untrusted_api_call_count_types_conservatively_exhaust_budget(
    monkeypatch, tmp_path, invalid_usage
):
    report = _router_report(
        status="external_success",
        calls=invalid_usage,
        attempted=["primary"],
        selected="primary",
    )

    transcript, errors, budget, router_calls = _run_segmented_transcription(
        monkeypatch, tmp_path, [report], duration=30, budget_limit=5
    )

    assert transcript is None
    assert router_calls == 1
    assert budget.snapshot() == {"limit": 5, "used": 5, "remaining": 0}
    assert [item["type"] for item in errors] == ["VISION_REPORT_INVALID"]


def test_multiple_segments_keep_partial_success_and_count_each_report_once(monkeypatch, tmp_path):
    reports = [
        _router_report(
            status="external_success",
            calls=1,
            attempted=["primary"],
            selected="primary",
        ),
        _router_report(
            status="external_exhausted",
            calls=2,
            attempted=["primary"],
            errors=[
                {
                    "provider": "primary",
                    "type": "SERVER_ERROR",
                    "message": "first failure",
                    "suggestion": "retry",
                    "retryable": True,
                    "attempt": 1,
                },
                {
                    "provider": "primary",
                    "type": "SERVER_ERROR",
                    "message": "second failure",
                    "suggestion": "switch",
                    "retryable": True,
                    "attempt": 2,
                },
            ],
        ),
        _router_report(
            status="external_success",
            calls=1,
            attempted=["backup"],
            selected="backup",
        ),
    ]

    transcript, errors, budget, router_calls = _run_segmented_transcription(
        monkeypatch, tmp_path, reports, duration=370, budget_limit=10
    )

    assert transcript is not None
    assert [(item.start, item.end) for item in transcript.segments] == [(0.0, 180.0), (360.0, 370.0)]
    assert router_calls == 3
    assert budget.snapshot() == {"limit": 10, "used": 4, "remaining": 6}
    assert [item["type"] for item in errors] == [
        "SERVER_ERROR",
        "PROVIDER_RETRY",
        "SERVER_ERROR",
    ]
    assert all(item["segment_index"] == 2 for item in errors)
    assert all(
        item["time_range"] == {"start_seconds": 180.0, "end_seconds": 360.0}
        for item in errors
    )
