#!/usr/bin/env python3
"""Safely manage only marked cache directories owned by this skill."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

try:
    from .config_loader import load_config
    from .console import configure_utf8_stdio
except ImportError:
    from config_loader import load_config
    from console import configure_utf8_stdio

configure_utf8_stdio()

ROOT_MARKER = ".media-content-understanding-managed"
JOB_MARKER = ".job-managed"
JOB_STATE = ".job-state.json"


def validate_root(root: Path, output_root: Path) -> None:
    resolved = root.resolve()
    resolved_output = output_root.resolve()
    home = Path.home().resolve()
    if resolved in {Path(resolved.anchor), home, resolved_output}:
        raise ValueError(f"拒绝使用不安全的缓存根目录：{resolved}")
    if resolved in resolved_output.parents or resolved_output in resolved.parents:
        raise ValueError("temp_root 与 output_root 不能互相包含")


def _roots(config: Dict[str, Any]) -> tuple[Path, Path]:
    root = Path(config["paths"]["temp_root"]).expanduser().resolve()
    output_root = Path(config["paths"]["output_root"]).expanduser().resolve()
    validate_root(root, output_root)
    return root, output_root


def ensure_managed_root(config: Dict[str, Any]) -> Path:
    root, _ = _roots(config)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ROOT_MARKER
    if not marker.exists():
        marker.write_text("managed cache root\n", encoding="utf-8")
    return root


def _require_managed_root(root: Path) -> None:
    if not (root / ROOT_MARKER).is_file():
        raise ValueError(f"缓存根目录缺少管理标记：{root / ROOT_MARKER}")


def _require_managed_job(root: Path, job: Path) -> Path:
    resolved = job.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("任务目录必须位于 temp_root 内") from exc
    if resolved == root:
        raise ValueError("任务目录不能等于 temp_root")
    if resolved.is_symlink() or not (resolved / JOB_MARKER).is_file():
        raise ValueError(f"任务目录缺少管理标记：{resolved}")
    return resolved


def write_job_state(job: Path, status: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    if status not in {"running", "completed", "failed"}:
        raise ValueError(f"无效任务状态：{status}")
    timestamp = time.time() if now is None else float(now)
    state_path = job / JOB_STATE
    previous: Dict[str, Any] = {}
    if state_path.is_file():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                previous = payload
        except (OSError, json.JSONDecodeError):
            previous = {}
    state = {
        "status": status,
        "created_at": float(previous.get("created_at", timestamp)),
        "updated_at": timestamp,
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def register_job(config: Dict[str, Any], job: Path, *, now: Optional[float] = None) -> Path:
    root = ensure_managed_root(config)
    resolved = job.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("任务目录必须位于 temp_root 内") from exc
    if resolved == root:
        raise ValueError("任务目录不能等于 temp_root")
    resolved.mkdir(parents=True, exist_ok=True)
    (resolved / JOB_MARKER).write_text(str(time.time() if now is None else now) + "\n", encoding="utf-8")
    write_job_state(resolved, "running", now=now)
    return resolved


def _read_job_state(job: Path) -> Dict[str, Any]:
    state_path = job / JOB_STATE
    if state_path.is_file():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
    return {"status": "unknown", "updated_at": job.stat().st_mtime}


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink() or not item.is_file():
            continue
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def _managed_jobs(root: Path, protected: Set[Path]) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for child in root.iterdir():
        resolved = child.resolve()
        if resolved in protected or child.is_symlink() or not child.is_dir():
            continue
        if not (child / JOB_MARKER).is_file():
            continue
        state = _read_job_state(child)
        try:
            updated_at = float(state.get("updated_at", child.stat().st_mtime))
        except (TypeError, ValueError):
            updated_at = child.stat().st_mtime
        jobs.append(
            {
                "path": resolved,
                "status": str(state.get("status") or "unknown"),
                "updated_at": updated_at,
                "size_bytes": _directory_size(child),
            }
        )
    return jobs


def clean_cache(
    config: Dict[str, Any],
    *,
    apply: bool = False,
    older_than_hours: Optional[float] = None,
    now: Optional[float] = None,
    protect: Iterable[Path] = (),
) -> Dict[str, Any]:
    root, _ = _roots(config)
    _require_managed_root(root)
    current_time = time.time() if now is None else float(now)
    retention = config.get("retention", {})
    if not isinstance(retention, dict):
        raise ValueError("retention 必须是对象")
    failed_hours = float(retention.get("failed_job_retention_hours", 72))
    ttl_hours = float(retention.get("cache_ttl_days", 7)) * 24
    max_cache_gb = float(retention.get("max_cache_gb", 20))
    if older_than_hours is not None and older_than_hours < 0:
        raise ValueError("older-than-hours 不能为负数")
    if failed_hours < 0 or ttl_hours < 0 or max_cache_gb <= 0:
        raise ValueError("缓存保留时间不能为负数，max_cache_gb 必须大于 0")
    protected = {Path(path).expanduser().resolve() for path in protect}
    jobs = _managed_jobs(root, protected)
    selected: Dict[Path, Dict[str, Any]] = {}
    for job in jobs:
        if older_than_hours is not None:
            threshold_hours = float(older_than_hours)
            reason = "expired_override"
        elif job["status"] == "failed":
            threshold_hours = failed_hours
            reason = "expired_failed"
        else:
            threshold_hours = ttl_hours
            reason = "expired_cache"
        if current_time - job["updated_at"] >= threshold_hours * 3600:
            selected[job["path"]] = {**job, "reasons": [reason]}

    total_before = sum(int(job["size_bytes"]) for job in jobs)
    max_bytes = int(max_cache_gb * 1024**3)
    projected = total_before - sum(int(item["size_bytes"]) for item in selected.values())
    if projected > max_bytes:
        remaining = sorted(
            (job for job in jobs if job["path"] not in selected and job["status"] != "running"),
            key=lambda item: (item["updated_at"], str(item["path"])),
        )
        for job in remaining:
            selected[job["path"]] = {**job, "reasons": ["capacity"]}
            projected -= int(job["size_bytes"])
            if projected <= max_bytes:
                break

    details = sorted(selected.values(), key=lambda item: (item["updated_at"], str(item["path"])))
    if apply:
        for item in details:
            managed = _require_managed_job(root, item["path"])
            shutil.rmtree(managed)
    return {
        "applied": bool(apply),
        "candidates": [str(item["path"]) for item in details],
        "candidate_details": [
            {
                "path": str(item["path"]),
                "status": item["status"],
                "size_bytes": item["size_bytes"],
                "reasons": item["reasons"],
            }
            for item in details
        ],
        "total_bytes_before": total_before,
        "total_bytes_after": max(0, projected),
        "max_cache_bytes": max_bytes,
    }


def finish_job(
    config: Dict[str, Any],
    job: Path,
    *,
    success: bool,
    retain_success: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    root, _ = _roots(config)
    _require_managed_root(root)
    managed = _require_managed_job(root, job)
    write_job_state(managed, "completed" if success else "failed", now=now)
    retention = config.get("retention", {})
    cleanup_on_success = bool(retention.get("cleanup_on_success", True))
    keep_source_media = bool(retention.get("keep_source_media", False))
    if success and cleanup_on_success and not keep_source_media and not retain_success:
        shutil.rmtree(managed)
        return {"retained": False, "reason": "cleanup_on_success", "path": str(managed)}
    if not success:
        reason = "failed_retention"
    elif retain_success:
        reason = "requested_retention"
    elif keep_source_media:
        reason = "keep_source_media"
    else:
        reason = "cleanup_disabled"
    return {"retained": True, "reason": reason, "path": str(managed)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-root")
    register = sub.add_parser("register")
    register.add_argument("job_dir")
    clean = sub.add_parser("clean")
    clean.add_argument("--dry-run", action="store_true")
    clean.add_argument("--apply", action="store_true")
    clean.add_argument("--older-than-hours", type=float)
    args = parser.parse_args()

    try:
        config, _ = load_config(args.config)
        if args.command == "init-root":
            print(ensure_managed_root(config))
            return 0
        if args.command == "register":
            print(register_job(config, Path(args.job_dir)))
            return 0

        if args.apply and args.dry_run:
            raise ValueError("--apply 与 --dry-run 不能同时使用")
        result = clean_cache(
            config,
            apply=bool(args.apply),
            older_than_hours=args.older_than_hours,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
