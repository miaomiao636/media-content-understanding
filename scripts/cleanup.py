#!/usr/bin/env python3
"""Safely manage only marked cache directories owned by this skill."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from config_loader import load_config
from console import configure_utf8_stdio

configure_utf8_stdio()

ROOT_MARKER = ".media-content-understanding-managed"
JOB_MARKER = ".job-managed"


def validate_root(root: Path, output_root: Path) -> None:
    resolved = root.resolve()
    home = Path.home().resolve()
    if resolved in {Path(resolved.anchor), home, output_root.resolve()}:
        raise ValueError(f"拒绝使用不安全的缓存根目录：{resolved}")


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
        root = Path(config["paths"]["temp_root"]).resolve()
        output_root = Path(config["paths"]["output_root"]).resolve()
        validate_root(root, output_root)
        if args.command == "init-root":
            root.mkdir(parents=True, exist_ok=True)
            (root / ROOT_MARKER).write_text("managed cache root\n", encoding="utf-8")
            print(root)
            return 0
        if not (root / ROOT_MARKER).is_file():
            raise ValueError(f"缓存根目录缺少管理标记：{root / ROOT_MARKER}")
        if args.command == "register":
            job = Path(args.job_dir).expanduser().resolve()
            try:
                job.relative_to(root)
            except ValueError:
                raise ValueError("任务目录必须位于 temp_root 内")
            if job == root:
                raise ValueError("任务目录不能等于 temp_root")
            job.mkdir(parents=True, exist_ok=True)
            (job / JOB_MARKER).write_text(str(time.time()) + "\n", encoding="utf-8")
            print(job)
            return 0

        if args.apply and args.dry_run:
            raise ValueError("--apply 与 --dry-run 不能同时使用")
        hours = args.older_than_hours
        if hours is None:
            hours = float(config["retention"].get("cache_ttl_days", 7)) * 24
        if hours < 0:
            raise ValueError("older-than-hours 不能为负数")
        cutoff = time.time() - hours * 3600
        candidates = []
        for child in root.iterdir():
            if not child.is_dir() or not (child / JOB_MARKER).is_file():
                continue
            if child.stat().st_mtime <= cutoff:
                candidates.append(child)
        if args.apply:
            for child in candidates:
                shutil.rmtree(child)
        print(
            json.dumps(
                {"applied": bool(args.apply), "candidates": [str(path) for path in candidates]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
