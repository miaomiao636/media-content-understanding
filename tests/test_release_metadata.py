import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def capture(path, pattern):
    match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert match is not None, f"{path} 中没有找到版本号"
    return match.group(1)


def test_release_versions_are_consistent():
    versions = {
        "pyproject": capture("pyproject.toml", r'^version = "([^"]+)"$'),
        "scripts": capture("scripts/__init__.py", r'^__version__ = "([^"]+)"$'),
        "skill": capture("SKILL.md", r'^  version: "([^"]+)"$'),
        "changelog": capture("CHANGELOG.md", r"^## ([0-9]+\.[0-9]+\.[0-9]+) - "),
    }

    assert len(set(versions.values())) == 1, versions
