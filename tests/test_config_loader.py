import copy
import json

import pytest

from scripts.config_loader import DEFAULT_CONFIG, load_config


def test_loading_defaults_does_not_mutate_global_default_config(tmp_path):
    before = copy.deepcopy(DEFAULT_CONFIG)

    load_config(str(tmp_path / "missing.json"))

    assert DEFAULT_CONFIG == before


def test_loading_partial_existing_config_does_not_mutate_global_defaults(tmp_path):
    before = copy.deepcopy(DEFAULT_CONFIG)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"vision": {"max_frames": 7}}), encoding="utf-8")

    config, _ = load_config(str(path))

    assert config["vision"]["max_frames"] == 7
    assert DEFAULT_CONFIG == before


@pytest.mark.parametrize("section", ["paths", "acquisition", "asr", "retention", "vision"])
def test_configuration_sections_must_be_objects(tmp_path, section):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({section: None}), encoding="utf-8")

    with pytest.raises(ValueError, match=section):
        load_config(str(path))


def test_browser_profile_path_is_resolved_outside_cache(tmp_path):
    profile = tmp_path / "profiles" / "mcu"
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "paths": {
                    "temp_root": str(tmp_path / "cache"),
                    "output_root": str(tmp_path / "output"),
                },
                "acquisition": {"browser_profile_dir": str(profile)},
            }
        ),
        encoding="utf-8",
    )

    config, _ = load_config(str(path))

    assert config["acquisition"]["browser_profile_dir"] == str(profile.resolve())


@pytest.mark.parametrize("location", ["cache", "output", "parent"])
def test_browser_profile_rejects_unsafe_overlap(tmp_path, location):
    temp_root = tmp_path / "cache"
    output_root = tmp_path / "output"
    profile = {
        "cache": temp_root / "browser-profile",
        "output": output_root / "browser-profile",
        "parent": tmp_path,
    }[location]
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "paths": {"temp_root": str(temp_root), "output_root": str(output_root)},
                "acquisition": {"browser_profile_dir": str(profile)},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="browser_profile_dir"):
        load_config(str(path))


@pytest.mark.parametrize(
    "override",
    [
        {"acquisition": {"max_download_mb": 0}},
        {"asr": {"mode": "remote"}},
        {"retention": {"failed_job_retention_hours": -1}},
        {"retention": {"cache_ttl_days": -1}},
        {"retention": {"max_cache_gb": 0}},
        {"vision": {"max_frames": 0}},
        {"vision": {"max_visual_calls": 0}},
        {"vision": {"max_upload_mb": 0}},
    ],
)
def test_invalid_runtime_limits_are_rejected(tmp_path, override):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(override), encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(str(path))
