#!/usr/bin/env python3
"""Shared configuration helpers for media-content-understanding."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def default_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "media-content-understanding" / "config.json"


def default_temp_root() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "media-content-understanding"


def default_output_root() -> Path:
    return Path.home() / "Documents" / "媒体内容提炼"


DEFAULT_CONFIG: Dict[str, Any] = {
    "paths": {"temp_root": "", "output_root": ""},
    "acquisition": {
        "browser_fallback": True,
        "browser_headless": False,
        "cookie_browser": "",
        "max_download_mb": 2048,
    },
    "asr": {
        "mode": "auto",
        "local_model": "small",
        "language": "zh",
    },
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
        "max_visual_calls": 20,
        "max_frames": 60,
        "max_upload_mb": 100,
        "providers": [],
    },
}


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(explicit: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[Path]]:
    selected = explicit or os.environ.get("MEDIA_CONTENT_CONFIG")
    path = Path(selected).expanduser() if selected else default_config_path()
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("配置根节点必须是 JSON 对象")
        config = _merge(DEFAULT_CONFIG, raw)
    else:
        path = None

    paths = config.setdefault("paths", {})
    paths["temp_root"] = str(Path(paths.get("temp_root") or default_temp_root()).expanduser().resolve())
    paths["output_root"] = str(Path(paths.get("output_root") or default_output_root()).expanduser().resolve())
    providers = config.setdefault("vision", {}).setdefault("providers", [])
    if not isinstance(providers, list):
        raise ValueError("vision.providers 必须是数组")
    return config, path


def provider_value(provider: Dict[str, Any], direct_key: str, env_key: str) -> str:
    if provider.get(direct_key):
        return str(provider[direct_key])
    env_name = str(provider.get(env_key) or "")
    return os.environ.get(env_name, "") if env_name else ""
