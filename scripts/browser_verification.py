"""Shared detection and bounded waiting for interactive browser verification."""

from __future__ import annotations

import sys
from typing import Any, Optional

DEFAULT_POLL_SECONDS = 5
DEFAULT_TIMEOUT_SECONDS = 120

CHALLENGE_URL_TOKENS = ("/verify", "captcha", "challenge")
LOGIN_URL_TOKENS = ("/passport/login", "/login")
CHALLENGE_TEXT_TOKENS = (
    "完成验证",
    "安全验证",
    "验证码",
    "拖动滑块",
    "滑块验证",
    "verify to continue",
    "captcha",
    "geetest",
    "slider",
)
LOGIN_TEXT_TOKENS = ("登录后查看", "请先登录", "扫码登录后", "登录后继续")


def _safe_page_text(page: Any) -> str:
    try:
        return str(page.locator("body").inner_text(timeout=1000) or "").lower()
    except Exception:
        return ""


def verification_kind(page: Any) -> Optional[str]:
    """Return ``challenge``/``login`` when the current page needs user action."""
    try:
        url = str(page.url or "").lower()
    except Exception:
        url = ""
    try:
        title = str(page.title() or "").lower()
    except Exception:
        title = ""
    visible_text = _safe_page_text(page)
    if any(token in url for token in CHALLENGE_URL_TOKENS) or any(
        token in title or token in visible_text for token in CHALLENGE_TEXT_TOKENS
    ):
        return "challenge"
    if any(token in url for token in LOGIN_URL_TOKENS) or any(
        token in title or token in visible_text for token in LOGIN_TEXT_TOKENS
    ):
        return "login"
    return None


def wait_for_user_verification(
    page: Any,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> bool:
    """Wait only when verification/login is visible; return false on timeout."""
    kind = verification_kind(page)
    if kind is None:
        return True

    label = "人机验证" if kind == "challenge" else "登录"
    print(
        f"[mcu] 检测到{label}页面，请在浏览器窗口中完成；"
        f"最多等待 {timeout_seconds} 秒。",
        file=sys.stderr,
    )
    elapsed = 0
    while elapsed < timeout_seconds:
        page.wait_for_timeout(poll_seconds * 1000)
        elapsed += poll_seconds
        if verification_kind(page) is None:
            print("[mcu] 验证或登录已完成，继续读取内容。", file=sys.stderr)
            return True
    print(f"[mcu] 等待{label}超时，已停止本次获取。", file=sys.stderr)
    return False
