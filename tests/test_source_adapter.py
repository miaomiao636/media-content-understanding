import pytest

from scripts.source_adapter import (
    AcquiredSource,
    AcquisitionError,
    PlaywrightAdapter,
    SourceRouter,
    _declared_duration,
    classify_failure,
    extract_source_id,
    validate_supported_url,
)


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://v.douyin.com/abc123/", "douyin"),
        ("https://www.douyin.com/video/123456789", "douyin"),
        ("https://b23.tv/abc123", "bilibili"),
        ("https://www.bilibili.com/video/BV1Ab411c7De", "bilibili"),
    ],
)
def test_supported_platforms(url, platform):
    assert validate_supported_url(url)[0] == platform


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://example.com/video/1",
        "https://user:pass@www.bilibili.com/video/BV1Ab411c7De",
        "http://127.0.0.1/video/1",
    ],
)
def test_rejects_unsupported_or_unsafe_urls(url):
    with pytest.raises(AcquisitionError):
        validate_supported_url(url)


def test_source_id_extraction():
    assert (
        extract_source_id("douyin", "https://www.douyin.com/video/7633454865993256234")
        == "7633454865993256234"
    )
    assert extract_source_id("bilibili", "https://www.bilibili.com/video/BV1Ab411c7De") == "BV1Ab411c7De"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Fresh cookies are needed", "AUTHENTICATION_REQUIRED"),
        ("HTTP Error 412", "ACCESS_RESTRICTED"),
        ("captcha verification", "CHALLENGE_REQUIRED"),
        ("connection reset", "NETWORK_ERROR"),
    ],
)
def test_failure_classification(message, expected):
    assert classify_failure(message) == expected


def test_browser_candidate_scoring_prefers_matching_media():
    video = {"url": "https://cdn.example/video.mp4", "mime": "video/mp4"}
    audio = {"url": "https://cdn.example/audio.m4a", "mime": "audio/mp4"}
    assert PlaywrightAdapter._candidate_score(video, "video") > PlaywrightAdapter._candidate_score(
        audio, "video"
    )


def test_declared_duration_prefers_player_total_and_element_duration():
    assert _declared_duration("00:07 / 13:27\n评论里有 35:43", [807.2]) == pytest.approx(807.2)


class FailingAdapter:
    name = "failing"

    def available(self):
        return True

    def acquire(self, url, work_dir):
        raise AcquisitionError("NETWORK_ERROR", "temporary", adapter=self.name)


class SuccessAdapter:
    name = "success"

    def available(self):
        return True

    def acquire(self, url, work_dir):
        media = work_dir / "source.mp4"
        media.write_bytes(b"test")
        return AcquiredSource(
            platform="douyin",
            input_url=url,
            canonical_url=url,
            source_id="1",
            title="test",
            author="",
            duration=1.0,
            published_at="",
            media_path=str(media),
            acquisition_method=self.name,
        )


def test_router_records_failover(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.source_adapter.resolve_share_url", lambda value: value)
    result = SourceRouter([FailingAdapter(), SuccessAdapter()]).acquire(
        "https://www.douyin.com/video/1", tmp_path
    )
    assert result.acquisition_method == "success"
    assert [item.adapter for item in result.attempts] == ["failing", "success"]
    assert result.attempts[0].error_type == "NETWORK_ERROR"
