import os
import stat

import pytest

from scripts.source_adapter import (
    BROWSER_PROFILE_MARKER,
    AcquiredSource,
    AcquisitionError,
    PlaywrightAdapter,
    SourceRouter,
    YtDlpAdapter,
    _declared_duration,
    classify_failure,
    default_adapters,
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
        ("File is larger than max-filesize", "INPUT_TOO_LARGE"),
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


def test_browser_settings_are_propagated_to_all_adapters(tmp_path):
    profile = tmp_path / "browser-profile"
    adapters = default_adapters(
        {
            "acquisition": {
                "browser_fallback": True,
                "browser_headless": False,
                "browser_profile_dir": str(profile),
                "max_download_mb": 321,
            }
        }
    )

    assert [adapter.max_bytes for adapter in adapters] == [321 * 1024 * 1024, 321 * 1024 * 1024]
    assert adapters[1].profile_dir == profile.resolve()


def test_playwright_uses_persistent_context_when_profile_is_configured(tmp_path):
    calls = []
    context = object()

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            calls.append(("persistent", kwargs))
            return context

        def launch(self, **kwargs):
            raise AssertionError("配置专用档案时不应启动一次性浏览器")

    playwright = type("Playwright", (), {"chromium": Chromium()})()
    adapter = PlaywrightAdapter(profile_dir=tmp_path / "browser-profile")

    returned_context, browser = adapter._launch_context(playwright, RuntimeError)

    assert returned_context is context
    assert browser is None
    assert calls[0][0] == "persistent"
    assert calls[0][1]["user_data_dir"] == str((tmp_path / "browser-profile").resolve())


def test_playwright_uses_isolated_context_without_profile():
    context = object()
    calls = []

    class Browser:
        def new_context(self, **kwargs):
            calls.append(("new_context", kwargs))
            return context

    class Chromium:
        def launch(self, **kwargs):
            calls.append(("launch", kwargs))
            return Browser()

        def launch_persistent_context(self, **kwargs):
            raise AssertionError("未配置专用档案时不应使用持久上下文")

    playwright = type("Playwright", (), {"chromium": Chromium()})()
    adapter = PlaywrightAdapter()

    returned_context, browser = adapter._launch_context(playwright, RuntimeError)

    assert returned_context is context
    assert browser is not None
    assert [item[0] for item in calls] == ["launch", "new_context"]


def test_playwright_reports_profile_in_use(tmp_path):
    class ProfileInUseError(RuntimeError):
        pass

    class Chromium:
        def launch_persistent_context(self, **kwargs):
            raise ProfileInUseError("Failed to create a ProcessSingleton for the profile")

    playwright = type("Playwright", (), {"chromium": Chromium()})()
    adapter = PlaywrightAdapter(profile_dir=tmp_path / "browser-profile")

    with pytest.raises(AcquisitionError) as exc_info:
        adapter._launch_context(playwright, ProfileInUseError)

    assert exc_info.value.error_type == "BROWSER_PROFILE_IN_USE"
    assert "另一个任务" in str(exc_info.value)


@pytest.mark.skipif(os.name == "nt", reason="Windows 不使用 POSIX 目录权限")
def test_browser_profile_directory_is_private(tmp_path):
    profile = tmp_path / "browser-profile"
    adapter = PlaywrightAdapter(profile_dir=profile)

    adapter._prepare_profile_dir()

    assert stat.S_IMODE(profile.stat().st_mode) == 0o700
    assert (profile / BROWSER_PROFILE_MARKER).is_file()


def test_browser_profile_rejects_unmanaged_nonempty_directory(tmp_path):
    profile = tmp_path / "ordinary-directory"
    profile.mkdir()
    (profile / "user-file.txt").write_text("keep", encoding="utf-8")
    adapter = PlaywrightAdapter(profile_dir=profile)

    with pytest.raises(AcquisitionError) as exc_info:
        adapter._prepare_profile_dir()

    assert exc_info.value.error_type == "UNSAFE_BROWSER_PROFILE"
    assert (profile / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_yt_dlp_rejects_media_over_download_limit(monkeypatch, tmp_path):
    captured_command = []

    def fake_run(command, **kwargs):
        captured_command.extend(command)
        (tmp_path / "source.mp4").write_bytes(b"x" * 1025)
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    adapter = YtDlpAdapter(max_download_mb=1 / 1024)
    monkeypatch.setattr(adapter, "_command", lambda: ["yt-dlp"])
    monkeypatch.setattr("scripts.source_adapter.subprocess.run", fake_run)

    with pytest.raises(AcquisitionError) as exc_info:
        adapter.acquire("https://www.bilibili.com/video/BV1Ab411c7De", tmp_path)

    assert exc_info.value.error_type == "INPUT_TOO_LARGE"
    assert "--max-filesize" in captured_command


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
