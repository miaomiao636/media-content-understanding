#!/usr/bin/env python3
"""Ordered, fail-safe routing across provider-specific vision APIs."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .config_loader import load_config, provider_value
    from .console import configure_utf8_stdio
    from .credential_store import CredentialError, resolve_api_key
except ImportError:
    from config_loader import load_config, provider_value
    from console import configure_utf8_stdio
    from credential_store import CredentialError, resolve_api_key

configure_utf8_stdio()

SUGGESTIONS = {
    "CONFIGURATION_ERROR": "核对模型名、Base URL、请求配置、适配器和配置字段。",
    "AUTHENTICATION_ERROR": "在本机钥匙串或对应环境变量中更新 API Key，不要把密钥发到聊天。",
    "PERMISSION_ERROR": "检查账户授权、模型白名单、项目权限和服务区域。",
    "RATE_LIMITED": "等待额度恢复、降低并发或切换其他视觉模型。",
    "TIMEOUT": "减少帧数或媒体尺寸、延长超时，或者切换响应更快的模型。",
    "NETWORK_ERROR": "检查网络、代理、DNS 和 Base URL 是否可访问。",
    "SERVER_ERROR": "服务商暂时异常；稍后重试或切换其他 provider。",
    "INPUT_TOO_LARGE": "减少关键帧、分批分析、压缩媒体或改用公网 URL。",
    "UNSUPPORTED_MEDIA": "转换为受支持的图片/视频格式，或改为抽取关键帧。",
    "CONTENT_POLICY": "检查内容范围；如内容合规，可切换到获准处理该内容的模型。",
    "INVALID_RESPONSE": "使用结构化提示重试一次；仍无效则切换模型。",
    "UNKNOWN_ERROR": "查看脱敏错误详情和服务商状态，再决定是否停用该模型。",
}
RETRYABLE = {"RATE_LIMITED", "TIMEOUT", "NETWORK_ERROR", "SERVER_ERROR", "INVALID_RESPONSE"}
SUPPORTED_PROFILES = {"standard", "qwen-omni", "xiaomi-mimo"}
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-matroska",
    "video/x-msvideo",
    "video/x-flv",
    "video/x-ms-wmv",
}
CONFIDENCE_PATTERN = re.compile(
    r"<!--\s*MCU_CONFIDENCE\s*:\s*(high|medium|low)\s*-->\s*$", re.IGNORECASE
)


class VisionCallError(Exception):
    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type


@dataclass
class MediaInput:
    kind: str
    images: List[Path]
    video_path: Optional[Path] = None
    video_url: Optional[str] = None


@dataclass
class ProviderResult:
    text: str
    model: Optional[str]
    usage: Optional[Dict[str, Any]]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_http(status: int, body: str) -> str:
    lowered = body.lower()
    if status == 401:
        return "AUTHENTICATION_ERROR"
    if status == 403:
        return "PERMISSION_ERROR"
    if status == 429:
        return "RATE_LIMITED"
    if status == 413 or "too large" in lowered or "context length" in lowered:
        return "INPUT_TOO_LARGE"
    if status == 415 or "unsupported media" in lowered or "image format" in lowered:
        return "UNSUPPORTED_MEDIA"
    if "content policy" in lowered or "safety" in lowered or "moderation" in lowered:
        return "CONTENT_POLICY"
    if status >= 500:
        return "SERVER_ERROR"
    if status == 400:
        return "CONFIGURATION_ERROR"
    return "UNKNOWN_ERROR"


def sanitize_message(message: str, secrets: List[str]) -> str:
    cleaned = message
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = re.sub(
        r"(?i)(authorization|api[_-]?key|access[_-]?token|cookie)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        cleaned,
    )
    cleaned = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED_QUERY]", cleaned)
    return cleaned


def error_record(
    provider_id: str,
    error_type: str,
    message: str,
    attempt: int,
    secrets: List[str],
) -> Dict[str, Any]:
    return {
        "stage": "visual_analysis",
        "provider": provider_id,
        "type": error_type,
        "message": sanitize_message(message, secrets)[:1000],
        "suggestion": SUGGESTIONS.get(error_type, SUGGESTIONS["UNKNOWN_ERROR"]),
        "retryable": error_type in RETRYABLE,
        "attempt": attempt,
        "occurred_at": now_iso(),
    }


def encode_file_data_url(path: Path, allowed_mimes: set, encoded_limit_mb: float) -> str:
    if not path.is_file():
        raise VisionCallError("CONFIGURATION_ERROR", f"媒体文件不存在：{path}")
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if mime not in allowed_mimes:
        raise VisionCallError("UNSUPPORTED_MEDIA", f"不支持的媒体类型：{mime}")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    encoded_mb = len(data.encode("ascii")) / (1024 * 1024)
    if encoded_mb > encoded_limit_mb:
        raise VisionCallError(
            "INPUT_TOO_LARGE",
            f"Base64 媒体约 {encoded_mb:.1f} MB，超过 provider 限制 {encoded_limit_mb:.1f} MB",
        )
    return f"data:{mime};base64,{data}"


def encode_image(path: Path, provider: Dict[str, Any]) -> Dict[str, Any]:
    limit = float(provider.get("max_image_base64_mb", 20))
    return {
        "type": "image_url",
        "image_url": {"url": encode_file_data_url(path, IMAGE_MIMES, limit)},
    }


def confidence_from_text(text: str) -> Optional[str]:
    match = CONFIDENCE_PATTERN.search(text)
    return match.group(1).lower() if match else None


def strip_confidence_marker(text: str) -> str:
    return CONFIDENCE_PATTERN.sub("", text).strip()


def enforce_aggregate_upload_limit(content: List[Dict[str, Any]], limit_mb: float) -> None:
    """Limit the combined Base64 media payload in one request.

    Remote HTTP(S) URLs do not upload local bytes and therefore do not count here.
    Provider-specific limits remain per media item in ``encode_file_data_url``.
    """
    encoded_bytes = 0
    for item in content:
        if not isinstance(item, dict):
            continue
        media = item.get("image_url") or item.get("video_url")
        if not isinstance(media, dict):
            continue
        value = media.get("url")
        if isinstance(value, str) and value.startswith("data:"):
            encoded_bytes += len(value.encode("utf-8"))
    encoded_mb = encoded_bytes / (1024 * 1024)
    if encoded_mb > limit_mb:
        raise VisionCallError(
            "INPUT_TOO_LARGE",
            f"本次请求的 Base64 媒体合计约 {encoded_mb:.1f} MB，"
            f"超过 vision.max_upload_mb={limit_mb:.1f} MB",
        )


def validate_remote_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https", "data"}:
        raise VisionCallError("CONFIGURATION_ERROR", "视频 URL 必须使用 http、https 或 data 协议")
    return value


def video_data_url(media: MediaInput, provider: Dict[str, Any]) -> str:
    if media.video_url:
        value = validate_remote_url(media.video_url)
        if value.startswith("data:"):
            encoded_mb = len(value.encode("utf-8")) / (1024 * 1024)
            limit = float(provider.get("max_video_base64_mb", 10))
            if encoded_mb > limit:
                raise VisionCallError(
                    "INPUT_TOO_LARGE",
                    f"Base64 视频约 {encoded_mb:.1f} MB，超过 provider 限制 {limit:.1f} MB",
                )
        return value
    if not media.video_path:
        raise VisionCallError("CONFIGURATION_ERROR", "缺少视频输入")
    limit = float(provider.get("max_video_base64_mb", 10))
    return encode_file_data_url(media.video_path, VIDEO_MIMES, limit)


def build_content(
    provider: Dict[str, Any],
    prompt: str,
    media: MediaInput,
    max_upload_mb: Optional[float] = None,
) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []
    if media.kind in {"image", "multi_image"}:
        content.extend(encode_image(path, provider) for path in media.images)
    elif media.kind == "video":
        item: Dict[str, Any] = {
            "type": "video_url",
            "video_url": {"url": video_data_url(media, provider)},
        }
        if provider.get("request_profile") == "xiaomi-mimo":
            item["fps"] = float(provider.get("video_fps", 2))
            item["media_resolution"] = str(provider.get("media_resolution", "default"))
        content.append(item)
    else:
        raise VisionCallError("CONFIGURATION_ERROR", f"不支持的输入类型：{media.kind}")
    if max_upload_mb is not None:
        enforce_aggregate_upload_limit(content, max_upload_mb)
    content.append({"type": "text", "text": prompt})
    return content


def prepare_request(
    provider: Dict[str, Any],
    prompt: str,
    media: MediaInput,
    api_key: str,
    max_upload_mb: Optional[float] = None,
) -> Tuple[Dict[str, str], Dict[str, Any], bool]:
    if provider.get("adapter") != "openai-compatible":
        raise VisionCallError("CONFIGURATION_ERROR", f"不支持 adapter：{provider.get('adapter')}")
    profile = str(provider.get("request_profile") or "standard")
    if profile not in SUPPORTED_PROFILES:
        raise VisionCallError("CONFIGURATION_ERROR", f"不支持 request_profile：{profile}")
    model = str(provider.get("model") or "")
    if not model:
        raise VisionCallError("CONFIGURATION_ERROR", "缺少 model")

    content = build_content(provider, prompt, media, max_upload_mb)
    messages = [{"role": "user", "content": content}]
    headers = {"Content-Type": "application/json"}
    max_tokens = int(provider.get("max_output_tokens", 2000))

    if profile == "qwen-omni":
        headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "messages": messages,
            "modalities": ["text"],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        return headers, body, True

    if profile == "xiaomi-mimo":
        headers["api-key"] = api_key
        body = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": str(provider.get("thinking", "disabled"))},
        }
        return headers, body, False

    headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    return headers, body, False


def extract_content(payload: Dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionCallError("INVALID_RESPONSE", f"响应缺少 choices[0].message.content：{exc}")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = "\n".join(
            str(item.get("text", "")).strip() for item in content if isinstance(item, dict)
        ).strip()
    else:
        text = ""
    if not text:
        raise VisionCallError("INVALID_RESPONSE", "视觉模型返回空内容")
    return text


def parse_sse(response: Any) -> ProviderResult:
    parts: List[str] = []
    model: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        model = model or payload.get("model")
        if isinstance(payload.get("usage"), dict):
            usage = payload["usage"]
        choices = payload.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str):
                parts.append(content)
    text = "".join(parts).strip()
    if not text:
        raise VisionCallError("INVALID_RESPONSE", "流式响应中没有有效文本")
    return ProviderResult(text=text, model=model, usage=usage)


def call_provider(
    provider: Dict[str, Any],
    prompt: str,
    media: MediaInput,
    *,
    max_upload_mb: Optional[float] = None,
) -> ProviderResult:
    model = str(provider.get("model") or "")
    base_url = provider_value(provider, "base_url", "base_url_env").rstrip("/")
    if not model or not base_url:
        raise VisionCallError("CONFIGURATION_ERROR", "缺少 model 或 Base URL")
    try:
        api_key, _ = resolve_api_key(provider)
    except CredentialError as exc:
        raise VisionCallError("AUTHENTICATION_ERROR", str(exc)) from exc
    if not api_key:
        raise VisionCallError("AUTHENTICATION_ERROR", "API Key 未在环境变量或钥匙串中配置")

    endpoint = str(provider.get("endpoint_path") or "/chat/completions")
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    headers, body, streaming = prepare_request(
        provider, prompt, media, api_key, max_upload_mb=max_upload_mb
    )
    timeout_key = "video_timeout_seconds" if media.kind == "video" else "timeout_seconds"
    timeout = float(provider.get(timeout_key, provider.get("timeout_seconds", 60)))
    request = urllib.request.Request(
        base_url + endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if streaming:
                return parse_sse(response)
            payload = json.loads(response.read().decode("utf-8"))
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
            return ProviderResult(text=extract_content(payload), model=payload.get("model"), usage=usage)
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:1000]
        raise VisionCallError(classify_http(exc.code, body_text), f"HTTP {exc.code}: {body_text}")
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        error_type = "TIMEOUT" if isinstance(exc.reason, socket.timeout) else "NETWORK_ERROR"
        raise VisionCallError(error_type, reason)
    except (socket.timeout, TimeoutError) as exc:
        raise VisionCallError("TIMEOUT", str(exc))
    except json.JSONDecodeError as exc:
        raise VisionCallError("INVALID_RESPONSE", f"响应不是有效 JSON：{exc}")


def build_media_input(args: argparse.Namespace) -> MediaInput:
    images = [Path(item).expanduser().resolve() for item in (args.image or [])]
    kinds = int(bool(images)) + int(bool(args.video)) + int(bool(args.video_url))
    if kinds != 1:
        raise ValueError("必须且只能提供图片、一个本地视频或一个视频 URL")
    if images:
        return MediaInput(kind="multi_image" if len(images) > 1 else "image", images=images)
    if args.video:
        return MediaInput(kind="video", images=[], video_path=Path(args.video).expanduser().resolve())
    return MediaInput(kind="video", images=[], video_url=str(args.video_url))


def safe_usage(usage: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not usage:
        return None
    allowed = {"prompt_tokens", "completion_tokens", "total_tokens"}
    return {key: value for key, value in usage.items() if key in allowed}


def verification_prompt(original_prompt: str, primary_text: str) -> str:
    return (
        "你是第二视觉复核模型。请重新检查同一批媒体证据，核对并修正主模型输出。\n"
        "重点检查数字、专有名词、界面文字、操作顺序，以及事实与推断是否混淆。\n"
        "直接输出可替换主结果的完整中文答案，不要只写评语。\n"
        "最后必须单独追加以下三个标记之一：\n"
        "<!-- MCU_CONFIDENCE: high -->\n"
        "<!-- MCU_CONFIDENCE: medium -->\n"
        "<!-- MCU_CONFIDENCE: low -->\n\n"
        f"原任务：\n{original_prompt}\n\n主模型输出：\n{primary_text}"
    )


def _write_result(
    report: Dict[str, Any], text: str, output: Optional[str], report_path: Optional[str]
) -> None:
    if output:
        output_file = Path(output)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(strip_confidence_marker(text) + "\n", encoding="utf-8")
    if report_path:
        target = Path(report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _attempt_provider(
    provider: Dict[str, Any],
    prompt: str,
    media: MediaInput,
    report: Dict[str, Any],
    *,
    api_calls_limit: int,
    max_upload_mb: float,
) -> Optional[ProviderResult]:
    provider_id = str(provider.get("id") or "unnamed-provider")
    if provider_id not in report["attempted_providers"]:
        report["attempted_providers"].append(provider_id)
    max_retries = max(0, int(provider.get("max_retries", 1)))
    for attempt in range(1, max_retries + 2):
        if report["api_calls_used"] >= api_calls_limit:
            report["budget_exhausted"] = True
            return None
        report["api_calls_used"] += 1
        try:
            return call_provider(provider, prompt, media, max_upload_mb=max_upload_mb)
        except VisionCallError as exc:
            try:
                secret, _ = resolve_api_key(provider)
            except CredentialError:
                secret = ""
            report["errors"].append(
                error_record(provider_id, exc.error_type, str(exc), attempt, [secret])
            )
            secret = ""
            if exc.error_type not in RETRYABLE or attempt > max_retries:
                break
            time.sleep(min(2 ** (attempt - 1), 5))
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file")
    parser.add_argument("--image", action="append")
    parser.add_argument("--video")
    parser.add_argument("--video-url")
    parser.add_argument("--provider", help="只测试指定 provider")
    parser.add_argument("--max-api-calls", type=int, help="本次进程允许的 provider 尝试总数")
    parser.add_argument("--output")
    parser.add_argument("--report")
    args = parser.parse_args()

    try:
        config, config_path = load_config(args.config)
        prompt = args.prompt or Path(args.prompt_file).read_text(encoding="utf-8")
        media = build_media_input(args)
        configured_limit = int(config["vision"].get("max_visual_calls", 20))
        requested_limit = args.max_api_calls if args.max_api_calls is not None else configured_limit
        api_calls_limit = min(configured_limit, requested_limit)
        if api_calls_limit <= 0:
            raise ValueError("--max-api-calls 必须大于 0")
        max_upload_mb = float(config["vision"].get("max_upload_mb", 100))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid_input", "error": str(exc)}, ensure_ascii=False))
        return 2

    selected = []
    required_capability = media.kind
    for provider in config["vision"].get("providers", []):
        if not isinstance(provider, dict) or not provider.get("enabled", False):
            continue
        if args.provider and str(provider.get("id")) != args.provider:
            continue
        capabilities = set(provider.get("capabilities") or [])
        if required_capability == "image" and not capabilities.intersection({"image", "multi_image"}):
            continue
        if required_capability == "multi_image" and "multi_image" not in capabilities:
            continue
        if required_capability == "video" and "video" not in capabilities:
            continue
        selected.append(provider)
    selected.sort(key=lambda item: (int(item.get("priority", 100)), str(item.get("id", ""))))

    report: Dict[str, Any] = {
        "status": "external_not_attempted",
        "config_path": str(config_path) if config_path else None,
        "input_kind": media.kind,
        "required_capability": required_capability,
        "attempted_providers": [],
        "selected_provider": None,
        "selected_model": None,
        "usage": None,
        "confidence": None,
        "api_calls_limit": api_calls_limit,
        "api_calls_used": 0,
        "budget_exhausted": False,
        "verification": {
            "mode": str(config["vision"].get("verification_mode", "low-confidence")),
            "status": "not_evaluated",
        },
        "errors": [],
        "host_fallback_enabled": bool(config["vision"].get("host_fallback", True)),
        "created_at": now_iso(),
    }
    if not selected:
        report["status"] = "no_eligible_external_provider"
        if args.report:
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 20

    for index, provider in enumerate(selected):
        result = _attempt_provider(
            provider,
            prompt,
            media,
            report,
            api_calls_limit=api_calls_limit,
            max_upload_mb=max_upload_mb,
        )
        if result is None:
            if report["budget_exhausted"]:
                break
            continue

        provider_id = str(provider.get("id") or "unnamed-provider")
        final_result = result
        final_provider = provider
        confidence = confidence_from_text(result.text)
        report["verification"] = {
            "mode": str(config["vision"].get("verification_mode", "low-confidence")),
            "status": "not_required",
            "primary_provider": provider_id,
            "primary_model": result.model or provider.get("model"),
            "primary_confidence": confidence or "unknown",
        }
        if report["verification"]["mode"] == "low-confidence" and confidence == "low":
            report["verification"]["status"] = "requested"
            verifier_prompt = verification_prompt(prompt, strip_confidence_marker(result.text))
            for verifier in selected[index + 1 :]:
                verified = _attempt_provider(
                    verifier,
                    verifier_prompt,
                    media,
                    report,
                    api_calls_limit=api_calls_limit,
                    max_upload_mb=max_upload_mb,
                )
                if verified is not None:
                    final_result = verified
                    final_provider = verifier
                    report["verification"].update(
                        {
                            "status": "succeeded",
                            "provider": str(verifier.get("id") or "unnamed-provider"),
                            "model": verified.model or verifier.get("model"),
                        }
                    )
                    break
                if report["budget_exhausted"]:
                    report["verification"]["status"] = "budget_exhausted"
                    break
            else:
                report["verification"]["status"] = "no_successful_verifier"

        report["status"] = "external_success"
        report["selected_provider"] = str(final_provider.get("id") or "unnamed-provider")
        report["selected_model"] = final_result.model or final_provider.get("model")
        report["usage"] = safe_usage(final_result.usage)
        report["confidence"] = confidence_from_text(final_result.text) or "unknown"
        _write_result(report, final_result.text, args.output, args.report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if report["api_calls_used"] >= api_calls_limit:
        report["budget_exhausted"] = True
    report["status"] = "external_budget_exhausted" if report["budget_exhausted"] else "external_exhausted"
    _write_result(report, "", None, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 21


if __name__ == "__main__":
    raise SystemExit(main())
