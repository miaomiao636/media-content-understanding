from scripts.mcu import analyze, build_parser, resolve_analyze_options


def test_analyze_options_use_config_when_cli_omits_values():
    args = build_parser().parse_args(["analyze", "https://www.bilibili.com/video/BV1Ab411c7De"])
    config = {
        "asr": {"mode": "none", "local_model": "medium", "language": "en"},
        "vision": {"max_frames": 42},
    }

    options = resolve_analyze_options(args, config)

    assert options.asr_mode == "none"
    assert options.asr_model == "medium"
    assert options.language == "en"
    assert options.max_frames == 42
    assert options.storyboard_interval == 30.0


def test_explicit_cli_values_override_config():
    args = build_parser().parse_args(
        [
            "analyze",
            "https://www.bilibili.com/video/BV1Ab411c7De",
            "--asr",
            "local",
            "--asr-model",
            "small",
            "--language",
            "zh",
            "--storyboard-interval",
            "12.5",
            "--max-frames",
            "9",
        ]
    )
    config = {
        "asr": {"mode": "none", "local_model": "medium", "language": "en"},
        "vision": {"max_frames": 42},
    }

    options = resolve_analyze_options(args, config)

    assert options.asr_mode == "local"
    assert options.asr_model == "small"
    assert options.language == "zh"
    assert options.storyboard_interval == 12.5
    assert options.max_frames == 9


def test_analyze_options_reject_non_positive_storyboard_values():
    args = build_parser().parse_args(
        [
            "analyze",
            "https://www.bilibili.com/video/BV1Ab411c7De",
            "--storyboard-interval",
            "0",
        ]
    )

    try:
        resolve_analyze_options(args, {"asr": {}, "vision": {}})
    except ValueError as exc:
        assert "storyboard-interval" in str(exc)
    else:
        raise AssertionError("零故事板间隔必须被拒绝")


def test_analyze_propagates_selected_config_path_to_visual_workflow(monkeypatch, tmp_path):
    selected = tmp_path / "custom.json"
    config = {
        "paths": {"temp_root": str(tmp_path / "cache"), "output_root": str(tmp_path / "output")},
        "asr": {"mode": "none", "local_model": "small", "language": "zh"},
        "vision": {"max_frames": 20, "max_visual_calls": 3},
    }
    received = {}

    def fake_analyze_job(args, current, options, job, *, config_path=None):
        received["config_path"] = config_path
        return 0

    monkeypatch.setattr("scripts.mcu.load_config", lambda path: (config, selected))
    monkeypatch.setattr("scripts.mcu.create_job", lambda current: tmp_path / "job")
    monkeypatch.setattr("scripts.mcu.analyze_job", fake_analyze_job)
    args = build_parser().parse_args(
        ["--config", str(selected), "analyze", "https://www.bilibili.com/video/BV1Ab411c7De"]
    )

    assert analyze(args) == 0
    assert received["config_path"] == selected
