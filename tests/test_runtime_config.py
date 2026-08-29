from scripts.mcu import build_parser, resolve_analyze_options


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
