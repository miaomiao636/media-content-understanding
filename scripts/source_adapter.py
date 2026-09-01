#!/usr/bin/env python3
"""Cross-platform acquisition adapters for public Douyin and Bilibili content."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from .browser_verification import (
        DEFAULT_POLL_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
        wait_for_user_verification,
    )
except ImportError:
    from browser_verification import (
        DEFAULT_POLL_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
        wait_for_user_verification,
    )

try:
    from .sanitization import sanitize_error_text
except ImportError:
    from sanitization import sanitize_error_text

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
ALLOWED_HOSTS = {
    "douyin.com",
    "www.douyin.com",
    "m.douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "b23.tv",
}
MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".flv", ".m4a", ".mp3"}
SUBTITLE_EXTENSIONS = {".vtt", ".srt", ".ass", ".lrc"}
BROWSER_PROFILE_MARKER = ".media-content-understanding-browser-profile"
BROWSER_PROFILE_MARKER_CONTENT = "media-content-understanding browser profile v1\n"
BROWSER_PROFILE_FORBIDDEN_ENTRIES = {".git", "SKILL.md", "pyproject.toml"}


class AcquisitionError(RuntimeError):
    def __init__(self, error_type: str, message: str, *, adapter: str = ""):
        super().__init__(message)
        self.error_type = error_type
        self.adapter = adapter


def is_managed_browser_profile(path: Path) -> bool:
    marker = path / BROWSER_PROFILE_MARKER
    if marker.is_symlink() or not marker.is_file():
        return False
    try:
        return marker.read_text(encoding="utf-8") == BROWSER_PROFILE_MARKER_CONTENT
    except OSError:
        return False


def browser_profile_contains_project(path: Path) -> bool:
    return any((path / name).exists() for name in BROWSER_PROFILE_FORBIDDEN_ENTRIES)


@dataclass
class AcquisitionAttempt:
    adapter: str
    ok: bool
    error_type: str = ""
    message: str = ""


@dataclass
class AcquiredSource:
    platform: str
    input_url: str
    canonical_url: str
    source_id: str
    title: str
    author: str
    duration: Optional[float]
    published_at: str
    media_path: Optional[str]
    subtitle_paths: List[str] = field(default_factory=list)
    page_text_path: Optional[str] = None
    metadata_path: Optional[str] = None
    acquisition_method: str = ""
    attempts: List[AcquisitionAttempt] = field(default_factory=list)
    content_kind: str = "video"
    body_text: str = ""
    image_paths: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def _host_matches(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith("." + allowed)


def validate_supported_url(value: str) -> Tuple[str, str]:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise AcquisitionError("UNSUPPORTED_SOURCE", "只接受 http(s) 抖音或哔哩哔哩链接")
    if parsed.username or parsed.password:
        raise AcquisitionError("UNSAFE_URL", "链接不能包含用户名或密码")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not any(_host_matches(host, allowed) for allowed in ALLOWED_HOSTS):
        raise AcquisitionError("UNSUPPORTED_SOURCE", f"当前公共版只支持抖音和哔哩哔哩：{host}")
    if any(_host_matches(host, item) for item in {"douyin.com", "iesdouyin.com"}):
        platform = "douyin"
    else:
        platform = "bilibili"
    return platform, value.strip()


def resolve_share_url(value: str, timeout: int = 20) -> str:
    """Resolve short links while keeping the destination inside supported platforms."""
    validate_supported_url(value)
    parsed = urllib.parse.urlparse(value)
    if re.search(r"/(?:note|video)/(?:\d+|BV[0-9A-Za-z]+|av\d+)", parsed.path, re.IGNORECASE):
        return value.strip()
    request = urllib.request.Request(value, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code not in {400, 403, 405}:
            raise AcquisitionError("NETWORK_ERROR", f"短链接解析失败：HTTP {exc.code}") from exc
        request = urllib.request.Request(
            value,
            headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final_url = response.geturl()
        except (OSError, urllib.error.URLError) as inner:
            raise AcquisitionError("NETWORK_ERROR", f"短链接解析失败：{inner}") from inner
    except (OSError, urllib.error.URLError) as exc:
        raise AcquisitionError("NETWORK_ERROR", f"短链接解析失败：{exc}") from exc
    validate_supported_url(final_url)
    return final_url


def extract_source_id(platform: str, url: str, info: Optional[Dict[str, Any]] = None) -> str:
    if info and info.get("id"):
        return str(info["id"])
    if platform == "douyin":
        match = re.search(r"/(?:video|note)/(\d+)", url)
        return match.group(1) if match else "unknown"
    match = re.search(r"/(BV[0-9A-Za-z]+|av\d+)", url, flags=re.IGNORECASE)
    return match.group(1) if match else "unknown"


def classify_failure(text: str) -> str:
    lowered = text.lower()
    if "max-filesize" in lowered or "file is larger than" in lowered or "too large" in lowered:
        return "INPUT_TOO_LARGE"
    if "cookies" in lowered or "login" in lowered or "登录" in lowered:
        return "AUTHENTICATION_REQUIRED"
    if "captcha" in lowered or "verify" in lowered or "验证码" in lowered:
        return "CHALLENGE_REQUIRED"
    if "412" in lowered or "403" in lowered or "forbidden" in lowered:
        return "ACCESS_RESTRICTED"
    if "unsupported url" in lowered:
        return "UNSUPPORTED_SOURCE"
    if "timed out" in lowered or "timeout" in lowered:
        return "TIMEOUT"
    if "unable to download" in lowered or "network" in lowered or "connection" in lowered:
        return "NETWORK_ERROR"
    return "ACQUISITION_FAILED"


def find_outputs(work_dir: Path) -> Tuple[Optional[Path], List[Path], Optional[Path]]:
    files = [path for path in work_dir.iterdir() if path.is_file()]
    media = [path for path in files if path.suffix.lower() in MEDIA_EXTENSIONS]
    media = [path for path in media if not path.name.startswith("browser-audio")]
    media.sort(key=lambda path: path.stat().st_size, reverse=True)
    subtitles = sorted(path for path in files if path.suffix.lower() in SUBTITLE_EXTENSIONS)
    info_files = sorted(work_dir.glob("*.info.json"))
    return (media[0] if media else None, subtitles, info_files[0] if info_files else None)


class YtDlpAdapter:
    name = "yt-dlp"

    def __init__(
        self,
        *,
        cookie_browser: str = "",
        timeout_seconds: int = 900,
        max_download_mb: float = 2048,
    ):
        if max_download_mb <= 0:
            raise ValueError("max_download_mb 必须大于 0")
        self.cookie_browser = cookie_browser.strip()
        self.timeout_seconds = timeout_seconds
        self.max_bytes = int(max_download_mb * 1024 * 1024)

    def available(self) -> bool:
        try:
            __import__("yt_dlp")
            return True
        except ImportError:
            return shutil.which("yt-dlp") is not None

    def supports(self, url: str) -> bool:
        platform, _ = validate_supported_url(url)
        return not (
            platform == "douyin"
            and bool(re.search(r"/note/\d+", urllib.parse.urlparse(url).path))
        )

    def _command(self) -> List[str]:
        try:
            __import__("yt_dlp")
            return [sys.executable, "-m", "yt_dlp"]
        except ImportError:
            executable = shutil.which("yt-dlp")
            if not executable:
                raise AcquisitionError("MISSING_DEPENDENCY", "未安装 yt-dlp", adapter=self.name)
            return [executable]

    def acquire(self, url: str, work_dir: Path) -> AcquiredSource:
        platform, _ = validate_supported_url(url)
        command = self._command() + [
            "--no-playlist",
            "--no-progress",
            "--no-warnings",
            "--write-info-json",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "zh-Hans,zh-CN,zh,en",
            "--convert-subs",
            "vtt",
            "--merge-output-format",
            "mp4",
            "--max-filesize",
            str(self.max_bytes),
            "--output",
            str(work_dir / "source.%(ext)s"),
        ]
        if self.cookie_browser:
            command.extend(["--cookies-from-browser", self.cookie_browser])
        command.append(url)
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AcquisitionError("TIMEOUT", "yt-dlp 获取超时", adapter=self.name) from exc
        detail = (completed.stderr or completed.stdout)[-3000:].strip()
        if completed.returncode:
            raise AcquisitionError(classify_failure(detail), detail or "yt-dlp 获取失败", adapter=self.name)

        media, subtitles, info_path = find_outputs(work_dir)
        if media is None:
            if classify_failure(detail) == "INPUT_TOO_LARGE":
                raise AcquisitionError("INPUT_TOO_LARGE", detail, adapter=self.name)
            raise AcquisitionError("MEDIA_NOT_FOUND", "yt-dlp 未产生媒体文件", adapter=self.name)
        if media.stat().st_size > self.max_bytes:
            raise AcquisitionError(
                "INPUT_TOO_LARGE",
                f"yt-dlp 获取的媒体超过大小上限 {self.max_bytes} 字节",
                adapter=self.name,
            )
        info: Dict[str, Any] = {}
        if info_path:
            try:
                info = json.loads(info_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                info = {}
        canonical = str(info.get("webpage_url") or info.get("original_url") or url)
        return AcquiredSource(
            platform=platform,
            input_url=url,
            canonical_url=canonical,
            source_id=extract_source_id(platform, canonical, info),
            title=str(info.get("title") or "未命名视频"),
            author=str(info.get("uploader") or info.get("creator") or ""),
            duration=float(info["duration"]) if info.get("duration") is not None else None,
            published_at=str(info.get("timestamp") or info.get("upload_date") or ""),
            media_path=str(media),
            subtitle_paths=[str(path) for path in subtitles],
            metadata_path=str(info_path) if info_path else None,
            acquisition_method=self.name,
        )


def _download_url(
    url: str,
    output: Path,
    *,
    headers: Dict[str, str],
    timeout: int,
    max_bytes: int,
    resolver: Optional[Callable[..., Sequence[Tuple[Any, ...]]]] = None,
    opener: Optional[Any] = None,
) -> Path:
    try:
        try:
            from .douyin_content_adapter import build_safe_image_opener, validate_public_media_url
        except ImportError:
            from douyin_content_adapter import build_safe_image_opener, validate_public_media_url

        validate_public_media_url(url, resolver=resolver)
        client = opener or build_safe_image_opener(resolver=resolver)
        if not bool(getattr(client, "mcu_dns_pinned", False)):
            raise AcquisitionError(
                "UNSAFE_MEDIA_URL",
                "自定义媒体下载器未声明使用已验证 IP 连接",
                adapter="playwright-browser",
            )
        request = urllib.request.Request(url, headers=headers)
        total = 0
        with client.open(request, timeout=timeout) as response, output.open("wb") as handle:
            validate_public_media_url(response.geturl(), resolver=resolver)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise AcquisitionError(
                        "INPUT_TOO_LARGE",
                        "浏览器捕获的媒体超过大小上限",
                        adapter="playwright-browser",
                    )
                handle.write(chunk)
        return output
    except AcquisitionError as exc:
        output.unlink(missing_ok=True)
        raise AcquisitionError(
            exc.error_type,
            str(exc),
            adapter="playwright-browser",
        ) from exc
    except Exception as exc:
        output.unlink(missing_ok=True)
        error_type = getattr(exc, "error_type", "NETWORK_ERROR")
        raise AcquisitionError(
            str(error_type),
            sanitize_error_text(exc),
            adapter="playwright-browser",
        ) from exc


def _probe_media(path: Path) -> Dict[str, Any]:
    """Inspect real media streams instead of trusting HTTP MIME types."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise AcquisitionError("MISSING_DEPENDENCY", "未找到 ffprobe，无法验证浏览器捕获的媒体流")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,duration:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise AcquisitionError("INVALID_MEDIA", f"媒体流校验失败：{completed.stderr[-500:].strip()}")
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AcquisitionError("INVALID_MEDIA", "ffprobe 未返回有效 JSON") from exc
    streams = raw.get("streams") or []
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    duration_values: List[float] = []
    for value in [raw.get("format", {}).get("duration"), *(item.get("duration") for item in streams)]:
        try:
            duration_values.append(float(value))
        except (TypeError, ValueError):
            continue
    resolution = max(
        (int(item.get("width") or 0) * int(item.get("height") or 0) for item in video_streams),
        default=0,
    )
    return {
        "has_video": bool(video_streams),
        "has_audio": bool(audio_streams),
        "duration": max(duration_values, default=0.0),
        "resolution": resolution,
        "streams": [
            {
                "type": str(item.get("codec_type") or ""),
                "codec": str(item.get("codec_name") or ""),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
            }
            for item in streams
        ],
    }


def _declared_duration(page_text: str, element_durations: Sequence[float]) -> float:
    """Estimate the source duration without treating arbitrary comment timestamps as authoritative."""
    durations = [float(value) for value in element_durations if value and value > 0]
    # Douyin renders the active player as ``current / total``. The total is much
    # safer than taking the maximum timestamp from comments or chapter lists.
    for match in re.finditer(r"\b\d{1,2}:[0-5]\d\s*/\s*(\d{1,2}):([0-5]\d)\b", page_text):
        durations.append(int(match.group(1)) * 60 + int(match.group(2)))
    return max(durations, default=0.0)


class PlaywrightAdapter:
    """Optional real-browser fallback. It never imports browser cookies automatically."""

    name = "playwright-browser"

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_seconds: int = 120,
        max_download_mb: int = 2048,
        profile_dir: Optional[Path] = None,
    ):
        if max_download_mb <= 0:
            raise ValueError("max_download_mb 必须大于 0")
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self.max_bytes = int(max_download_mb * 1024 * 1024)
        self.profile_dir = Path(profile_dir).expanduser().resolve() if profile_dir else None

    def available(self) -> bool:
        try:
            __import__("playwright.sync_api")
            return True
        except ImportError:
            return False

    def supports(self, url: str) -> bool:
        platform, _ = validate_supported_url(url)
        return not (
            platform == "douyin"
            and bool(re.search(r"/note/\d+", urllib.parse.urlparse(url).path))
        )

    def _prepare_profile_dir(self) -> None:
        if self.profile_dir is None:
            return
        if self.profile_dir.is_symlink():
            raise AcquisitionError(
                "UNSAFE_BROWSER_PROFILE",
                "专用浏览器档案目录不能是符号链接",
                adapter=self.name,
            )
        if self.profile_dir.exists() and not self.profile_dir.is_dir():
            raise AcquisitionError(
                "UNSAFE_BROWSER_PROFILE",
                "专用浏览器档案路径不是目录",
                adapter=self.name,
            )
        if self.profile_dir.exists():
            if browser_profile_contains_project(self.profile_dir):
                raise AcquisitionError(
                    "UNSAFE_BROWSER_PROFILE",
                    "专用浏览器档案目录不能是项目或代码仓库",
                    adapter=self.name,
                )
            has_entries = any(self.profile_dir.iterdir())
            if has_entries and not is_managed_browser_profile(self.profile_dir):
                raise AcquisitionError(
                    "UNSAFE_BROWSER_PROFILE",
                    "专用浏览器档案目录不是由本 Skill 管理的空目录或已标记目录",
                    adapter=self.name,
                )
        else:
            self.profile_dir.mkdir(parents=True, mode=0o700)
        marker = self.profile_dir / BROWSER_PROFILE_MARKER
        if not marker.exists():
            marker.write_text(BROWSER_PROFILE_MARKER_CONTENT, encoding="utf-8")
        if os.name != "nt":
            self.profile_dir.chmod(0o700)

    @staticmethod
    def _profile_is_in_use(message: str) -> bool:
        lowered = message.lower()
        return any(
            token in lowered
            for token in (
                "processsingleton",
                "singletonlock",
                "user data directory is already in use",
                "profile is already in use",
            )
        )

    def _launch_context(self, playwright: Any, playwright_error: Any) -> Tuple[Any, Optional[Any]]:
        if self.profile_dir is not None:
            self._prepare_profile_dir()
            arguments = {
                "user_data_dir": str(self.profile_dir),
                "headless": self.headless,
                "user_agent": USER_AGENT,
            }
            try:
                context = playwright.chromium.launch_persistent_context(channel="chrome", **arguments)
            except playwright_error as exc:
                if self._profile_is_in_use(str(exc)):
                    raise AcquisitionError(
                        "BROWSER_PROFILE_IN_USE",
                        "专用浏览器档案正被另一个任务占用；请等待该任务结束后重试",
                        adapter=self.name,
                    ) from exc
                try:
                    context = playwright.chromium.launch_persistent_context(**arguments)
                except playwright_error as fallback_exc:
                    if self._profile_is_in_use(str(fallback_exc)):
                        raise AcquisitionError(
                            "BROWSER_PROFILE_IN_USE",
                            "专用浏览器档案正被另一个任务占用；请等待该任务结束后重试",
                            adapter=self.name,
                        ) from fallback_exc
                    raise
            return context, None

        try:
            browser = playwright.chromium.launch(headless=self.headless, channel="chrome")
        except playwright_error:
            browser = playwright.chromium.launch(headless=self.headless)
        return browser.new_context(user_agent=USER_AGENT), browser

    @staticmethod
    def _candidate_score(item: Dict[str, Any], kind: str) -> int:
        mime = str(item.get("mime") or "").lower()
        url = str(item.get("url") or "").lower()
        score = 0
        if kind in mime:
            score += 100
        if kind in url:
            score += 20
        if any(token in url for token in ("play", "video", "audio", "media")):
            score += 10
        try:
            score += min(int(item.get("size") or 0) // (1024 * 1024), 1000)
        except (TypeError, ValueError):
            pass
        if url.startswith("blob:"):
            score -= 1000
        return score

    @staticmethod
    def _cookie_headers_for_candidates(context: Any, urls: Sequence[str]) -> Dict[str, str]:
        """Ask the browser for only the cookies applicable to each candidate URL."""
        result: Dict[str, str] = {}
        for url in dict.fromkeys(str(value) for value in urls):
            if not url.startswith(("http://", "https://")):
                continue
            rows = context.cookies(url)
            pairs = []
            for item in rows:
                name = str(item.get("name") or "")
                value = str(item.get("value") or "")
                if not name or not value or any(token in name + value for token in ("\r", "\n")):
                    continue
                pairs.append(f"{name}={value}")
            if pairs:
                result[url] = "; ".join(pairs)
        return result

    def acquire(self, url: str, work_dir: Path) -> AcquiredSource:
        platform, _ = validate_supported_url(url)
        if not self.available():
            raise AcquisitionError(
                "MISSING_DEPENDENCY",
                "未安装 Playwright；运行 `pip install -e .[browser]` 并执行 `playwright install chromium`",
                adapter=self.name,
            )
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore

        candidates: List[Dict[str, Any]] = []
        page_text = ""
        title = ""
        final_url = url
        cookie_headers: Dict[str, str] = {}
        element_durations: List[float] = []
        user_agent = USER_AGENT
        try:
            with sync_playwright() as playwright:
                context = None
                browser = None
                try:
                    context, browser = self._launch_context(playwright, PlaywrightError)
                    page = context.new_page()

                    def collect(response: Any) -> None:
                        try:
                            response_headers = response.headers
                            mime = str(response_headers.get("content-type") or "")
                            resource_type = str(response.request.resource_type or "")
                            candidate_url = str(response.url or "")
                            content_range = str(response_headers.get("content-range") or "")
                            content_length = str(response_headers.get("content-length") or "0")
                            size_match = re.search(r"/(\d+)$", content_range)
                            size = int(size_match.group(1)) if size_match else int(content_length or 0)
                            if resource_type == "media" or mime.startswith(("video/", "audio/")):
                                candidates.append(
                                    {
                                        "url": candidate_url,
                                        "mime": mime,
                                        "size": size,
                                        "resource_type": resource_type,
                                    }
                                )
                        except Exception:
                            return

                    page.on("response", collect)
                    page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_seconds * 1000)
                    page.wait_for_timeout(3000)
                    for element in page.locator("video").all():
                        try:
                            element.evaluate(
                                "el => { el.muted = true; el.play().catch(() => {}); return true; }"
                            )
                        except PlaywrightError:
                            continue
                    page.wait_for_timeout(15000)
                    # Keep the browser visible for user verification if needed.
                    if not self.headless:
                        verification_ok = wait_for_user_verification(
                            page,
                            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                            poll_seconds=DEFAULT_POLL_SECONDS,
                        )
                        if not verification_ok:
                            raise AcquisitionError(
                                "CHALLENGE_REQUIRED",
                                "浏览器验证或登录未在等待时间内完成，请人工完成后重试",
                                adapter=self.name,
                            )
                        # Wait for any post-verification navigation to settle.
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        page.wait_for_timeout(3000)
                    final_url = page.url
                    validate_supported_url(final_url)
                    title = page.title()
                    for selector in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
                        try:
                            value = page.locator(selector).first.get_attribute("content", timeout=1000)
                            if value:
                                title = value.strip()
                                break
                        except PlaywrightError:
                            continue
                    try:
                        page_text = page.locator("body").inner_text(timeout=5000)
                    except PlaywrightError:
                        page_text = ""
                    for element in page.locator("video, audio").all():
                        try:
                            item = element.evaluate(
                                "el => ({url: el.currentSrc || el.src || '', "
                                "mime: el.tagName.toLowerCase(), duration: Number(el.duration || 0)})"
                            )
                            if item and item.get("url"):
                                candidates.append(
                                    {"url": str(item["url"]), "mime": str(item.get("mime") or ""), "size": 0}
                                )
                            try:
                                duration = float(item.get("duration") or 0)
                                if duration > 0:
                                    element_durations.append(duration)
                            except (TypeError, ValueError, AttributeError):
                                pass
                        except PlaywrightError:
                            continue
                    cookie_headers = self._cookie_headers_for_candidates(
                        context,
                        [str(item.get("url") or "") for item in candidates],
                    )
                    user_agent = page.evaluate("navigator.userAgent") or USER_AGENT
                finally:
                    if context is not None:
                        try:
                            context.close()
                        except PlaywrightError:
                            pass
                    if browser is not None:
                        try:
                            browser.close()
                        except PlaywrightError:
                            pass
        except (PlaywrightError, OSError) as exc:
            raise AcquisitionError("BROWSER_FAILED", f"浏览器获取失败：{exc}", adapter=self.name) from exc

        deduped: List[Dict[str, Any]] = []
        seen: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            candidate_url = item.get("url", "")
            if not candidate_url.startswith(("http://", "https://")):
                continue
            identity = urllib.parse.urlsplit(candidate_url)._replace(query="").geturl()
            previous = seen.get(identity)
            if previous is None or int(item.get("size") or 0) > int(previous.get("size") or 0):
                seen[identity] = item
        deduped.extend(seen.values())
        # CDNs may expose the same stream through multiple signed URLs. A stable
        # MIME/length pair avoids downloading exact duplicates in one capture.
        unique_streams: List[Dict[str, Any]] = []
        stream_fingerprints: set[Tuple[str, int]] = set()
        for item in deduped:
            size = int(item.get("size") or 0)
            fingerprint = (str(item.get("mime") or "").lower(), size)
            if size >= 1024 * 1024 and fingerprint in stream_fingerprints:
                continue
            if size >= 1024 * 1024:
                stream_fingerprints.add(fingerprint)
            unique_streams.append(item)
        deduped = unique_streams
        diagnostics: List[Dict[str, Any]] = [
            {
                "mime": str(item.get("mime") or ""),
                "size": int(item.get("size") or 0),
                "resource_type": str(item.get("resource_type") or ""),
                "video_score": self._candidate_score(item, "video"),
                "audio_score": self._candidate_score(item, "audio"),
            }
            for item in deduped
        ]
        ranked = sorted(
            enumerate(deduped),
            key=lambda pair: (
                max(self._candidate_score(pair[1], "video"), self._candidate_score(pair[1], "audio")),
                int(pair[1].get("size") or 0),
            ),
            reverse=True,
        )
        if not ranked:
            raise AcquisitionError("MEDIA_NOT_FOUND", "浏览器没有捕获到可下载的视频流", adapter=self.name)

        base_headers = {"User-Agent": user_agent, "Referer": final_url}
        downloaded: List[Dict[str, Any]] = []
        exceeded_limit = False
        maximum_size = max((int(item.get("size") or 0) for item in deduped), default=0)
        for candidate_index, item in ranked:
            size = int(item.get("size") or 0)
            # Keep a few unknown-size DOM sources, but skip tiny preview chunks
            # when long, complete streams are already visible.
            if size and maximum_size >= 1024 * 1024 and size < max(1024 * 1024, maximum_size // 20):
                continue
            mime = str(item.get("mime") or "").split(";", 1)[0]
            extension = mimetypes.guess_extension(mime) or ".mp4"
            candidate_path = work_dir / f"browser-candidate-{candidate_index + 1:03d}{extension}"
            candidate_url = str(item["url"])
            headers = dict(base_headers)
            cookie_header = cookie_headers.get(candidate_url)
            if cookie_header:
                headers["Cookie"] = cookie_header
            try:
                _download_url(
                    candidate_url,
                    candidate_path,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    max_bytes=self.max_bytes,
                )
                probe = _probe_media(candidate_path)
            except (AcquisitionError, OSError) as exc:
                if isinstance(exc, AcquisitionError) and exc.error_type == "INPUT_TOO_LARGE":
                    exceeded_limit = True
                diagnostics[candidate_index]["download_error"] = str(exc)[-500:]
                candidate_path.unlink(missing_ok=True)
                continue
            diagnostics[candidate_index]["probe"] = probe
            downloaded.append({"path": candidate_path, "probe": probe, "size": candidate_path.stat().st_size})
            if len(downloaded) >= 6:
                break

        (work_dir / "browser-candidates.json").write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        videos = [item for item in downloaded if item["probe"]["has_video"]]
        if not videos:
            if exceeded_limit:
                raise AcquisitionError(
                    "INPUT_TOO_LARGE",
                    f"浏览器捕获的媒体超过大小上限 {self.max_bytes} 字节",
                    adapter=self.name,
                )
            raise AcquisitionError("MEDIA_NOT_FOUND", "捕获到的候选文件中没有真实视频轨道", adapter=self.name)
        video_item = max(
            videos,
            key=lambda item: (item["probe"]["duration"], item["probe"]["resolution"], item["size"]),
        )
        output_path = video_item["path"]
        final_probe = video_item["probe"]
        if not final_probe["has_audio"]:
            audios = [item for item in downloaded if item["probe"]["has_audio"] and item is not video_item]
            if audios:
                audio_item = max(audios, key=lambda item: (item["probe"]["duration"], item["size"]))
                ffmpeg = shutil.which("ffmpeg")
                if ffmpeg:
                    merged = work_dir / "source.mp4"
                    completed = subprocess.run(
                        [
                            ffmpeg,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-y",
                            "-i",
                            str(video_item["path"]),
                            "-i",
                            str(audio_item["path"]),
                            "-c",
                            "copy",
                            "-shortest",
                            str(merged),
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )
                    if completed.returncode == 0:
                        output_path = merged
                        final_probe = _probe_media(merged)

        if output_path.stat().st_size > self.max_bytes:
            raise AcquisitionError(
                "INPUT_TOO_LARGE",
                f"浏览器获取的最终媒体超过大小上限 {self.max_bytes} 字节",
                adapter=self.name,
            )

        expected_duration = _declared_duration(page_text, element_durations)
        actual_duration = float(final_probe.get("duration") or 0)
        if expected_duration >= 30 and actual_duration < expected_duration * 0.8:
            raise AcquisitionError(
                "INCOMPLETE_MEDIA",
                f"只捕获到 {actual_duration:.1f} 秒媒体，页面显示约 {expected_duration:.1f} 秒；"
                "可将 acquisition.browser_headless 设为 false 后重试",
                adapter=self.name,
            )

        page_text_path = work_dir / "page-text.txt"
        page_text_path.write_text(page_text, encoding="utf-8")
        return AcquiredSource(
            platform=platform,
            input_url=url,
            canonical_url=final_url,
            source_id=extract_source_id(platform, final_url),
            title=title or "未命名视频",
            author="",
            duration=actual_duration or None,
            published_at="",
            media_path=str(output_path),
            page_text_path=str(page_text_path),
            acquisition_method=self.name,
        )


class SourceRouter:
    def __init__(self, adapters: Sequence[Any]):
        self.adapters = list(adapters)

    def acquire(self, url: str, work_dir: Path) -> AcquiredSource:
        platform, input_url = validate_supported_url(url)
        work_dir.mkdir(parents=True, exist_ok=True)
        canonical = resolve_share_url(input_url)
        attempts: List[AcquisitionAttempt] = []
        for adapter in self.adapters:
            supports = getattr(adapter, "supports", None)
            if callable(supports) and not supports(canonical):
                continue
            if not adapter.available():
                attempts.append(
                    AcquisitionAttempt(
                        adapter=adapter.name, ok=False, error_type="MISSING_DEPENDENCY", message="不可用"
                    )
                )
                continue
            try:
                result = adapter.acquire(canonical, work_dir)
                attempts.append(
                    AcquisitionAttempt(adapter=result.acquisition_method or adapter.name, ok=True)
                )
                result.input_url = input_url
                result.platform = platform
                result.attempts = attempts
                return result
            except AcquisitionError as exc:
                attempts.append(
                    AcquisitionAttempt(
                        adapter=exc.adapter or adapter.name,
                        ok=False,
                        error_type=exc.error_type,
                        message=sanitize_error_text(exc)[-1000:],
                    )
                )
                if (exc.adapter or adapter.name) == "douyin-content":
                    raise AcquisitionError(
                        exc.error_type,
                        sanitize_error_text(exc),
                        adapter=exc.adapter or adapter.name,
                    ) from exc
        detail = "; ".join(f"{item.adapter}:{item.error_type}" for item in attempts)
        if (
            not attempts
            and platform == "douyin"
            and re.search(r"/note/\d+", urllib.parse.urlparse(canonical).path)
        ):
            raise AcquisitionError(
                "CONTENT_ADAPTER_DISABLED",
                "抖音图文适配器未启用；请开启 acquisition.browser_fallback 并安装 Playwright",
                adapter="douyin-content",
            )
        raise AcquisitionError("ALL_ADAPTERS_FAILED", f"所有来源适配器均失败：{detail}")


def default_adapters(config: Optional[Dict[str, Any]] = None) -> List[Any]:
    acquisition = (config or {}).get("acquisition", {}) if isinstance(config, dict) else {}
    cookie_browser = str(acquisition.get("cookie_browser") or "")
    raw_profile_dir = str(acquisition.get("browser_profile_dir") or "").strip()
    profile_dir = Path(raw_profile_dir).expanduser().resolve() if raw_profile_dir else None
    max_download_mb = float(acquisition.get("max_download_mb", 2048))
    if max_download_mb <= 0:
        raise ValueError("acquisition.max_download_mb 必须大于 0")
    douyin_content_adapter: Optional[Any] = None
    if acquisition.get("browser_fallback", True):
        try:
            from .douyin_content_adapter import DouyinContentAdapter
        except ImportError:
            from douyin_content_adapter import DouyinContentAdapter

        douyin_content_adapter = DouyinContentAdapter(
            headless=bool(acquisition.get("browser_headless", False)),
            profile_dir=profile_dir,
        )
    adapters: List[Any] = []
    if douyin_content_adapter is not None:
        adapters.append(douyin_content_adapter)
    adapters.append(YtDlpAdapter(cookie_browser=cookie_browser, max_download_mb=max_download_mb))
    if acquisition.get("browser_fallback", True):
        adapters.append(
            PlaywrightAdapter(
                headless=bool(acquisition.get("browser_headless", False)),
                max_download_mb=max_download_mb,
                profile_dir=profile_dir,
            )
        )
    return adapters
