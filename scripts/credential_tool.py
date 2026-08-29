#!/usr/bin/env python3
"""Store and inspect provider API keys without printing secret values."""

from __future__ import annotations

import argparse
import getpass
import json
import platform
import subprocess
from typing import Any, Dict, List

from config_loader import load_config
from credential_store import (
    CredentialError,
    delete_keychain_secret,
    get_keychain_secret,
    keychain_identity,
    resolve_api_key,
    set_keychain_secret,
)


def providers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in config.get("vision", {}).get("providers", []) if isinstance(item, dict)]


def select_provider(config: Dict[str, Any], provider_id: str) -> Dict[str, Any]:
    for provider in providers(config):
        if str(provider.get("id")) == provider_id:
            return provider
    raise CredentialError(f"找不到 provider：{provider_id}")


def gui_prompt(provider_id: str) -> str:
    if platform.system() != "Darwin":
        raise CredentialError("--gui 仅支持 macOS")
    script = (
        'set dlg to display dialog "请输入视觉模型 '
        + provider_id.replace('"', "")
        + ' 的 API Key。密钥不会显示，也不会写入聊天或配置文件。" '
        'default answer "" with hidden answer buttons {"取消", "保存到钥匙串"} '
        'default button "保存到钥匙串" cancel button "取消" '
        'with title "Skill 1：保存视觉模型密钥"\ntext returned of dlg'
    )
    completed = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise CredentialError("用户取消或无法打开安全输入窗口")
    return completed.stdout.rstrip("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    sub = parser.add_subparsers(dest="command", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--provider")
    set_parser = sub.add_parser("set")
    set_parser.add_argument("--provider", required=True)
    set_parser.add_argument("--gui", action="store_true")
    delete_parser = sub.add_parser("delete")
    delete_parser.add_argument("--provider", required=True)
    delete_parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    try:
        config, _ = load_config(args.config)
        if args.command == "status":
            selected = providers(config)
            if args.provider:
                selected = [select_provider(config, args.provider)]
            rows = []
            for provider in selected:
                _, source = resolve_api_key(provider)
                rows.append({"id": provider.get("id"), "present": source != "missing", "source": source})
            print(json.dumps({"providers": rows}, ensure_ascii=False, indent=2))
            return 0

        provider = select_provider(config, args.provider)
        service, account = keychain_identity(provider)
        if not service or not account:
            raise CredentialError("provider 缺少持久密钥 service/account")

        if args.command == "set":
            secret = gui_prompt(args.provider) if args.gui else getpass.getpass("API Key（不会回显）：")
            set_keychain_secret(service, account, secret)
            secret = ""
            stored = bool(get_keychain_secret(service, account))
            print(
                json.dumps(
                    {
                        "id": args.provider,
                        "stored": stored,
                        "source": "macos-keychain" if platform.system() == "Darwin" else "system-keyring",
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if stored else 1

        if not args.yes:
            raise CredentialError("删除钥匙串密钥必须显式提供 --yes")
        deleted = delete_keychain_secret(service, account)
        print(json.dumps({"id": args.provider, "deleted": deleted}, ensure_ascii=False))
        return 0
    except (CredentialError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
