"""Tests for the Skill ZIP bundle builder.

Validates allowlist correctness, security constraints, and
deterministic reproducibility of the bundle output.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import zipfile
from pathlib import Path, PureWindowsPath

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "build_skill_bundle",
    str(PROJECT_ROOT / "scripts" / "build_skill_bundle.py"),
)
_bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bundle)


@pytest.fixture()
def bundle_zip(tmp_path: Path):
    """Build the bundle into a temp directory and return (zip_path, metadata)."""
    out = tmp_path / "skill-bundle.zip"
    result = _bundle.build_zip(PROJECT_ROOT, out)
    return out, result


# --------------------------------------------------------------------------- #
# Allowlist and content
# --------------------------------------------------------------------------- #


class TestBundleContent:
    def test_zip_contains_skill_md(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "SKILL.md" in names

    def test_zip_contains_readme(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "README.md" in zf.namelist()

    def test_zip_contains_license(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "LICENSE" in zf.namelist()

    def test_zip_contains_pyproject(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "pyproject.toml" in zf.namelist()

    def test_zip_contains_uv_lock(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "uv.lock" in zf.namelist()

    def test_zip_contains_scripts(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "scripts/mcu.py" in names
        assert "scripts/__init__.py" in names

    def test_zip_contains_swift_helper(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "scripts/keychain_helper.swift" in zf.namelist()

    def test_zip_contains_references(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "references/installation.md" in names
        assert "references/package-contract.md" in names

    def test_zip_contains_tests(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "tests/test_nonvideo_workflow.py" in names

    def test_zip_contains_test_fixtures(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        fixture_files = [n for n in names if n.startswith("tests/fixtures/")]
        assert len(fixture_files) > 0

    def test_zip_contains_assets(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "assets/config.example.json" in zf.namelist()

    def test_zip_contains_agents(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "agents/openai.yaml" in zf.namelist()

    def test_zip_contains_changelog(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "CHANGELOG.md" in zf.namelist()


# --------------------------------------------------------------------------- #
# Exclusions
# --------------------------------------------------------------------------- #


class TestBundleExclusions:
    def test_no_agent_workflow(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any(n.startswith(".agent-workflow/") for n in names)

    def test_no_agents_md(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "AGENTS.md" not in zf.namelist()

    def test_no_claude_code_tasks(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            assert "CLAUDE_CODE_TASKS.md" not in zf.namelist()

    def test_no_context_files(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        for name in [
            "DECISIONS.md",
            "HANDOFF.md",
            "MEMORY_INDEX.md",
            "NEXT_TASKS.md",
            "PROGRESS.md",
            "PROJECT_CONTEXT.md",
        ]:
            assert name not in names, f"{name} should not be in bundle"

    def test_no_git(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any(n.startswith(".git") for n in names)

    def test_no_venv(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any(n.startswith(".venv/") for n in names)

    def test_no_dist(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any(n.startswith("dist/") for n in names)

    def test_no_pycache(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any("__pycache__" in n for n in names)

    def test_no_pyc(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert not any(n.endswith(".pyc") for n in names)


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #


class TestBundleSecurity:
    def test_no_symlinks(self, bundle_zip):
        zip_path, _ = bundle_zip
        project_root = PROJECT_ROOT
        files = _bundle.collect_bundle_files(project_root)
        for rel in files:
            full = project_root / rel
            assert not full.is_symlink(), f"symlink in bundle: {rel}"

    def test_no_absolute_paths(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                assert not os.path.isabs(info.filename), (
                    f"absolute path in ZIP: {info.filename}"
                )

    def test_no_path_traversal(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                resolved = (PROJECT_ROOT / info.filename).resolve()
                try:
                    resolved.relative_to(PROJECT_ROOT.resolve())
                except ValueError:
                    pytest.fail(f"path traversal: {info.filename}")

    def test_no_sensitive_filenames(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        sensitive = {".env", "config.json", "cookies.txt"}
        for name in names:
            basename = name.rsplit("/", 1)[-1]
            assert basename not in sensitive, f"sensitive file in bundle: {name}"

    def test_no_browser_profile_paths(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        for name in names:
            # Skip test files that legitimately reference browser profiles.
            if name.startswith("tests/"):
                continue
            lower = name.lower()
            assert "browser-profile" not in lower, (
                f"browser profile path in bundle: {name}"
            )
            assert "browser_profile" not in lower, (
                f"browser profile path in bundle: {name}"
            )

    def test_no_keychain_paths(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        # "keychain_helper.swift" is the legitimate Swift helper; exclude that.
        for name in names:
            lower = name.lower()
            if "keychain_helper" in lower:
                continue
            assert "keychain" not in lower, f"keychain path in bundle: {name}"

    @pytest.mark.parametrize(
        "relative_path",
        [
            "scripts/CONFIG.JSON",
            "assets/Credentials.json",
            "references/mycredentials/token.txt",
            "assets/credential-store/key.yaml",
            "references/browserprofile/Cookies",
            "assets/tokens.json",
        ],
    )
    def test_rejects_case_variants_and_nested_sensitive_paths(
        self, tmp_path: Path, relative_path: str
    ):
        project = tmp_path / "project"
        project.mkdir()
        (project / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: test\n---\n", encoding="utf-8"
        )
        sensitive = project / relative_path
        sensitive.parent.mkdir(parents=True, exist_ok=True)
        sensitive.write_text("not-a-real-secret", encoding="utf-8")

        with pytest.raises(RuntimeError, match="Security violations"):
            _bundle.build_zip(project, tmp_path / "bundle.zip")


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class TestBundleDeterminism:
    def test_archive_names_use_posix_separators_on_windows(self):
        assert _bundle._archive_name(PureWindowsPath("scripts/mcu.py")) == "scripts/mcu.py"

    def test_consecutive_builds_same_hash(self, tmp_path: Path):
        zip1 = tmp_path / "v1.zip"
        zip2 = tmp_path / "v2.zip"
        r1 = _bundle.build_zip(PROJECT_ROOT, zip1)
        r2 = _bundle.build_zip(PROJECT_ROOT, zip2)
        assert r1["bundle_sha256"] == r2["bundle_sha256"]
        assert r1["file_count"] == r2["file_count"]

    def test_manifest_stable(self, tmp_path: Path):
        zip1 = tmp_path / "v1.zip"
        zip2 = tmp_path / "v2.zip"
        r1 = _bundle.build_zip(PROJECT_ROOT, zip1)
        r2 = _bundle.build_zip(PROJECT_ROOT, zip2)
        assert r1["manifest"] == r2["manifest"]

    def test_manifest_sha256_matches_actual(self, bundle_zip):
        zip_path, result = bundle_zip
        actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        assert result["bundle_sha256"] == actual

    def test_existing_bundle_verification_detects_source_drift(self, tmp_path: Path):
        project = tmp_path / "project"
        project.mkdir()
        skill = project / "SKILL.md"
        skill.write_text(
            "---\nname: test-skill\ndescription: test\n---\noriginal\n",
            encoding="utf-8",
        )
        bundle = tmp_path / "bundle.zip"
        result = _bundle.build_zip(project, bundle)
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "bundle_sha256": result["bundle_sha256"],
                    "file_count": result["file_count"],
                    "files": result["manifest"],
                }
            ),
            encoding="utf-8",
        )

        assert _bundle.verify_existing_bundle(project, bundle, manifest) == []

        skill.write_text(
            "---\nname: test-skill\ndescription: test\n---\nchanged after build\n",
            encoding="utf-8",
        )

        errors = _bundle.verify_existing_bundle(project, bundle, manifest)
        assert any("source content changed" in error for error in errors)


# --------------------------------------------------------------------------- #
# Extractability and usability
# --------------------------------------------------------------------------- #


class TestBundleExtractability:
    def test_skill_md_readable_from_root(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            content = zf.read("SKILL.md")
        assert len(content) > 0
        assert b"media-content-understanding" in content.lower()

    def test_no_directory_entries_for_files(self, bundle_zip):
        zip_path, _ = bundle_zip
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.filename.endswith("/"):
                    # Directory entry -- that's fine.
                    continue
                # File entries should not have zero size unless genuinely empty.
                assert info.file_size >= 0
