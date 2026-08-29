from scripts.asr_router import choose_subtitle, format_timestamp, parse_vtt_or_srt


def test_timestamp_format():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3661) == "01:01:01"


def test_choose_chinese_subtitle(tmp_path):
    english = tmp_path / "source.en.vtt"
    chinese = tmp_path / "source.zh-Hans.vtt"
    assert choose_subtitle([english, chinese]) == chinese


def test_parse_vtt_deduplicates_adjacent_captions(tmp_path):
    subtitle = tmp_path / "sample.zh.vtt"
    subtitle.write_text(
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.500\n<b>你好</b> 世界\n\n"
        "00:00:01.500 --> 00:00:02.500\n你好 世界\n\n"
        "00:00:02.500 --> 00:00:04.000\n第二句\n",
        encoding="utf-8",
    )
    segments = parse_vtt_or_srt(subtitle)
    assert [item.text for item in segments] == ["你好 世界", "第二句"]
    assert segments[1].start == 2.5
