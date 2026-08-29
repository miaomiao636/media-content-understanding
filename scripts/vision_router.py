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


def validate_remote_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https", "data"}:
        raise VisionCallError("CONFIGURATION_ERROR", "视频 URL 必须使用 http、https 或 data 协议")
    return value


def video_data_url(media: MediaInput, provider: Dict[str, Any]) -> str:
    if media.video_url:
        return validate_remote_url(media.video_url)
    if not media.video_path:
        raise VisionCallError("CONFIGURATION_ERROR", "缺少视频输入")
    limit = float(provider.get("max_video_base64_mb", 10))
    return encode_file_data_url(media.video_path, VIDEO_MIMES, limit)


def build_content(provider: Dict[str, Any], prompt: str, media: MediaInput) -> List[Dict[str, Any]]:
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
    content.append({"type": "text", "text": prompt})
    return content


def prepare_request(
    provider: Dict[str, Any],
    prompt: str,
    media: MediaInput,
    api_key: str,
) -> Tuple[Dict[str, str], Dict[str, Any], bool]:
    if provider.get("adapter") != "openai-compatible":
        raise VisionCallError("CONFIGURATION_ERROR", f"不支持 adapter：{provider.get('adapter')}")
    profile = str(provider.get("request_profile") or "standard")
    if profile not in SUPPORTED_PROFILES:
        raise VisionCallError("CONFIGURATION_ERROR", f"不支持 request_profile：{profile}")
    model = str(provider.get("model") or "")
    if not model:
        raise VisionCallError("CONFIGURATION_ERROR", "缺少 model")

    content = build_content(provider, prompt, media)
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


def call_provider(provider: Dict[str, Any], prompt: str, media: MediaInput) -> ProviderResult:
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
    headers, body, streaming = prepare_request(provider, prompt, media, api_key)
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
    parser.add_argument("--output")
    parser.add_argument("--report")
    args = parser.parse_args()

    try:
        config, config_path = load_config(args.config)
        prompt = args.prompt or Path(args.prompt_file).read_text(encoding="utf-8")
        media = build_media_input(args)
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

    for provider in selected:
        provider_id = str(provider.get("id") or "unnamed-provider")
        report["attempted_providers"].append(provider_id)
        max_retries = max(0, int(provider.get("max_retries", 1)))
        for attempt in range(1, max_retries + 2):
            try:
                result = call_provider(provider, prompt, media)
                report["status"] = "external_success"
                report["selected_provider"] = provider_id
                report["selected_model"] = result.model or provider.get("model")
                report["usage"] = safe_usage(result.usage)
                if args.output:
                    output_path = Path(args.output)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(result.text + "\n", encoding="utf-8")
                if args.report:
                    report_path = Path(args.report)
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0
            except VisionCallError as exc:
                try:
                    secret, _ = resolve_api_key(provider)
                except CredentialError:
                    secret = ""
                record = error_record(provider_id, exc.error_type, str(exc), attempt, [secret])
                secret = ""
                report["errors"].append(record)
                if exc.error_type not in RETRYABLE or attempt > max_retries:
                    break
                time.sleep(min(2 ** (attempt - 1), 5))

    report["status"] = "external_exhausted"
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 21


if __name__ == "__main__":
    raise SystemExit(main())
