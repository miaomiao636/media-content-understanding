#!/usr/bin/env python3
"""Shared configuration helpers for media-content-understanding."""

from __future__ import annotations

import json
import os
import platform
from copy import deepcopy
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
        "browser_profile_dir": "",
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


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _paths_overlap(first: Path, second: Path) -> bool:
    return _path_contains(first, second) or _path_contains(second, first)


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _require_section(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    value = config.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是 JSON 对象")
    return value


def _number(config: Dict[str, Any], section: str, key: str) -> float:
    try:
        return float(config[section][key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{section}.{key} 必须是数字") from exc


def validate_config(config: Dict[str, Any]) -> None:
    if _number(config, "acquisition", "max_download_mb") <= 0:
        raise ValueError("acquisition.max_download_mb 必须大于 0")
    mode = str(config.get("asr", {}).get("mode", "auto"))
    if mode not in {"auto", "local", "none"}:
        raise ValueError(f"asr.mode 无效：{mode}")
    for key in ("failed_job_retention_hours", "cache_ttl_days"):
        if _number(config, "retention", key) < 0:
            raise ValueError(f"retention.{key} 不能为负数")
    if _number(config, "retention", "max_cache_gb") <= 0:
        raise ValueError("retention.max_cache_gb 必须大于 0")
    for key in ("max_visual_calls", "max_frames", "max_upload_mb"):
        if _number(config, "vision", key) <= 0:
            raise ValueError(f"vision.{key} 必须大于 0")
    for key in ("max_visual_calls", "max_frames"):
        value = _number(config, "vision", key)
        if not value.is_integer():
            raise ValueError(f"vision.{key} 必须是整数")
    verification_mode = str(config["vision"].get("verification_mode", "low-confidence"))
    if verification_mode not in {"none", "low-confidence"}:
        raise ValueError(f"vision.verification_mode 无效：{verification_mode}")


def load_config(explicit: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[Path]]:
    selected = explicit or os.environ.get("MEDIA_CONTENT_CONFIG")
    path = Path(selected).expanduser() if selected else default_config_path()
    config = deepcopy(DEFAULT_CONFIG)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("配置根节点必须是 JSON 对象")
        config = _merge(deepcopy(DEFAULT_CONFIG), raw)
    else:
        path = None

    for section in ("paths", "acquisition", "asr", "retention", "vision"):
        _require_section(config, section)

    paths = config["paths"]
    paths["temp_root"] = str(Path(paths.get("temp_root") or default_temp_root()).expanduser().resolve())
    paths["output_root"] = str(Path(paths.get("output_root") or default_output_root()).expanduser().resolve())
    acquisition = config["acquisition"]
    raw_profile = acquisition.get("browser_profile_dir", "")
    if not isinstance(raw_profile, str):
        raise ValueError("acquisition.browser_profile_dir 必须是路径字符串")
    if raw_profile.strip():
        profile = Path(raw_profile).expanduser().resolve()
        temp_root = Path(paths["temp_root"])
        output_root = Path(paths["output_root"])
        home = Path.home().resolve()
        if profile in {Path(profile.anchor), home}:
            raise ValueError("acquisition.browser_profile_dir 不能是根目录或用户主目录")
        if _paths_overlap(profile, temp_root) or _paths_overlap(profile, output_root):
            raise ValueError("acquisition.browser_profile_dir 必须与 temp_root、output_root 完全分离")
        acquisition["browser_profile_dir"] = str(profile)
    else:
        acquisition["browser_profile_dir"] = ""
    providers = config["vision"].setdefault("providers", [])
    if not isinstance(providers, list):
        raise ValueError("vision.providers 必须是数组")
    validate_config(config)
    return config, path


def provider_value(provider: Dict[str, Any], direct_key: str, env_key: str) -> str:
    if provider.get(direct_key):
        return str(provider[direct_key])
    env_name = str(provider.get(env_key) or "")
    return os.environ.get(env_name, "") if env_name else ""
