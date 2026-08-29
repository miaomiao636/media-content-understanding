import json
from pathlib import Path

from scripts.cleanup import (
    JOB_MARKER,
    JOB_STATE,
    ROOT_MARKER,
    clean_cache,
    finish_job,
    write_job_state,
)


def make_config(tmp_path, **retention_overrides):
    retention = {
        "cleanup_on_success": True,
        "failed_job_retention_hours": 72,
        "cache_ttl_days": 7,
        "max_cache_gb": 20,
        "keep_source_media": False,
    }
    retention.update(retention_overrides)
    return {
        "paths": {
            "temp_root": str(tmp_path / "cache"),
            "output_root": str(tmp_path / "output"),
        },
        "retention": retention,
    }


def make_job(config, name="job-1"):
    root = Path(config["paths"]["temp_root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / ROOT_MARKER).write_text("managed\n", encoding="utf-8")
    job = root / name
    job.mkdir()
    (job / JOB_MARKER).write_text("managed\n", encoding="utf-8")
    return job


def test_successful_job_is_removed_when_cleanup_is_enabled(tmp_path):
    config = make_config(tmp_path)
    job = make_job(config)
    (job / "source.mp4").write_bytes(b"video")

    result = finish_job(config, job, success=True, now=100)

    assert result["retained"] is False
    assert result["reason"] == "cleanup_on_success"
    assert not job.exists()


def test_keep_source_media_retains_successful_job_in_managed_cache(tmp_path):
    config = make_config(tmp_path, keep_source_media=True)
    job = make_job(config)
    (job / "source.mp4").write_bytes(b"video")

    result = finish_job(config, job, success=True, now=100)

    assert result["retained"] is True
    assert job.is_dir()
    state = json.loads((job / JOB_STATE).read_text(encoding="utf-8"))
    assert state["status"] == "completed"


def test_successful_acquisition_can_explicitly_retain_its_job(tmp_path):
    config = make_config(tmp_path)
    job = make_job(config)

    result = finish_job(config, job, success=True, retain_success=True, now=100)

    assert result["retained"] is True
    assert result["reason"] == "requested_retention"
    state = json.loads((job / JOB_STATE).read_text(encoding="utf-8"))
    assert state["status"] == "completed"


def test_failed_job_uses_failed_retention_window(tmp_path):
    config = make_config(tmp_path, failed_job_retention_hours=1, cache_ttl_days=30)
    job = make_job(config)
    finish_job(config, job, success=False, now=100)

    before_expiry = clean_cache(config, apply=False, now=3699)
    after_expiry = clean_cache(config, apply=False, now=3701)

    assert before_expiry["candidates"] == []
    assert after_expiry["candidates"] == [str(job.resolve())]
    assert "expired_failed" in after_expiry["candidate_details"][0]["reasons"]


def test_capacity_cleanup_selects_oldest_managed_job_only(tmp_path):
    max_bytes = 1500
    config = make_config(
        tmp_path,
        cleanup_on_success=False,
        cache_ttl_days=365,
        max_cache_gb=max_bytes / (1024**3),
    )
    oldest = make_job(config, "job-oldest")
    (oldest / "payload.bin").write_bytes(b"x" * 800)
    finish_job(config, oldest, success=True, now=100)
    newest = make_job(config, "job-newest")
    (newest / "payload.bin").write_bytes(b"y" * 800)
    finish_job(config, newest, success=True, now=200)
    unmarked = Path(config["paths"]["temp_root"]) / "user-folder"
    unmarked.mkdir()
    (unmarked / "payload.bin").write_bytes(b"z" * 5000)

    preview = clean_cache(config, apply=False, now=300)
    applied = clean_cache(config, apply=True, now=300)

    assert preview["candidates"] == [str(oldest.resolve())]
    assert "capacity" in preview["candidate_details"][0]["reasons"]
    assert applied["applied"] is True
    assert not oldest.exists()
    assert newest.is_dir()
    assert unmarked.is_dir()


def test_capacity_cleanup_does_not_select_a_recent_running_job(tmp_path):
    max_bytes = 1200
    config = make_config(
        tmp_path,
        cleanup_on_success=False,
        cache_ttl_days=365,
        max_cache_gb=max_bytes / (1024**3),
    )
    running = make_job(config, "job-running")
    (running / "payload.bin").write_bytes(b"x" * 800)
    write_job_state(running, "running", now=100)
    completed = make_job(config, "job-completed")
    (completed / "payload.bin").write_bytes(b"y" * 800)
    finish_job(config, completed, success=True, now=200)

    preview = clean_cache(config, apply=False, now=300)

    assert preview["candidates"] == [str(completed.resolve())]
    assert running.is_dir()
