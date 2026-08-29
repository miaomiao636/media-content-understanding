import json
import sys

import pytest

from scripts.vision_router import (
    MediaInput,
    ProviderResult,
    VisionCallError,
    build_content,
    confidence_from_text,
    main,
    strip_confidence_marker,
)


def _image(tmp_path, name="frame.png", size=64):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def _config(tmp_path, providers, **vision_overrides):
    path = tmp_path / "config.json"
    vision = {
        "providers": providers,
        "max_visual_calls": 20,
        "max_upload_mb": 100,
        "verification_mode": "low-confidence",
        **vision_overrides,
    }
    path.write_text(json.dumps({"vision": vision}), encoding="utf-8")
    return path


def _provider(provider_id, priority):
    return {
        "id": provider_id,
        "enabled": True,
        "priority": priority,
        "adapter": "openai-compatible",
        "model": f"model-{provider_id}",
        "base_url": "https://example.invalid/v1",
        "capabilities": ["image", "multi_image"],
        "max_retries": 0,
        "max_image_base64_mb": 10,
    }


def test_aggregate_upload_limit_applies_across_multiple_images(tmp_path):
    images = [_image(tmp_path, "one.png", 450_000), _image(tmp_path, "two.png", 450_000)]
    media = MediaInput(kind="multi_image", images=images)

    with pytest.raises(VisionCallError) as raised:
        build_content(_provider("primary", 1), "describe", media, max_upload_mb=1)

    assert raised.value.error_type == "INPUT_TOO_LARGE"
    assert "vision.max_upload_mb" in str(raised.value)


def test_confidence_marker_is_structured_and_removed_from_user_output():
    text = "分析结果\n<!-- MCU_CONFIDENCE: Low -->"

    assert confidence_from_text(text) == "low"
    assert strip_confidence_marker(text) == "分析结果"


def test_confidence_marker_must_be_the_final_structured_value():
    text = "引用 <!-- MCU_CONFIDENCE: low -->，但最终没有按协议返回标记。"

    assert confidence_from_text(text) is None


def test_router_budget_caps_retries_and_failover(monkeypatch, tmp_path, capsys):
    providers = [_provider("primary", 1), _provider("backup", 2)]
    providers[0]["max_retries"] = 3
    config = _config(tmp_path, providers)
    image = _image(tmp_path)
    calls = []

    def fail(provider, prompt, media, *, max_upload_mb=None):
        calls.append(provider["id"])
        raise VisionCallError("SERVER_ERROR", "temporary")

    monkeypatch.setattr("scripts.vision_router.call_provider", fail)
    monkeypatch.setattr("scripts.vision_router.resolve_api_key", lambda provider: ("", "none"))
    monkeypatch.setattr("scripts.vision_router.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vision_router.py",
            "--config",
            str(config),
            "--prompt",
            "describe",
            "--image",
            str(image),
            "--max-api-calls",
            "1",
        ],
    )

    assert main() == 21
    payload = json.loads(capsys.readouterr().out)
    assert calls == ["primary"]
    assert payload["status"] == "external_budget_exhausted"
    assert payload["api_calls_used"] == 1
    assert payload["budget_exhausted"] is True


def test_cli_budget_can_shrink_but_not_expand_configured_limit(monkeypatch, tmp_path, capsys):
    config = _config(tmp_path, [_provider("primary", 1)], max_visual_calls=2)
    image = _image(tmp_path)

    def fail(provider, prompt, media, *, max_upload_mb=None):
        raise VisionCallError("SERVER_ERROR", "temporary")

    monkeypatch.setattr("scripts.vision_router.call_provider", fail)
    monkeypatch.setattr("scripts.vision_router.resolve_api_key", lambda provider: ("", "none"))
    monkeypatch.setattr("scripts.vision_router.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vision_router.py",
            "--config",
            str(config),
            "--prompt",
            "describe",
            "--image",
            str(image),
            "--max-api-calls",
            "99",
        ],
    )

    assert main() == 21
    payload = json.loads(capsys.readouterr().out)
    assert payload["api_calls_limit"] == 2


def test_low_confidence_uses_next_provider_for_verification(monkeypatch, tmp_path, capsys):
    providers = [_provider("primary", 1), _provider("backup", 2)]
    config = _config(tmp_path, providers)
    image = _image(tmp_path)
    output = tmp_path / "result.md"
    report = tmp_path / "report.json"
    calls = []

    def succeed(provider, prompt, media, *, max_upload_mb=None):
        calls.append((provider["id"], prompt))
        if provider["id"] == "primary":
            return ProviderResult(
                text="主结果\n<!-- MCU_CONFIDENCE: low -->", model="model-primary", usage=None
            )
        return ProviderResult(
            text="复核后的结果\n<!-- MCU_CONFIDENCE: high -->",
            model="model-backup",
            usage={"total_tokens": 5},
        )

    monkeypatch.setattr("scripts.vision_router.call_provider", succeed)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vision_router.py",
            "--config",
            str(config),
            "--prompt",
            "describe",
            "--image",
            str(image),
            "--output",
            str(output),
            "--report",
            str(report),
            "--max-api-calls",
            "2",
        ],
    )

    assert main() == 0
    capsys.readouterr()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert [provider_id for provider_id, _ in calls] == ["primary", "backup"]
    assert "第二视觉复核模型" in calls[1][1]
    assert payload["verification"]["status"] == "succeeded"
    assert payload["selected_provider"] == "backup"
    assert payload["confidence"] == "high"
    assert payload["api_calls_used"] == 2
    assert output.read_text(encoding="utf-8").strip() == "复核后的结果"
