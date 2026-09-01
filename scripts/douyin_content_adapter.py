#!/usr/bin/env python3
"""Acquire and normalize public Douyin notes without treating the page as a video.

The adapter deliberately keeps page parsing and media downloading separate.  It
prefers first-party structured data embedded in the page, uses a narrow author
content DOM fallback, and never stores raw HTML, cookies, or signed media URLs.
"""

from __future__ import annotations

import hashlib
import html
import http.client
import ipaddress
import json
import mimetypes
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from .browser_verification import (
        CHALLENGE_TEXT_TOKENS,
        CHALLENGE_URL_TOKENS,
        DEFAULT_POLL_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
        LOGIN_TEXT_TOKENS,
        LOGIN_URL_TOKENS,
        wait_for_user_verification,
    )
except ImportError:
    from browser_verification import (
        CHALLENGE_TEXT_TOKENS,
        CHALLENGE_URL_TOKENS,
        DEFAULT_POLL_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
        LOGIN_TEXT_TOKENS,
        LOGIN_URL_TOKENS,
        wait_for_user_verification,
    )

DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "m.douyin.com"}
ALLOWED_IMAGE_MIMES = {
    "image/avif",
    "image/bmp",
    "image/gif",
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
BLOCKED_DOM_TOKENS = {
    "ad-",
    "advert",
    "comment",
    "footer",
    "header",
    "login",
    "navigation",
    "recommend",
    "related",
    "sidebar",
}
AUTHOR_CONTENT_TOKENS = {
    "detail-desc",
    "note-content",
    "note-desc",
    "note-detail",
    "slide-content",
}
MAX_PAGE_BYTES = 16 * 1024 * 1024
ACQUISITION_AUDIT_SCHEMA = "douyin-public-acquisition-audit/v1"
VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def _acquisition_error(error_type: str, message: str) -> Exception:
    try:
        from .source_adapter import AcquisitionError
    except ImportError:
        from source_adapter import AcquisitionError

    return AcquisitionError(error_type, message, adapter="douyin-content")


@dataclass
class NormalizedDouyinContent:
    source_id: str
    title: str
    author: str
    published_at: str
    body_text: str
    image_urls: List[str]
    content_kind: str
    extraction_method: str
    has_video: bool = False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(256 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_digest(record: Dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "receipt_sha256"}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_acquisition_audit_record(
    source: Any,
    *,
    observed_at: Optional[str] = None,
    browser_profile_used: bool = False,
    cookie_browser_used: bool = False,
) -> Dict[str, Any]:
    """Build a sanitized receipt from an actual successful public-note acquisition.

    The receipt intentionally contains hashes instead of creator text or remote media
    URLs.  It is evidence of one observed run, not a promise that a later request will
    avoid platform login or challenge controls.
    """
    canonical_url = str(getattr(source, "canonical_url", "") or "")
    parsed = urllib.parse.urlparse(canonical_url)
    source_id = str(getattr(source, "source_id", "") or "")
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower().rstrip(".") not in DOUYIN_HOSTS
        or not re.fullmatch(rf"/note/{re.escape(source_id)}", parsed.path.rstrip("/"))
    ):
        raise ValueError("审计记录只能由规范公开抖音 /note/<id> 成功结果生成")
    content_kind = str(getattr(source, "content_kind", "") or "")
    if content_kind not in {"long_text", "gallery", "mixed"}:
        raise ValueError("审计记录只接受非视频内容类型")
    if getattr(source, "media_path", None):
        raise ValueError("非视频审计记录不应包含视频媒体路径")

    body_text = str(getattr(source, "body_text", "") or "")
    image_entries: List[Dict[str, Any]] = []
    for index, raw_path in enumerate(getattr(source, "image_paths", []) or [], start=1):
        image_path = Path(raw_path)
        if not image_path.is_file():
            raise ValueError(f"审计记录的第 {index} 张图片不存在")
        image_entries.append(
            {
                "position": index,
                "filename": image_path.name,
                "bytes": image_path.stat().st_size,
                "sha256": _sha256_file(image_path),
            }
        )

    metadata_path = Path(str(getattr(source, "metadata_path", "") or ""))
    metadata_is_sanitized = False
    if metadata_path.is_file():
        metadata_text = metadata_path.read_text(encoding="utf-8")
        metadata_is_sanitized = (
            '"image_urls"' not in metadata_text
            and "http://" not in metadata_text
            and "https://" not in metadata_text
        )
    if not metadata_is_sanitized:
        raise ValueError("成功获取的元数据未通过远程 URL 脱敏检查")

    observed = observed_at or datetime.now(tz=timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "schema_version": ACQUISITION_AUDIT_SCHEMA,
        "record_type": "sanitized-live-acquisition-receipt",
        "observed_at": observed,
        "sample": {"public_url": canonical_url, "source_id": source_id},
        "result": {
            "content_kind": content_kind,
            "title": str(getattr(source, "title", "") or ""),
            "author": str(getattr(source, "author", "") or ""),
            "published_at": str(getattr(source, "published_at", "") or ""),
            "body_chars": len(body_text),
            "body_sha256": hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
            "image_count": len(image_entries),
            "images": image_entries,
            "acquisition_method": str(getattr(source, "acquisition_method", "") or ""),
        },
        "safety": {
            "browser_profile_used": bool(browser_profile_used),
            "cookie_browser_used": bool(cookie_browser_used),
            "remote_media_urls_stored": False,
            "metadata_remote_url_scan_passed": True,
            "challenge_bypassed": False,
        },
        "replay_boundary": {
            "fresh_live_run_required_for_current_availability": True,
            "challenge_or_login_is_not_success": True,
        },
    }
    record["receipt_sha256"] = _audit_digest(record)
    errors = validate_acquisition_audit_record(record)
    if errors:
        raise ValueError("审计记录生成失败：" + "；".join(errors))
    return record


def validate_acquisition_audit_record(record: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(record, dict):
        return ["审计记录必须是 JSON 对象"]
    if record.get("schema_version") != ACQUISITION_AUDIT_SCHEMA:
        errors.append("审计记录 schema_version 不受支持")
    sample = record.get("sample") if isinstance(record.get("sample"), dict) else {}
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    safety = record.get("safety") if isinstance(record.get("safety"), dict) else {}
    public_url = str(sample.get("public_url") or "")
    source_id = str(sample.get("source_id") or "")
    parsed = urllib.parse.urlparse(public_url)
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").lower().rstrip(".") not in DOUYIN_HOSTS
        or not re.fullmatch(rf"/note/{re.escape(source_id)}", parsed.path.rstrip("/"))
    ):
        errors.append("样本必须是规范公开抖音 /note/<id> URL")
    if result.get("content_kind") not in {"long_text", "gallery", "mixed"}:
        errors.append("内容类型不是受支持的非视频类型")
    images = result.get("images") if isinstance(result.get("images"), list) else []
    if result.get("image_count") != len(images):
        errors.append("图片数量与图片记录不一致")
    if [item.get("position") for item in images if isinstance(item, dict)] != list(
        range(1, len(images) + 1)
    ):
        errors.append("图片顺序不连续")
    if any(
        not isinstance(item, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
        or not isinstance(item.get("bytes"), int)
        or item.get("bytes", 0) <= 0
        for item in images
    ):
        errors.append("图片哈希或大小记录无效")
    if safety.get("remote_media_urls_stored") is not False:
        errors.append("审计记录不得声明保存远程媒体 URL")
    if safety.get("challenge_bypassed") is not False:
        errors.append("审计记录不得声明绕过挑战")

    for key, value in _walk_audit_values(record):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            if key != "public_url" or value != public_url:
                errors.append("审计记录包含非样本页的远程 URL")
                break
    if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("receipt_sha256") or "")):
        errors.append("收据哈希格式无效")
    elif record.get("receipt_sha256") != _audit_digest(record):
        errors.append("收据哈希不匹配")
    return errors


def _walk_audit_values(value: Any, key: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from _walk_audit_values(child, str(child_key))
    elif isinstance(value, list):
        for child in value:
            yield from _walk_audit_values(child, key)
    else:
        yield key, value


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_flatten_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "value", "desc", "paragraphs"):
            if key in value:
                text = _flatten_text(value[key])
                if text:
                    return text
    return ""


def _published_at(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return str(value).strip()


def _url_from_image(value: Any) -> str:
    if isinstance(value, str):
        return value.strip() if value.strip().startswith(("http://", "https://")) else ""
    if not isinstance(value, dict):
        return ""
    for key in (
        "url_list",
        "download_url_list",
        "origin_url_list",
        "url",
        "src",
        "uri",
    ):
        candidate = value.get(key)
        if isinstance(candidate, list):
            for item in candidate:
                selected = _url_from_image(item)
                if selected:
                    return selected
        else:
            selected = _url_from_image(candidate)
            if selected:
                return selected
    return ""


def _image_urls(item: Dict[str, Any]) -> List[str]:
    containers = [item]
    for key in ("image_post_info", "imagePostInfo", "article_info", "articleInfo", "note_info"):
        value = item.get(key)
        if isinstance(value, dict):
            containers.append(value)
    urls: List[str] = []
    for container in containers:
        for key in ("images", "image_list", "imageList", "pictures"):
            values = container.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                selected = _url_from_image(value)
                if selected and selected not in urls:
                    urls.append(selected)
    return urls


def _article_body(item: Dict[str, Any]) -> str:
    article = item.get("article_info") or item.get("articleInfo") or item.get("note_info")
    if isinstance(article, dict):
        for key in ("content", "article_content", "articleContent", "text", "paragraphs", "desc"):
            text = _flatten_text(article.get(key))
            if text:
                return text
    return _first_text(item.get("desc"), item.get("description"), item.get("content"))


def classify_content_kind(body_text: str, image_urls: Sequence[str]) -> str:
    has_text = bool(body_text.strip())
    has_images = bool(image_urls)
    if has_text and has_images:
        return "mixed"
    if has_images:
        return "gallery"
    if has_text:
        return "long_text"
    return "unknown"


def _candidate_score(item: Dict[str, Any], expected_source_id: str) -> int:
    source_id = str(item.get("aweme_id") or item.get("awemeId") or item.get("item_id") or "")
    score = 0
    if source_id:
        score += 20
    if expected_source_id and source_id == expected_source_id:
        score += 100
    if any(key in item for key in ("desc", "description", "content")):
        score += 8
    if isinstance(item.get("author"), dict):
        score += 8
    if _image_urls(item):
        score += 20
    if any(key in item for key in ("article_info", "articleInfo", "note_info")):
        score += 15
    if isinstance(item.get("video"), dict):
        score += 2
    return score


def _candidate_source_id(item: Dict[str, Any]) -> str:
    return str(item.get("aweme_id") or item.get("awemeId") or item.get("item_id") or "")


def _walk_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def normalize_douyin_payload(
    payload: Any, *, expected_source_id: str = ""
) -> Optional[NormalizedDouyinContent]:
    candidates = list(_walk_dicts(payload))
    if not candidates:
        return None
    if expected_source_id:
        candidates = [
            candidate
            for candidate in candidates
            if _candidate_source_id(candidate) == expected_source_id
        ]
        if not candidates:
            return None
    item = max(candidates, key=lambda value: _candidate_score(value, expected_source_id))
    if _candidate_score(item, expected_source_id) < 20:
        return None
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    share_info = item.get("share_info") if isinstance(item.get("share_info"), dict) else {}
    source_id = str(
        item.get("aweme_id")
        or item.get("awemeId")
        or item.get("item_id")
        or expected_source_id
        or ""
    )
    title = _first_text(
        item.get("item_title"),
        item.get("itemTitle"),
        item.get("title"),
        item.get("note_title"),
        share_info.get("share_title"),
    )
    body_text = _article_body(item)
    images = _image_urls(item)
    kind = classify_content_kind(body_text, images)
    return NormalizedDouyinContent(
        source_id=source_id,
        title=title,
        author=_first_text(
            author.get("nickname"), author.get("name"), author.get("unique_id"), item.get("author_name")
        ),
        published_at=_published_at(
            item.get("create_time") or item.get("createTime") or item.get("publish_time")
        ),
        body_text=body_text,
        image_urls=images,
        content_kind=kind,
        extraction_method="structured-data",
        has_video=isinstance(item.get("video"), dict),
    )


def _decode_json_blob(value: str) -> Optional[Any]:
    candidate = urllib.parse.unquote(html.unescape(value)).strip()
    if not candidate:
        return None
    for raw in (candidate, candidate[candidate.find("{") : candidate.rfind("}") + 1]):
        if not raw:
            continue
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


class _DouyinHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: List[str] = []
        self.meta: Dict[str, str] = {}
        self.author_text: List[str] = []
        self.all_text: List[str] = []
        self.image_urls: List[str] = []
        self.title_text: List[str] = []
        self._stack: List[Tuple[bool, bool, str]] = []
        self._script_parts: Optional[List[str]] = None

    @staticmethod
    def _attributes(attrs: Sequence[Tuple[str, Optional[str]]]) -> Dict[str, str]:
        return {str(key).lower(): str(value or "") for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        attributes = self._attributes(attrs)
        marker = " ".join(
            (
                attributes.get("id", ""),
                attributes.get("class", ""),
                attributes.get("data-e2e", ""),
                attributes.get("role", ""),
            )
        ).lower()
        parent_blocked = self._stack[-1][0] if self._stack else False
        parent_capture = self._stack[-1][1] if self._stack else False
        blocked = parent_blocked or tag in {"nav", "aside", "footer", "header"} or any(
            token in marker for token in BLOCKED_DOM_TOKENS
        )
        capture = not blocked and (
            parent_capture
            or tag == "article"
            or any(token in marker for token in AUTHOR_CONTENT_TOKENS)
        )
        self._stack.append((blocked, capture, tag))
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "").strip()
            if key and content:
                self.meta[key] = content
        if tag == "script":
            script_id = attributes.get("id", "").lower()
            script_type = attributes.get("type", "").lower()
            if script_id in {"render_data", "__next_data__", "__initial_state__"} or "json" in script_type:
                self._script_parts = []
        if tag == "img" and capture:
            selected = _first_text(
                attributes.get("data-src"), attributes.get("data-original"), attributes.get("src")
            )
            if selected.startswith(("http://", "https://")) and selected not in self.image_urls:
                self.image_urls.append(selected)
        if tag in VOID_HTML_TAGS:
            self._stack.pop()

    def handle_startendtag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_HTML_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts))
            self._script_parts = None
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)
            return
        text = data.strip()
        if not text or not self._stack:
            return
        blocked, capture, tag = self._stack[-1]
        if tag in {"script", "style"}:
            return
        self.all_text.append(text)
        if tag == "title":
            self.title_text.append(text)
        if capture and not blocked and tag not in {"style", "script"}:
            self.author_text.append(text)


def _source_id_from_url(url: str) -> str:
    match = re.search(r"/(?:note|video)/(\d+)", url)
    if match:
        return match.group(1)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return str((query.get("modal_id") or [""])[0])


def detect_access_block(url: str, title: str, visible_text: str) -> Optional[Tuple[str, str]]:
    lowered_url = url.lower()
    text = f"{title}\n{visible_text}".lower()
    if any(token in lowered_url for token in CHALLENGE_URL_TOKENS) or any(
        token in text for token in CHALLENGE_TEXT_TOKENS
    ):
        return "CHALLENGE_REQUIRED", "抖音要求完成人机验证；请在专用浏览器中人工完成后重试"
    if any(token in lowered_url for token in LOGIN_URL_TOKENS) or any(
        token in text for token in LOGIN_TEXT_TOKENS
    ):
        return "AUTHENTICATION_REQUIRED", "该抖音内容需要登录；请在已配置的专用浏览器中主动登录后重试"
    return None


def normalize_dom_snapshot(
    snapshot: Optional[Dict[str, Any]], *, expected_source_id: str
) -> Optional[NormalizedDouyinContent]:
    if not isinstance(snapshot, dict):
        return None
    snapshot_source_id = _first_text(snapshot.get("source_id"))
    if expected_source_id and snapshot_source_id != expected_source_id:
        return None
    body = _first_text(snapshot.get("body_text"))
    raw_images = snapshot.get("image_urls") if isinstance(snapshot.get("image_urls"), list) else []
    images: List[str] = []
    for value in raw_images:
        selected = _url_from_image(value)
        if selected and selected not in images:
            images.append(selected)
    kind = classify_content_kind(body, images)
    if kind == "unknown":
        return None
    author = _first_text(snapshot.get("author"))
    for token in ("认证徽章", "已关注", "关注"):
        author = author.replace(token, "")
    return NormalizedDouyinContent(
        source_id=_first_text(snapshot_source_id, expected_source_id),
        title=_first_text(snapshot.get("title")),
        author=re.sub(r"\s+", " ", author).strip(),
        published_at=_first_text(snapshot.get("published_at")),
        body_text=body,
        image_urls=images,
        content_kind=kind,
        extraction_method="dom-fallback",
    )


def parse_douyin_page(
    html_text: str,
    canonical_url: str,
    *,
    dom_snapshot: Optional[Dict[str, Any]] = None,
    expected_source_id: str = "",
) -> NormalizedDouyinContent:
    if len(html_text.encode("utf-8", errors="ignore")) > MAX_PAGE_BYTES:
        raise _acquisition_error("INPUT_TOO_LARGE", "抖音页面超过解析大小上限")
    parser = _DouyinHTMLParser()
    parser.feed(html_text)
    expected_id = expected_source_id or _source_id_from_url(canonical_url)
    block = detect_access_block(
        canonical_url,
        _first_text(parser.meta.get("og:title"), *parser.title_text),
        "\n".join(parser.all_text),
    )
    if block:
        raise _acquisition_error(*block)
    structured: List[NormalizedDouyinContent] = []
    for script in parser.scripts:
        payload = _decode_json_blob(script)
        if payload is None:
            continue
        normalized = normalize_douyin_payload(payload, expected_source_id=expected_id)
        if normalized and normalized.content_kind != "unknown":
            structured.append(normalized)
    if structured:
        selected = max(
            structured,
            key=lambda item: (
                item.source_id == expected_id,
                bool(item.body_text),
                len(item.image_urls),
            ),
        )
        selected.title = selected.title or _first_text(
            parser.meta.get("og:title"), parser.meta.get("twitter:title"), *parser.title_text
        )
        selected.author = selected.author or _first_text(
            parser.meta.get("author"), parser.meta.get("article:author")
        )
        selected.published_at = selected.published_at or _first_text(
            parser.meta.get("article:published_time"), parser.meta.get("publish_time")
        )
        return selected

    normalized_dom = normalize_dom_snapshot(dom_snapshot, expected_source_id=expected_id)
    if normalized_dom is not None:
        return normalized_dom

    body = "\n".join(parser.author_text).strip()
    kind = classify_content_kind(body, parser.image_urls)
    if kind == "unknown":
        raise _acquisition_error(
            "CONTENT_NOT_FOUND",
            "页面中没有找到可验证的作者正文或图集；未使用评论、推荐或导航作为回退",
        )
    return NormalizedDouyinContent(
        source_id=expected_id,
        title=_first_text(parser.meta.get("og:title"), parser.meta.get("twitter:title"), *parser.title_text),
        author=_first_text(parser.meta.get("author"), parser.meta.get("article:author")),
        published_at=_first_text(
            parser.meta.get("article:published_time"), parser.meta.get("publish_time")
        ),
        body_text=body,
        image_urls=parser.image_urls,
        content_kind=kind,
        extraction_method="dom-fallback",
    )


def validate_public_media_url(
    url: str,
    *,
    resolver: Optional[Callable[..., Sequence[Tuple[Any, ...]]]] = None,
) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise _acquisition_error("UNSAFE_MEDIA_URL", "媒体只允许 http(s) URL")
    if parsed.username or parsed.password:
        raise _acquisition_error("UNSAFE_MEDIA_URL", "媒体 URL 不能包含用户名或密码")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost":
        raise _acquisition_error("UNSAFE_MEDIA_URL", "媒体 URL 缺少公开主机名")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise _acquisition_error("UNSAFE_MEDIA_URL", "媒体 URL 端口无效") from exc
    _resolve_public_addresses(host, port, resolver=resolver)
    return url.strip()


def _resolve_public_addresses(
    host: str,
    port: int,
    *,
    resolver: Optional[Callable[..., Sequence[Tuple[Any, ...]]]] = None,
) -> List[str]:
    active_resolver = resolver or socket.getaddrinfo
    try:
        rows = active_resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise _acquisition_error("NETWORK_ERROR", f"媒体主机 DNS 解析失败：{host}") from exc
    addresses = list(
        dict.fromkeys(
            str(row[4][0]).split("%", 1)[0] for row in rows if len(row) > 4 and row[4]
        )
    )
    if not addresses:
        raise _acquisition_error("NETWORK_ERROR", f"媒体主机没有可用 DNS 记录：{host}")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise _acquisition_error("UNSAFE_MEDIA_URL", "媒体主机返回了无效 IP 地址") from exc
        if not ip.is_global:
            raise _acquisition_error("UNSAFE_MEDIA_URL", "媒体 URL 解析到内网或非公开地址")
    return addresses


class _PinnedConnectionMixin:
    def _configure_pinned_resolution(
        self,
        resolver: Optional[Callable[..., Sequence[Tuple[Any, ...]]]],
    ) -> None:
        self._mcu_pinned_addresses = _resolve_public_addresses(
            self.host, self.port, resolver=resolver
        )
        self._create_connection = self._create_pinned_connection

    def _create_pinned_connection(
        self,
        address: Tuple[str, int],
        timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address: Optional[Tuple[str, int]] = None,
    ) -> socket.socket:
        last_error: Optional[OSError] = None
        for pinned_ip in self._mcu_pinned_addresses:
            try:
                # The literal, already-validated IP prevents urllib/http.client from
                # resolving the attacker-controlled hostname a second time.
                return socket.create_connection(
                    (pinned_ip, address[1]), timeout, source_address
                )
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("没有可用的已验证公开 IP")


class _PinnedHTTPConnection(_PinnedConnectionMixin, http.client.HTTPConnection):
    def __init__(self, host: str, *, resolver: Optional[Callable[..., Any]] = None, **kwargs: Any):
        super().__init__(host, **kwargs)
        self._configure_pinned_resolution(resolver)


class _PinnedHTTPSConnection(_PinnedConnectionMixin, http.client.HTTPSConnection):
    def __init__(self, host: str, *, resolver: Optional[Callable[..., Any]] = None, **kwargs: Any):
        super().__init__(host, **kwargs)
        self._configure_pinned_resolution(resolver)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, resolver: Optional[Callable[..., Any]] = None) -> None:
        super().__init__()
        self.resolver = resolver

    def http_open(self, req: urllib.request.Request) -> Any:
        def connection(host: str, **kwargs: Any) -> _PinnedHTTPConnection:
            return _PinnedHTTPConnection(host, resolver=self.resolver, **kwargs)

        return self.do_open(connection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, resolver: Optional[Callable[..., Any]] = None) -> None:
        super().__init__()
        self.resolver = resolver

    def https_open(self, req: urllib.request.Request) -> Any:
        def connection(host: str, **kwargs: Any) -> _PinnedHTTPSConnection:
            return _PinnedHTTPSConnection(host, resolver=self.resolver, **kwargs)

        return self.do_open(connection, req, context=self._context)


class SafeImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        *,
        max_redirects: int = 3,
        resolver: Optional[Callable[..., Sequence[Tuple[Any, ...]]]] = None,
    ) -> None:
        super().__init__()
        self.max_redirects = max_redirects
        self.resolver = resolver

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Optional[urllib.request.Request]:
        redirect_count = int(getattr(req, "_mcu_redirect_count", 0)) + 1
        if redirect_count > self.max_redirects:
            raise _acquisition_error("TOO_MANY_REDIRECTS", "图片下载重定向次数超过上限")
        validate_public_media_url(
            urllib.parse.urljoin(req.full_url, newurl), resolver=self.resolver
        )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            setattr(redirected, "_mcu_redirect_count", redirect_count)
            previous = urllib.parse.urlparse(req.full_url)
            destination = urllib.parse.urlparse(redirected.full_url)

            def origin(parsed: urllib.parse.ParseResult) -> Tuple[str, str, int]:
                scheme = parsed.scheme.lower()
                default_port = 443 if scheme == "https" else 80
                return scheme, (parsed.hostname or "").lower().rstrip("."), parsed.port or default_port

            if origin(previous) != origin(destination):
                # urllib otherwise copies explicitly supplied authentication headers
                # to another origin.  Browser cookies are scoped per URL before the
                # first request; they must never follow a cross-origin redirect.
                for name in ("Cookie", "Authorization", "Proxy-Authorization"):
                    redirected.remove_header(name)
        return redirected


def build_safe_image_opener(
    *, resolver: Optional[Callable[..., Sequence[Tuple[Any, ...]]]] = None
) -> Any:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PinnedHTTPHandler(resolver),
        _PinnedHTTPSHandler(resolver),
        SafeImageRedirectHandler(resolver=resolver),
    )
    setattr(opener, "mcu_dns_pinned", True)
    return opener


def download_public_image(
    url: str,
    output_stem: Path,
    *,
    referer: str,
    timeout_seconds: int,
    max_bytes: int,
    opener: Optional[Any] = None,
    resolver: Optional[Callable[..., Sequence[Tuple[Any, ...]]]] = None,
) -> Tuple[Path, int]:
    if max_bytes <= 0:
        raise _acquisition_error("INPUT_TOO_LARGE", "图片剩余下载预算已经耗尽")
    validate_public_media_url(url, resolver=resolver)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Referer": referer,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
        },
    )
    if opener is not None and not bool(getattr(opener, "mcu_dns_pinned", False)):
        raise _acquisition_error(
            "UNSAFE_MEDIA_URL", "自定义图片下载器未声明使用已验证 IP 连接"
        )
    client = opener or build_safe_image_opener(resolver=resolver)
    temp_path = output_stem.with_suffix(".part")
    total = 0
    try:
        with client.open(request, timeout=timeout_seconds) as response:
            validate_public_media_url(response.geturl(), resolver=resolver)
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            if content_type not in ALLOWED_IMAGE_MIMES:
                raise _acquisition_error("INVALID_MEDIA", f"图片响应类型不受支持：{content_type or 'unknown'}")
            try:
                declared_size = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                declared_size = 0
            if declared_size > max_bytes:
                raise _acquisition_error("INPUT_TOO_LARGE", "单张图片超过大小上限")
            with temp_path.open("wb") as handle:
                while True:
                    chunk = response.read(min(256 * 1024, max_bytes + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise _acquisition_error("INPUT_TOO_LARGE", "单张图片超过大小上限")
                    handle.write(chunk)
        with temp_path.open("rb") as handle:
            signature = handle.read(16)
        valid_signature = {
            "image/jpeg": signature.startswith(b"\xff\xd8\xff"),
            "image/png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/gif": signature.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
            "image/bmp": signature.startswith(b"BM"),
            "image/avif": signature[4:12] in {b"ftypavif", b"ftypavis"},
            "image/heic": signature[4:12] in {b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"},
            "image/heif": signature[4:12] in {b"ftypheif", b"ftypheim", b"ftypmif1"},
        }.get(content_type, False)
        if not valid_signature:
            raise _acquisition_error("INVALID_MEDIA", "图片响应的文件签名与声明类型不符")
        extension = mimetypes.guess_extension(content_type) or ".img"
        if extension == ".jpe":
            extension = ".jpg"
        output = output_stem.with_suffix(extension)
        temp_path.replace(output)
        return output, total
    except urllib.error.URLError as exc:
        temp_path.unlink(missing_ok=True)
        raise _acquisition_error("NETWORK_ERROR", "图片下载网络请求失败") from exc
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise _acquisition_error("NETWORK_ERROR", "图片下载或落盘失败") from exc
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


class DouyinContentAdapter:
    name = "douyin-content"

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_seconds: int = 120,
        profile_dir: Optional[Path] = None,
        max_images: int = 30,
        max_image_mb: float = 20,
        max_total_image_mb: float = 200,
    ) -> None:
        if max_images <= 0 or max_image_mb <= 0 or max_total_image_mb <= 0:
            raise ValueError("图片数量和大小上限必须大于 0")
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self.profile_dir = Path(profile_dir).expanduser().resolve() if profile_dir else None
        self.max_images = max_images
        self.max_image_bytes = int(max_image_mb * 1024 * 1024)
        self.max_total_image_bytes = int(max_total_image_mb * 1024 * 1024)

    def supports(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        return host in DOUYIN_HOSTS and bool(re.search(r"/note/\d+", parsed.path))

    def available(self) -> bool:
        try:
            __import__("playwright.sync_api")
            return True
        except ImportError:
            return False

    def _download_images(
        self, urls: Sequence[str], work_dir: Path, *, referer: str
    ) -> List[Path]:
        if len(urls) > self.max_images:
            raise _acquisition_error(
                "TOO_MANY_IMAGES", f"图集包含 {len(urls)} 张图片，超过 {self.max_images} 张上限"
            )
        image_dir = work_dir / "source-images"
        image_dir.mkdir(parents=True, exist_ok=True)
        paths: List[Path] = []
        total = 0
        for index, url in enumerate(urls, start=1):
            remaining = self.max_total_image_bytes - total
            if remaining <= 0:
                raise _acquisition_error("INPUT_TOO_LARGE", "图集图片合计大小超过上限")
            path, size = download_public_image(
                url,
                image_dir / f"{index:03d}",
                referer=referer,
                timeout_seconds=self.timeout_seconds,
                max_bytes=min(self.max_image_bytes, remaining),
            )
            total += size
            paths.append(path)
        return paths

    def acquire(self, url: str, work_dir: Path) -> Any:
        if not self.supports(url):
            raise _acquisition_error("UNSUPPORTED_SOURCE_TYPE", "该适配器只处理抖音 /note/ 图文来源")
        if not self.available():
            raise _acquisition_error("MISSING_DEPENDENCY", "抖音图文获取需要 Playwright 浏览器依赖")
        try:
            from .source_adapter import AcquiredSource, PlaywrightAdapter, validate_supported_url
        except ImportError:
            from source_adapter import AcquiredSource, PlaywrightAdapter, validate_supported_url

        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore

        work_dir.mkdir(parents=True, exist_ok=True)
        requested_source_id = _source_id_from_url(url)
        html_text = ""
        dom_snapshot: Optional[Dict[str, Any]] = None
        final_url = url
        try:
            with sync_playwright() as playwright:
                launcher = PlaywrightAdapter(
                    headless=self.headless,
                    timeout_seconds=self.timeout_seconds,
                    profile_dir=self.profile_dir,
                )
                context = None
                browser = None
                try:
                    context, browser = launcher._launch_context(playwright, PlaywrightError)
                    page = context.new_page()
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=self.timeout_seconds * 1000
                    )
                    if response is not None and response.status in {401, 407}:
                        raise _acquisition_error(
                            "AUTHENTICATION_REQUIRED", "抖音页面要求登录后才能读取"
                        )
                    page.wait_for_timeout(5000)
                    # Keep the browser window visible so the user can see the
                    # page and complete any challenge/login that may appear.
                    # Poll every few seconds; only proceed when the page looks
                    # like real content or the extended timeout expires.
                    if not self.headless:
                        verification_ok = wait_for_user_verification(
                            page,
                            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                            poll_seconds=DEFAULT_POLL_SECONDS,
                        )
                        if not verification_ok:
                            raise _acquisition_error(
                                "CHALLENGE_REQUIRED",
                                "抖音验证或登录未在等待时间内完成，请人工完成后重试",
                            )
                        # After the user completes verification the page navigates
                        # to real content.  Wait for that navigation to settle.
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        page.wait_for_timeout(3000)
                    final_url = page.url
                    validate_supported_url(final_url)
                    html_text = page.content()
                    dom_snapshot = page.evaluate(
                        """() => {
                          const root = document.querySelector('[data-e2e="note-detail"]');
                          const user = root && root.querySelector('[data-e2e="user-info"]');
                          const authorLink = user && Array.from(user.querySelectorAll('a')).find(
                            element => (element.innerText || '').trim()
                          );
                          const detail = user && user.nextElementSibling;
                          const detailText = detail ? (detail.innerText || '').trim() : '';
                          const publishedMatch = detailText.match(
                            /发布时间[：:]?\\s*([^\\n]+)/
                          );
                          const bodyText = detailText
                            .replace(/发布时间[：:]?\\s*[^\\n]+/, '')
                            .trim();
                          const player = root && root.querySelector('[data-e2e="player-container"]');
                          const rawImages = player
                            ? Array.from(player.querySelectorAll('.focusPanel img, img'))
                                .filter(img => img.naturalWidth >= 400 && img.naturalHeight >= 400)
                                .map(img => img.currentSrc || img.src || '')
                                .filter(value => /^https?:\\/\\//.test(value))
                            : [];
                          return {
                            source_id: (location.pathname.match(/\\/note\\/(\\d+)/) || [])[1] || '',
                            title: document.title.replace(/\\s*-\\s*抖音\\s*$/, '').trim(),
                            author: authorLink ? (authorLink.innerText || '').trim() : '',
                            published_at: publishedMatch ? publishedMatch[1].trim() : '',
                            body_text: bodyText,
                            image_urls: Array.from(new Set(rawImages)),
                          };
                        }"""
                    )
                finally:
                    if context is not None:
                        context.close()
                    if browser is not None:
                        browser.close()
        except Exception as exc:
            if getattr(exc, "error_type", ""):
                raise
            if isinstance(exc, (PlaywrightError, OSError)):
                raise _acquisition_error("BROWSER_FAILED", f"抖音图文浏览器获取失败：{exc}") from exc
            raise

        normalized = parse_douyin_page(
            html_text,
            final_url,
            dom_snapshot=dom_snapshot,
            expected_source_id=requested_source_id,
        )
        if normalized.has_video and not normalized.image_urls:
            raise _acquisition_error("UNSUPPORTED_SOURCE_TYPE", "该抖音来源是视频，不属于图文适配范围")
        image_paths = self._download_images(normalized.image_urls, work_dir, referer=final_url)
        body_path = work_dir / "author-content.txt"
        body_path.write_text(normalized.body_text, encoding="utf-8")
        metadata_path = work_dir / "content.info.json"
        safe_metadata = asdict(normalized)
        safe_metadata.pop("image_urls", None)
        safe_metadata["image_paths"] = [str(path) for path in image_paths]
        metadata_path.write_text(
            json.dumps(safe_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return AcquiredSource(
            platform="douyin",
            input_url=url,
            canonical_url=final_url,
            source_id=normalized.source_id,
            title=normalized.title or "未命名抖音内容",
            author=normalized.author,
            duration=None,
            published_at=normalized.published_at,
            media_path=None,
            page_text_path=str(body_path),
            metadata_path=str(metadata_path),
            acquisition_method=self.name,
            content_kind=normalized.content_kind,
            body_text=normalized.body_text,
            image_paths=[str(path) for path in image_paths],
        )
