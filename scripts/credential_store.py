#!/usr/bin/env python3
"""Resolve provider credentials without exposing them in config or logs."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

HERE = Path(__file__).resolve().parent
KEYCHAIN_HELPER = HERE / "keychain_helper.swift"


class CredentialError(RuntimeError):
    pass


def keychain_identity(provider: Dict[str, Any]) -> Tuple[str, str]:
    service = str(provider.get("api_key_keychain_service") or "")
    account = str(provider.get("api_key_keychain_account") or provider.get("id") or "")
    return service, account


def _run_keychain(
    command: str,
    service: str,
    account: str,
    *,
    secret: Optional[str] = None,
) -> subprocess.CompletedProcess[bytes]:
    if platform.system() != "Darwin" or not KEYCHAIN_HELPER.is_file():
        raise CredentialError("macOS 钥匙串助手不可用")
    try:
        return subprocess.run(
            ["/usr/bin/swift", str(KEYCHAIN_HELPER), command, service, account],
            input=secret.encode("utf-8") if secret is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CredentialError(f"钥匙串操作失败：{exc}") from exc


def get_keychain_secret(service: str, account: str) -> str:
    if platform.system() != "Darwin":
        try:
            import keyring  # type: ignore

            return keyring.get_password(service, account) or ""
        except Exception as exc:
            raise CredentialError(f"系统密钥管理器读取失败：{exc}") from exc
    completed = _run_keychain("get", service, account)
    if completed.returncode == 2:
        return ""
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CredentialError(detail or "无法读取钥匙串")
    return completed.stdout.decode("utf-8").strip()


def set_keychain_secret(service: str, account: str, secret: str) -> None:
    if not secret:
        raise CredentialError("密钥不能为空")
    if platform.system() != "Darwin":
        try:
            import keyring  # type: ignore

            keyring.set_password(service, account, secret)
            return
        except Exception as exc:
            raise CredentialError(f"系统密钥管理器写入失败：{exc}") from exc
    completed = _run_keychain("set", service, account, secret=secret)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CredentialError(detail or "无法写入钥匙串")


def delete_keychain_secret(service: str, account: str) -> bool:
    if platform.system() != "Darwin":
        try:
            import keyring  # type: ignore

            if not keyring.get_password(service, account):
                return False
            keyring.delete_password(service, account)
            return True
        except Exception as exc:
            raise CredentialError(f"系统密钥管理器删除失败：{exc}") from exc
    completed = _run_keychain("delete", service, account)
    if completed.returncode == 2:
        return False
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CredentialError(detail or "无法删除钥匙串项目")
    return True


def resolve_api_key(provider: Dict[str, Any]) -> Tuple[str, str]:
    """Return (secret, source). Environment overrides persistent storage."""
    env_name = str(provider.get("api_key_env") or "")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name], f"environment:{env_name}"
    service, account = keychain_identity(provider)
    if service and account:
        secret = get_keychain_secret(service, account)
        if secret:
            source = "macos-keychain" if platform.system() == "Darwin" else "system-keyring"
            return secret, source
    return "", "missing"
