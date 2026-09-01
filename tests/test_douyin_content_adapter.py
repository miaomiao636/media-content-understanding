import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.douyin_content_adapter as adapter_module
from scripts.douyin_content_adapter import (
    DouyinContentAdapter,
    SafeImageRedirectHandler,
    build_acquisition_audit_record,
    classify_content_kind,
    download_public_image,
    normalize_douyin_payload,
    parse_douyin_page,
    validate_acquisition_audit_record,
    validate_public_media_url,
)
from scripts.source_adapter import AcquisitionError, SourceRouter, default_adapters, resolve_share_url

FIXTURES = Path(__file__).parent / "fixtures" / "douyin"


def test_structured_data_wins_and_preserves_author_content_and_image_order():
    result = parse_douyin_page(
        (FIXTURES / "note-structured.html").read_text(encoding="utf-8"),
        "https://www.douyin.com/note/7659275356428852849",
    )

    assert result.extraction_method == "structured-data"
    assert result.content_kind == "mixed"
    assert result.source_id == "7659275356428852849"
    assert result.title == "三步整理旅行照片"
    assert result.author == "示例作者"
    assert result.published_at == "2026-08-30T04:00:00+00:00"
    assert result.body_text == "第一步筛选。\n第二步统一色调。\n第三步按地点归档。"
    assert result.image_urls == [
        "https://media.example/first.jpg",
        "https://media.example/second.jpg",
    ]
    combined = "\n".join((result.title, result.body_text))
    assert "评论" not in combined
    assert "猜你喜欢" not in combined
    assert "广告" not in combined


def test_structured_data_never_substitutes_a_recommended_work_for_target():
    html = """
    <html><body><script type="application/json">
      {"recommendations":[{"aweme_id":"999","desc":"推荐作品正文",
      "images":[{"url_list":["https://media.example/recommended.jpg"]}]}]}
    </script></body></html>
    """

    with pytest.raises(AcquisitionError) as exc_info:
        parse_douyin_page(html, "https://www.douyin.com/note/123")

    assert exc_info.value.error_type == "CONTENT_NOT_FOUND"


def test_mismatched_dom_snapshot_is_not_used_for_target_work():
    with pytest.raises(AcquisitionError) as exc_info:
        parse_douyin_page(
            "<html><body></body></html>",
            "https://www.douyin.com/note/123",
            dom_snapshot={"source_id": "999", "body_text": "推荐作品正文"},
        )

    assert exc_info.value.error_type == "CONTENT_NOT_FOUND"


def test_dom_snapshot_without_source_id_is_not_bound_to_requested_work():
    with pytest.raises(AcquisitionError) as exc_info:
        parse_douyin_page(
            "<html><body></body></html>",
            "https://www.douyin.com/note/123",
            dom_snapshot={"body_text": "未绑定作品的正文"},
        )

    assert exc_info.value.error_type == "CONTENT_NOT_FOUND"


def test_generic_meta_description_is_not_used_as_author_body():
    with pytest.raises(AcquisitionError) as exc_info:
        parse_douyin_page(
            "<html><head><meta name='description' content='搜索和推荐'></head><body></body></html>",
            "https://www.douyin.com/note/123",
        )

    assert exc_info.value.error_type == "CONTENT_NOT_FOUND"


def test_dom_fallback_keeps_gallery_order_and_excludes_page_chrome():
    result = parse_douyin_page(
        (FIXTURES / "note-dom-gallery.html").read_text(encoding="utf-8"),
        "https://www.douyin.com/note/7659275356428852849",
    )

    assert result.extraction_method == "dom-fallback"
    assert result.content_kind == "gallery"
    assert result.title == "两张设计稿"
    assert result.author == "DOM 作者"
    assert result.published_at == "2026-08-30T08:30:00+08:00"
    assert result.body_text == ""
    assert result.image_urls == [
        "https://media.example/001.jpg",
        "https://media.example/002.jpg",
    ]


def test_runtime_dom_snapshot_is_used_only_after_structured_data_is_absent():
    result = parse_douyin_page(
        "<html><head><meta name='description' content='搜索和推荐'></head><body></body></html>",
        "https://www.douyin.com/note/123",
        dom_snapshot={
            "source_id": "123",
            "title": "作者标题",
            "author": "作者 认证徽章",
            "published_at": "2026-08-30 12:00:00",
            "body_text": "作者正文",
            "image_urls": [
                "https://media.example/001.jpg",
                "https://media.example/001.jpg",
                "https://media.example/002.jpg",
            ],
        },
    )

    assert result.extraction_method == "dom-fallback"
    assert result.content_kind == "mixed"
    assert result.author == "作者"
    assert result.body_text == "作者正文"
    assert result.image_urls == [
        "https://media.example/001.jpg",
        "https://media.example/002.jpg",
    ]


def test_normalizer_distinguishes_long_text_gallery_and_mixed():
    base = {
        "aweme_id": "123",
        "item_title": "标题",
        "author": {"nickname": "作者"},
    }
    long_text = normalize_douyin_payload({**base, "desc": "只有正文"})
    gallery = normalize_douyin_payload(
        {**base, "images": [{"url_list": ["https://media.example/1.jpg"]}]}
    )
    mixed = normalize_douyin_payload(
        {
            **base,
            "desc": "图文正文",
            "images": [{"url_list": ["https://media.example/1.jpg"]}],
        }
    )

    assert long_text and long_text.content_kind == "long_text"
    assert gallery and gallery.content_kind == "gallery"
    assert mixed and mixed.content_kind == "mixed"
    assert classify_content_kind("", []) == "unknown"


def test_login_page_is_reported_without_using_its_prompt_as_author_content():
    with pytest.raises(AcquisitionError) as exc_info:
        parse_douyin_page(
            (FIXTURES / "note-login.html").read_text(encoding="utf-8"),
            "https://www.douyin.com/note/7659275356428852849",
        )

    assert exc_info.value.error_type == "AUTHENTICATION_REQUIRED"


def test_challenge_page_is_reported_without_bypass_attempt():
    with pytest.raises(AcquisitionError) as exc_info:
        parse_douyin_page(
            "<html><body><main>请完成安全验证后继续</main></body></html>",
            "https://www.douyin.com/note/7659275356428852849",
        )

    assert exc_info.value.error_type == "CHALLENGE_REQUIRED"


def test_access_block_wins_over_stale_structured_data_and_dom_snapshot():
    html = """
    <html><body>
      <main>请先登录后查看该内容</main>
      <script type="application/json">
        {"aweme_id":"123","desc":"不应返回的正文"}
      </script>
    </body></html>
    """

    with pytest.raises(AcquisitionError) as exc_info:
        parse_douyin_page(
            html,
            "https://www.douyin.com/note/123",
            dom_snapshot={"source_id": "123", "body_text": "不应返回的 DOM 正文"},
        )

    assert exc_info.value.error_type == "AUTHENTICATION_REQUIRED"


def test_access_block_detection_ignores_non_visible_script_text():
    result = parse_douyin_page(
        "<html><body><script>const captcha = 'verify';</script></body></html>",
        "https://www.douyin.com/note/123",
        dom_snapshot={"source_id": "123", "body_text": "公开作者正文"},
    )

    assert result.content_kind == "long_text"
    assert result.body_text == "公开作者正文"


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("http://127.0.0.1/image.jpg", "127.0.0.1"),
        ("https://media.example/image.jpg", "10.0.0.5"),
        ("https://media.example/image.jpg", "169.254.1.1"),
        ("https://media.example/image.jpg", "::1"),
    ],
)
def test_image_url_rejects_direct_and_dns_resolved_private_addresses(url, address):
    def resolver(*args, **kwargs):
        return [(2, 1, 6, "", (address, 443))]

    with pytest.raises(AcquisitionError) as exc_info:
        validate_public_media_url(url, resolver=resolver)

    assert exc_info.value.error_type == "UNSAFE_MEDIA_URL"


def test_image_url_rejects_non_http_protocol():
    with pytest.raises(AcquisitionError) as exc_info:
        validate_public_media_url("file:///etc/passwd", resolver=lambda *args, **kwargs: [])

    assert exc_info.value.error_type == "UNSAFE_MEDIA_URL"


def test_redirects_validate_each_destination_and_enforce_count(monkeypatch):
    checked = []
    monkeypatch.setattr(
        adapter_module,
        "validate_public_media_url",
        lambda value, **kwargs: checked.append(value) or value,
    )
    handler = SafeImageRedirectHandler(max_redirects=1)
    request = adapter_module.urllib.request.Request("https://media.example/one.jpg")
    redirected = handler.redirect_request(
        request, None, 302, "Found", {}, "https://cdn.example/two.jpg"
    )

    assert redirected is not None
    assert checked == ["https://cdn.example/two.jpg"]
    with pytest.raises(AcquisitionError) as exc_info:
        handler.redirect_request(
            redirected, None, 302, "Found", {}, "https://cdn.example/three.jpg"
        )
    assert exc_info.value.error_type == "TOO_MANY_REDIRECTS"


def test_cross_origin_redirect_strips_cookie_and_authorization(monkeypatch):
    monkeypatch.setattr(
        adapter_module,
        "validate_public_media_url",
        lambda value, **kwargs: value,
    )
    handler = SafeImageRedirectHandler()
    request = adapter_module.urllib.request.Request(
        "https://www.douyin.com/media/source.mp4",
        headers={
            "Cookie": "session=browser-secret",
            "Authorization": "Bearer browser-secret",
            "User-Agent": "test-agent",
        },
    )

    redirected = handler.redirect_request(
        request, None, 302, "Found", {}, "https://cdn.example/video.mp4"
    )

    assert redirected is not None
    assert redirected.get_header("Cookie") is None
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("User-agent") == "test-agent"


class _FakeResponse:
    def __init__(self, payload, *, content_type="image/jpeg", content_length=""):
        self.payload = payload
        self.offset = 0
        self.headers = {"Content-Type": content_type, "Content-Length": content_length}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return "https://media.example/image.jpg"

    def read(self, size):
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class _FakeOpener:
    mcu_dns_pinned = True

    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        return self.response


def test_dns_rebinding_is_rejected_before_any_connection(monkeypatch, tmp_path):
    answers = iter(("93.184.216.34", "127.0.0.1"))

    def rebinding_resolver(*args, **kwargs):
        address = next(answers)
        return [(2, 1, 6, "", (address, 443))]

    monkeypatch.setattr(
        adapter_module.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("私网重绑地址不得进入连接阶段"),
    )

    with pytest.raises(AcquisitionError) as exc_info:
        download_public_image(
            "https://media.example/image.jpg",
            tmp_path / "001",
            referer="https://www.douyin.com/note/123",
            timeout_seconds=5,
            max_bytes=100,
            resolver=rebinding_resolver,
        )

    assert exc_info.value.error_type == "UNSAFE_MEDIA_URL"
    assert not (tmp_path / "001.part").exists()


def test_pinned_connection_uses_validated_literal_ip_without_resolving_again(monkeypatch):
    resolver_calls = []
    connected = []
    sentinel = object()

    def resolver(host, port, **kwargs):
        resolver_calls.append((host, port))
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    def connect(address, timeout, source_address):
        connected.append(address)
        return sentinel

    monkeypatch.setattr(adapter_module.socket, "create_connection", connect)
    connection = adapter_module._PinnedHTTPSConnection(
        "media.example", resolver=resolver, timeout=5
    )

    assert connection._create_connection(("media.example", 443), 5, None) is sentinel
    assert resolver_calls == [("media.example", 443)]
    assert connected == [("93.184.216.34", 443)]


def test_unpinned_custom_image_opener_is_rejected(tmp_path):
    opener = _FakeOpener(_FakeResponse(b"payload"))
    opener.mcu_dns_pinned = False

    with pytest.raises(AcquisitionError) as exc_info:
        download_public_image(
            "https://media.example/image.jpg",
            tmp_path / "001",
            referer="https://www.douyin.com/note/123",
            timeout_seconds=5,
            max_bytes=100,
            opener=opener,
            resolver=lambda *args, **kwargs: [
                (2, 1, 6, "", ("93.184.216.34", 443))
            ],
        )

    assert exc_info.value.error_type == "UNSAFE_MEDIA_URL"


def test_image_download_enforces_streamed_item_size_and_removes_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter_module, "validate_public_media_url", lambda value, **kwargs: value)
    stem = tmp_path / "001"

    with pytest.raises(AcquisitionError) as exc_info:
        download_public_image(
            "https://media.example/image.jpg",
            stem,
            referer="https://www.douyin.com/note/123",
            timeout_seconds=5,
            max_bytes=5,
            opener=_FakeOpener(_FakeResponse(b"123456")),
        )

    assert exc_info.value.error_type == "INPUT_TOO_LARGE"
    assert not stem.with_suffix(".part").exists()


def test_image_download_rejects_non_image_response(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter_module, "validate_public_media_url", lambda value, **kwargs: value)

    with pytest.raises(AcquisitionError) as exc_info:
        download_public_image(
            "https://media.example/image.jpg",
            tmp_path / "001",
            referer="https://www.douyin.com/note/123",
            timeout_seconds=5,
            max_bytes=100,
            opener=_FakeOpener(_FakeResponse(b"not-image", content_type="text/html")),
        )

    assert exc_info.value.error_type == "INVALID_MEDIA"


def test_image_download_rejects_forged_image_mime(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter_module, "validate_public_media_url", lambda value, **kwargs: value)

    with pytest.raises(AcquisitionError) as exc_info:
        download_public_image(
            "https://media.example/image.jpg",
            tmp_path / "001",
            referer="https://www.douyin.com/note/123",
            timeout_seconds=5,
            max_bytes=100,
            opener=_FakeOpener(_FakeResponse(b"<html>not an image</html>")),
        )

    assert exc_info.value.error_type == "INVALID_MEDIA"
    assert not (tmp_path / "001.part").exists()


def test_gallery_download_enforces_count_and_total_budget(monkeypatch, tmp_path):
    adapter = DouyinContentAdapter(max_images=2, max_image_mb=1, max_total_image_mb=1 / 1024)
    with pytest.raises(AcquisitionError) as exc_info:
        adapter._download_images(
            ["https://media.example/1.jpg"] * 3,
            tmp_path,
            referer="https://www.douyin.com/note/123",
        )
    assert exc_info.value.error_type == "TOO_MANY_IMAGES"

    sizes = iter((700, 400))

    def fake_download(url, output_stem, **kwargs):
        size = next(sizes)
        if size > kwargs["max_bytes"]:
            raise AcquisitionError("INPUT_TOO_LARGE", "合计预算不足")
        output = output_stem.with_suffix(".jpg")
        output.write_bytes(b"x")
        return output, size

    monkeypatch.setattr(adapter_module, "download_public_image", fake_download)
    with pytest.raises(AcquisitionError) as exc_info:
        adapter._download_images(
            ["https://media.example/1.jpg", "https://media.example/2.jpg"],
            tmp_path / "second",
            referer="https://www.douyin.com/note/123",
        )
    assert exc_info.value.error_type == "INPUT_TOO_LARGE"


def test_source_router_skips_adapters_that_do_not_support_url(monkeypatch, tmp_path):
    class NoteOnly:
        name = "note-only"

        def supports(self, url):
            return False

        def available(self):
            raise AssertionError("不支持的来源不应检查依赖")

    class Success:
        name = "success"

        def available(self):
            return True

        def acquire(self, url, work_dir):
            from scripts.source_adapter import AcquiredSource

            return AcquiredSource(
                platform="bilibili",
                input_url=url,
                canonical_url=url,
                source_id="BV1Ab411c7De",
                title="test",
                author="",
                duration=1,
                published_at="",
                media_path=str(work_dir / "source.mp4"),
            )

    monkeypatch.setattr("scripts.source_adapter.resolve_share_url", lambda value: value)
    result = SourceRouter([NoteOnly(), Success()]).acquire(
        "https://www.bilibili.com/video/BV1Ab411c7De", tmp_path
    )

    assert result.acquisition_method == ""
    assert [attempt.adapter for attempt in result.attempts] == ["success"]


def test_source_router_stops_on_note_challenge_instead_of_trying_video_fallback(
    monkeypatch, tmp_path
):
    class Challenge:
        name = "douyin-content"

        def available(self):
            return True

        def acquire(self, url, work_dir):
            raise AcquisitionError(
                "CHALLENGE_REQUIRED", "请人工完成验证后重试", adapter=self.name
            )

    class MustNotRun:
        name = "video-fallback"

        def available(self):
            raise AssertionError("挑战出现后不得尝试绕过或改走视频获取")

    monkeypatch.setattr("scripts.source_adapter.resolve_share_url", lambda value: value)
    with pytest.raises(AcquisitionError) as exc_info:
        SourceRouter([Challenge(), MustNotRun()]).acquire(
            "https://www.douyin.com/note/7659275356428852849", tmp_path
        )

    assert exc_info.value.error_type == "CHALLENGE_REQUIRED"


def test_source_router_never_treats_failed_note_as_video(monkeypatch, tmp_path):
    class MissingContent:
        name = "douyin-content"

        def available(self):
            return True

        def acquire(self, url, work_dir):
            raise AcquisitionError(
                "CONTENT_NOT_FOUND", "没有可验证的作者内容", adapter=self.name
            )

    class MustNotRun:
        name = "video-fallback"

        def available(self):
            raise AssertionError("图文解析失败后不得将 /note/ 当作视频获取")

    monkeypatch.setattr("scripts.source_adapter.resolve_share_url", lambda value: value)
    with pytest.raises(AcquisitionError) as exc_info:
        SourceRouter([MissingContent(), MustNotRun()]).acquire(
            "https://www.douyin.com/note/7659275356428852849", tmp_path
        )

    assert exc_info.value.error_type == "CONTENT_NOT_FOUND"


def test_yt_dlp_adapter_does_not_support_douyin_notes():
    from scripts.source_adapter import YtDlpAdapter

    assert (
        YtDlpAdapter().supports(
            "https://www.douyin.com/note/7659275356428852849"
        )
        is False
    )


def test_default_adapters_route_douyin_notes_before_video_download():
    adapters = default_adapters({"acquisition": {"browser_fallback": True}})

    assert [adapter.name for adapter in adapters] == [
        "douyin-content",
        "yt-dlp",
        "playwright-browser",
    ]


def test_canonical_note_url_does_not_require_short_link_network_resolution(monkeypatch):
    monkeypatch.setattr(
        "scripts.source_adapter.urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("规范 /note/ 地址不应再次执行短链请求"),
    )
    url = "https://www.douyin.com/note/7659275356428852849"

    assert resolve_share_url(url) == url


def test_live_acquisition_receipt_is_sanitized_and_self_verifying():
    record = json.loads(
        (FIXTURES / "real-public-note-success.json").read_text(encoding="utf-8")
    )

    assert validate_acquisition_audit_record(record) == []
    assert record["sample"]["source_id"] == "7659275356428852849"
    assert record["result"]["content_kind"] == "mixed"
    assert record["result"]["image_count"] == 8
    assert [item["position"] for item in record["result"]["images"]] == list(
        range(1, 9)
    )
    serialized = json.dumps(record, ensure_ascii=False)
    assert "image_urls" not in serialized
    assert "signature=" not in serialized
    assert "source-images/" not in serialized
    assert "/tmp/" not in serialized
    assert serialized.count("https://") == 1


def test_live_acquisition_receipt_can_be_rebuilt_from_successful_local_outputs(tmp_path):
    image_dir = tmp_path / "source-images"
    image_dir.mkdir()
    first = image_dir / "001.webp"
    second = image_dir / "002.webp"
    first.write_bytes(b"first-public-image")
    second.write_bytes(b"second-public-image")
    metadata = tmp_path / "content.info.json"
    metadata.write_text(
        json.dumps({"source_id": "123", "image_paths": [str(first), str(second)]}),
        encoding="utf-8",
    )
    source = SimpleNamespace(
        canonical_url="https://www.douyin.com/note/123",
        source_id="123",
        content_kind="mixed",
        media_path=None,
        body_text="作者正文",
        image_paths=[str(first), str(second)],
        metadata_path=str(metadata),
        title="公开标题",
        author="公开作者",
        published_at="2026-08-30T00:00:00Z",
        acquisition_method="douyin-content",
    )

    record = build_acquisition_audit_record(
        source, observed_at="2026-08-30T00:01:00Z"
    )

    assert validate_acquisition_audit_record(record) == []
    assert [item["filename"] for item in record["result"]["images"]] == [
        "001.webp",
        "002.webp",
    ]
    assert record["safety"]["remote_media_urls_stored"] is False


def test_live_acquisition_receipt_detects_tampering():
    record = json.loads(
        (FIXTURES / "real-public-note-success.json").read_text(encoding="utf-8")
    )
    record["result"]["image_count"] = 999

    errors = validate_acquisition_audit_record(record)

    assert "图片数量与图片记录不一致" in errors
    assert "收据哈希不匹配" in errors
