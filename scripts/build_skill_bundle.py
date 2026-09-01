"""Deterministic Skill ZIP bundle builder for media-content-understanding.

Builds a distributable ZIP using an explicit allowlist.
Never packages secrets, caches, user config, or agent workflow state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePath
from typing import Optional

# Root-relative paths that are always included (files).
ALWAYS_INCLUDE_FILES: list[str] = [
    "SKILL.md",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "uv.lock",
]

# Root-relative directories whose non-hidden, non-cache contents are included.
INCLUDE_DIRS: list[str] = [
    "scripts",
    "tests",
    "references",
    "assets",
    "agents",
]

# Patterns to skip inside included directories.
SKIP_NAMES: set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".DS_Store",
}

SKIP_SUFFIXES: set[str] = {
    ".pyc",
    ".pyo",
}

# Sensitive filenames that must never appear in the bundle at any depth.
SENSITIVE_NAMES: set[str] = {
    ".env",
    "config.json",
    "cookies.txt",
    "cookie.txt",
    "credential.json",
    "credentials.json",
    "secret.json",
    "secrets.json",
    "token.txt",
}

# Sensitive path fragments (case-insensitive match on any component).
SENSITIVE_PATH_FRAGMENTS: set[str] = {
    "browser-profile",
    "browser_profile",
    "keychain",
    "credentials",
}

SENSITIVE_DATA_SUFFIXES: set[str] = {
    ".db",
    ".ini",
    ".json",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".txt",
    ".yaml",
    ".yml",
}
SENSITIVE_DATA_STEMS: set[str] = {
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "key",
    "secret",
    "secrets",
    "token",
    "tokens",
}


def _normalized_component(value: str) -> str:
    return re.sub(r"[-_.\s]+", "", value.casefold())


def _archive_name(path: PurePath) -> str:
    """Return the platform-independent path used by ZIP files and manifests."""
    return path.as_posix()


def _sensitive_data_name(name: str) -> bool:
    path = Path(name)
    if path.suffix.casefold() in {".key", ".p12", ".pem", ".pfx"}:
        return True
    stem = _normalized_component(path.stem)
    return (
        path.suffix.casefold() in SENSITIVE_DATA_SUFFIXES
        and stem in SENSITIVE_DATA_STEMS
    )


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _should_skip(name: str) -> bool:
    if name in SKIP_NAMES:
        return True
    if _is_hidden(name):
        return True
    for suffix in SKIP_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False


def collect_bundle_files(project_root: Path) -> list[Path]:
    """Collect all files that belong in the bundle (relative to project root)."""
    result: list[Path] = []

    # Explicit single files.
    for rel in ALWAYS_INCLUDE_FILES:
        p = project_root / rel
        if p.is_file():
            result.append(Path(rel))

    # Directories.
    for dir_rel in INCLUDE_DIRS:
        dir_path = project_root / dir_rel
        if not dir_path.is_dir():
            continue
        for child in sorted(dir_path.rglob("*")):
            if not child.is_file():
                continue
            rel = child.relative_to(project_root)
            parts = rel.parts
            if any(_should_skip(part) for part in parts):
                continue
            result.append(rel)

    return sorted(result)


def _check_security(project_root: Path, files: list[Path]) -> list[str]:
    """Return a list of security violations found in the bundle file list."""
    errors: list[str] = []
    for rel in files:
        full = project_root / rel
        # Symlinks.
        if full.is_symlink():
            errors.append(f"symlink: {rel}")
        # Absolute path components.
        if rel.is_absolute():
            errors.append(f"absolute path: {rel}")
        # Sensitive filenames.
        for part in rel.parts:
            if part.casefold() in SENSITIVE_NAMES or _sensitive_data_name(part):
                errors.append(f"sensitive file: {rel}")
                break
        # Sensitive path fragments.
        lower_parts = [_normalized_component(p) for p in rel.parts[:-1]]
        for frag in SENSITIVE_PATH_FRAGMENTS:
            normalized_fragment = _normalized_component(frag)
            if any(normalized_fragment in part for part in lower_parts):
                errors.append(f"sensitive path fragment '{frag}': {rel}")
                break
        # Path traversal.
        resolved = full.resolve()
        try:
            resolved.relative_to(project_root.resolve())
        except ValueError:
            errors.append(f"path traversal: {rel}")
    return errors


def _compute_manifest(files: list[Path], project_root: Path) -> dict[str, str]:
    """Return {relative_path: sha256} for every file in the bundle."""
    manifest: dict[str, str] = {}
    for rel in files:
        full = project_root / rel
        h = hashlib.sha256()
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        manifest[_archive_name(rel)] = h.hexdigest()
    return manifest


def build_zip(project_root: Path, output_path: Path) -> dict:
    """Build the Skill ZIP bundle and return metadata."""
    files = collect_bundle_files(project_root)
    if not files:
        raise RuntimeError("No files collected for bundle")

    violations = _check_security(project_root, files)
    if violations:
        raise RuntimeError(
            "Security violations found:\n" + "\n".join(f"  - {v}" for v in violations)
        )

    manifest = _compute_manifest(files, project_root)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            full = project_root / rel
            # Store with deterministic permissions (readable by all).
            info = zipfile.ZipInfo(_archive_name(rel))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
            ) << 16
            with open(full, "rb") as f:
                zf.writestr(info, f.read())

    # Compute bundle SHA-256.
    bundle_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()

    return {
        "zip_path": str(output_path),
        "file_count": len(files),
        "bundle_sha256": bundle_hash,
        "manifest": manifest,
    }


def verify_existing_bundle(
    project_root: Path,
    bundle_path: Path,
    manifest_path: Optional[Path] = None,
) -> list[str]:
    """Verify that an existing bundle exactly represents the current project files."""
    project_root = project_root.resolve()
    errors: list[str] = []
    if not bundle_path.is_file():
        return [f"bundle missing: {bundle_path}"]

    files = collect_bundle_files(project_root)
    security_errors = _check_security(project_root, files)
    errors.extend(security_errors)
    current_manifest = _compute_manifest(files, project_root)
    expected_names = [_archive_name(path) for path in files]
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            actual_names = archive.namelist()
            if actual_names != expected_names:
                errors.append("bundle file list differs from current source")
            for name, digest in current_manifest.items():
                try:
                    payload = archive.read(name)
                except KeyError:
                    errors.append(f"bundle file missing: {name}")
                    continue
                actual = hashlib.sha256(payload).hexdigest()
                if actual != digest:
                    errors.append(f"source content changed after bundle build: {name}")
    except (OSError, zipfile.BadZipFile) as exc:
        return errors + [f"bundle unreadable: {exc}"]

    if manifest_path is not None:
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"manifest unreadable: {exc}")
        else:
            bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            if manifest_data.get("bundle_sha256") != bundle_hash:
                errors.append("manifest bundle_sha256 does not match bundle")
            if manifest_data.get("file_count") != len(files):
                errors.append("manifest file_count does not match current source")
            if manifest_data.get("files") != current_manifest:
                errors.append("manifest file hashes differ from current source")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Skill ZIP bundle")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root (default: parent of this script's directory)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output ZIP path (default: dist/skill-bundle.zip)",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=None,
        help="Write SHA-256 manifest JSON to this path",
    )
    parser.add_argument(
        "--verify-existing",
        type=Path,
        default=None,
        help="Verify an existing ZIP against the current project instead of building",
    )
    parser.add_argument(
        "--manifest-in",
        type=Path,
        default=None,
        help="Optional manifest JSON to verify with --verify-existing",
    )
    args = parser.parse_args()

    if args.project_root:
        project_root = args.project_root.resolve()
    else:
        project_root = Path(__file__).resolve().parent.parent

    if args.verify_existing is not None:
        if args.output is not None or args.manifest_out is not None:
            parser.error("--verify-existing cannot be combined with --output or --manifest-out")
        errors = verify_existing_bundle(
            project_root,
            args.verify_existing.resolve(),
            args.manifest_in.resolve() if args.manifest_in else None,
        )
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "bundle": str(args.verify_existing.resolve()),
                    "errors": errors,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        raise SystemExit(1 if errors else 0)

    output_path = args.output or project_root / "dist" / "skill-bundle.zip"
    manifest_path = args.manifest_out or output_path.with_name(
        output_path.stem + "-manifest.json"
    )

    result = build_zip(project_root, output_path)

    # Write manifest.
    manifest_data = {
        "bundle_sha256": result["bundle_sha256"],
        "file_count": result["file_count"],
        "files": result["manifest"],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
