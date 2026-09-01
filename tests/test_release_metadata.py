import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _normalize_version(raw: str) -> str:
    """Normalize version strings to PEP 440 for comparison.

    Handles: v0.3.0-rc.1 -> 0.3.0rc1, v0.2.2 -> 0.2.2, 0.2.2 -> 0.2.2
    """
    v = raw.strip().lstrip("v")
    # Convert display-style release candidates: 0.3.0-rc.1 -> 0.3.0rc1
    v = re.sub(r"(\d+\.\d+\.\d+)-rc\.(\d+)", r"\1rc\2", v)
    return v


def capture(path, pattern):
    match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert match is not None, f"{path} 中没有找到版本号"
    return match.group(1)


def test_release_versions_are_consistent():
    versions = {
        "pyproject": capture("pyproject.toml", r'^version = "([^"]+)"$'),
        "scripts": capture("scripts/__init__.py", r'^__version__ = "([^"]+)"$'),
        "skill": capture("SKILL.md", r'^  version: "([^"]+)"$'),
        "changelog": capture("CHANGELOG.md", r"^## ([0-9]+\.[0-9]+\.[0-9]+\S*) - "),
    }

    normalized = {k: _normalize_version(v) for k, v in versions.items()}
    unique = set(normalized.values())
    assert len(unique) == 1, (
        f"Version mismatch after normalization: {normalized} (raw: {versions})"
    )
