#!/usr/bin/env python3
"""Keep Chinese CLI output readable on Windows and redirected terminals."""

from __future__ import annotations

import sys
from typing import Any


def configure_utf8_stdio() -> None:
    """Use UTF-8 where Python exposes a reconfigurable text stream."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure: Any = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (LookupError, OSError, ValueError):
            continue
