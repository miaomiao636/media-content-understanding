import subprocess

from scripts.mcu import VisualCallBudget, slug, vision_router_command, visual_summary


def test_slug_removes_unsafe_filename_characters():
    assert slug('A/B:C*D?E"F<G>H|I') == "A_B_C_D_E_F_G_H_I"


def test_slug_has_fallback():
    assert slug(" ... ") == "未命名视频"


def test_visual_call_budget_consumes_child_report_and_caps_remaining(tmp_path):
    budget = VisualCallBudget(limit=5)

    budget.consume_report({"api_calls_used": 2})

    assert budget.snapshot() == {"limit": 5, "used": 2, "remaining": 3}
    command = vision_router_command(tmp_path / "custom.json", budget)
    assert command[-4:] == ["--config", str(tmp_path / "custom.json"), "--max-api-calls", "3"]
    limited = vision_router_command(tmp_path / "custom.json", budget, max_calls=1)
    assert limited[-1] == "1"


def test_visual_call_budget_exhausts_when_child_usage_is_unknown():
    budget = VisualCallBudget(limit=5)

    budget.consume_report({"status": "external_success"})

    assert budget.snapshot() == {"limit": 5, "used": 5, "remaining": 0}


def test_visual_summary_timeout_returns_partial_failure_report(monkeypatch, tmp_path):
    package_dir = tmp_path / "package"
    job_dir = tmp_path / "job"
    package_dir.mkdir()
    job_dir.mkdir()
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    budget = VisualCallBudget(limit=3)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("vision-router", 600)

    monkeypatch.setattr("scripts.mcu.subprocess.run", timeout)

    report = visual_summary(
        package_dir,
        job_dir,
        None,
        [frame],
        "",
        budget=budget,
        config_path=None,
    )

    assert report is not None
    assert report["status"] == "external_router_failed"
    assert report["errors"][0]["type"] == "VISION_ROUTER_FAILED"
    assert report["errors"][0]["fatal"] is False
    assert report["workflow_budget"] == {"limit": 3, "used": 3, "remaining": 0}
