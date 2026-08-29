import argparse
import json

from scripts.mcu import browser_profile
from scripts.source_adapter import BROWSER_PROFILE_MARKER, BROWSER_PROFILE_MARKER_CONTENT


def profile_config(tmp_path):
    return {
        "paths": {
            "temp_root": str(tmp_path / "cache"),
            "output_root": str(tmp_path / "output"),
        },
        "acquisition": {"browser_profile_dir": str(tmp_path / "profile")},
    }


def test_browser_profile_status_does_not_create_directory(monkeypatch, tmp_path, capsys):
    config = profile_config(tmp_path)
    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))

    exit_code = browser_profile(argparse.Namespace(config=None, profile_action="status", yes=False))
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["configured"] is True
    assert payload["exists"] is False
    assert not (tmp_path / "profile").exists()


def test_browser_profile_reset_requires_confirmation(monkeypatch, tmp_path, capsys):
    config = profile_config(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / BROWSER_PROFILE_MARKER).write_text(BROWSER_PROFILE_MARKER_CONTENT, encoding="utf-8")
    (profile / "Cookies").write_text("test", encoding="utf-8")
    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))

    exit_code = browser_profile(argparse.Namespace(config=None, profile_action="reset", yes=False))
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["deleted"] is False
    assert payload["confirmation_required"] is True
    assert profile.exists()


def test_browser_profile_reset_deletes_only_configured_directory(monkeypatch, tmp_path, capsys):
    config = profile_config(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / BROWSER_PROFILE_MARKER).write_text(BROWSER_PROFILE_MARKER_CONTENT, encoding="utf-8")
    (profile / "Cookies").write_text("test", encoding="utf-8")
    unrelated = tmp_path / "keep"
    unrelated.mkdir()
    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))

    exit_code = browser_profile(argparse.Namespace(config=None, profile_action="reset", yes=True))
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["deleted"] is True
    assert not profile.exists()
    assert unrelated.exists()


def test_browser_profile_reset_refuses_unmanaged_directory(monkeypatch, tmp_path, capsys):
    config = profile_config(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "important.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, None))

    exit_code = browser_profile(argparse.Namespace(config=None, profile_action="reset", yes=True))
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error"] == "UNMANAGED_BROWSER_PROFILE"
    assert profile.exists()
    assert (profile / "important.txt").read_text(encoding="utf-8") == "keep"
