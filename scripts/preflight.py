#!/usr/bin/env python3
"""Read-only environment and configuration preflight."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

from config_loader import load_config, provider_value
from console import configure_utf8_stdio
from credential_store import CredentialError, resolve_api_key

configure_utf8_stdio()

SUPPORTED_PROFILES = {"standard", "qwen-omni", "xiaomi-mimo"}


def writable_parent(path: Path) -> bool:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current.exists() and os.access(str(current), os.W_OK)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.add_argument(
        "--content-type",
        choices=["unknown", "video", "gallery", "long_text", "mixed"],
        default="unknown",
    )
    parser.add_argument("--host-vision", choices=["yes", "no", "unknown"], default="unknown")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks = []
    warnings = []
    errors = []
    try:
        config, config_path = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "errors": [{"type": "CONFIGURATION_ERROR", "message": str(exc)}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    for name in ("ffmpeg", "ffprobe", "yt-dlp", "ego-browser"):
        found = shutil.which(name)
        checks.append({"name": name, "available": bool(found), "path": found})

    if args.content_type in {"video", "mixed"} and not shutil.which("ffmpeg"):
        errors.append({"type": "MISSING_DEPENDENCY", "message": "视频任务缺少 ffmpeg"})
    if not shutil.which("yt-dlp") and not shutil.which("ego-browser"):
        warnings.append("未发现 yt-dlp 或 Ego Browser；需要宿主提供等价来源访问能力")

    temp_root = Path(config["paths"]["temp_root"])
    output_root = Path(config["paths"]["output_root"])
    home = Path.home().resolve()
    if temp_root in {Path(temp_root.anchor), home}:
        errors.append({"type": "UNSAFE_PATH", "message": f"temp_root 不能是根目录或用户主目录：{temp_root}"})
    if temp_root == output_root:
        errors.append({"type": "UNSAFE_PATH", "message": "temp_root 与 output_root 不能相同"})
    for label, path in (("temp_root", temp_root), ("output_root", output_root)):
        if not writable_parent(path):
            errors.append({"type": "PATH_NOT_WRITABLE", "message": f"{label} 的现有父目录不可写：{path}"})

    provider_rows = []
    for index, provider in enumerate(config["vision"].get("providers", [])):
        if not isinstance(provider, dict) or not provider.get("enabled", False):
            continue
        provider_id = str(provider.get("id") or f"provider-{index + 1}")
        problems = []
        credential_source = "missing"
        if provider.get("adapter") != "openai-compatible":
            problems.append("adapter 当前必须是 openai-compatible")
        profile = str(provider.get("request_profile") or "standard")
        if profile not in SUPPORTED_PROFILES:
            problems.append(f"不支持 request_profile：{profile}")
        if not provider.get("model"):
            problems.append("缺少 model")
        if not provider_value(provider, "base_url", "base_url_env"):
            problems.append("Base URL 未配置或环境变量为空")
        try:
            api_key, credential_source = resolve_api_key(provider)
        except CredentialError as exc:
            api_key = ""
            problems.append(f"钥匙串读取失败：{exc}")
        if not api_key:
            problems.append("API Key 未在环境变量或钥匙串中设置")
        api_key = ""
        provider_rows.append(
            {
                "id": provider_id,
                "priority": provider.get("priority", 100),
                "request_profile": profile,
                "credential_source": credential_source,
                "available": not problems,
                "problems": problems,
            }
        )

    if not provider_rows:
        warnings.append("没有启用外部视觉模型；需要依赖宿主视觉或为视觉非必要内容降级")
    elif not any(row["available"] for row in provider_rows):
        warnings.append("所有已启用外部视觉模型都存在静态配置问题")

    if (
        args.host_vision == "no"
        and args.content_type in {"gallery", "mixed"}
        and not any(row["available"] for row in provider_rows)
    ):
        errors.append(
            {"type": "NO_VISUAL_CAPABILITY", "message": "内容需要视觉，但外部模型和宿主视觉均不可用"}
        )

    result = {
        "ok": not errors,
        "config_path": str(config_path) if config_path else None,
        "paths": {"temp_root": str(temp_root), "output_root": str(output_root)},
        "tools": checks,
        "vision_providers": sorted(provider_rows, key=lambda row: row["priority"]),
        "host_vision": args.host_vision,
        "warnings": warnings,
        "errors": errors,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("OK" if result["ok"] else "FAILED")
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR [{error['type']}]: {error['message']}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
