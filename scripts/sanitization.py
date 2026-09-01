"""Shared redaction helpers for persisted and user-visible diagnostics."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEY = (
    r"(?:authorization|set[-_]?cookie|cookie|api[-_]?key|access[-_]?token|"
    r"refresh[-_]?token|password|client[-_]?secret|secret[-_]?key|secret|token)"
)
QUOTED_SECRET_RE = re.compile(
    rf'''(?i)(["']?{SENSITIVE_KEY}["']?\s*[:=]\s*)(["'])(.*?)\2'''
)
HEADER_SECRET_RE = re.compile(
    r'''(?im)\b(authorization|set[-_]?cookie|cookie)(\s*[:=]\s*)(?!["'])[^\r\n,}}]+'''
)
UNQUOTED_SECRET_RE = re.compile(
    rf'''(?i)\b({SENSITIVE_KEY})(\s*[:=]\s*)(?!["'])[^,\s;}}\]]+'''
)


def _redact_quoted(match: re.Match[str]) -> str:
    return f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}"


def sanitize_error_text(value: Any) -> str:
    """Redact common credentials and URL query strings from diagnostic text."""
    cleaned = str(value or "")
    cleaned = QUOTED_SECRET_RE.sub(_redact_quoted, cleaned)
    cleaned = HEADER_SECRET_RE.sub(r"\1\2[REDACTED]", cleaned)
    cleaned = UNQUOTED_SECRET_RE.sub(r"\1\2[REDACTED]", cleaned)
    cleaned = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", cleaned)
    cleaned = re.sub(r"\bsk-[A-Za-z0-9._-]{6,}\b", "[REDACTED]", cleaned)
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED_QUERY]", cleaned)
