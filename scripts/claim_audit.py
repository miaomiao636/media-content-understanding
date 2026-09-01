#!/usr/bin/env python3
"""Extract high-risk factual claims and compare a summary with source evidence."""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from .console import configure_utf8_stdio
except ImportError:
    from console import configure_utf8_stdio

configure_utf8_stdio()

CLAIM_TYPES = (
    "numeric_range",
    "amount",
    "percentage",
    "duration",
    "version",
    "model_or_software",
)

NUMBER = r"\d+(?:[,.]\d+)?"
ISO_DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}(?:-\d{2})?(?!\d)")
RANGE_RE = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<start>{NUMBER})\s*(?:-|~|–|—|至|到)\s*(?P<end>{NUMBER})"
    r"(?P<unit>\s*(?:元|万元|美元|刀|USD|CNY|RMB|%|％|秒|分钟|小时|天|周|个月|年))?",
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(
    rf"(?:(?P<prefix>[$¥€£])\s*(?P<pvalue>{NUMBER})|"
    rf"(?P<svalue>{NUMBER})\s*(?P<suffix>元|万元|美元|刀|USD|CNY|RMB))",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(rf"(?<![A-Za-z0-9_.])(?P<value>{NUMBER})\s*(?P<unit>%|％)")
DURATION_RE = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<value>{NUMBER})\s*(?P<unit>ms|毫秒|s|sec(?:ond)?s?|秒|"
    r"min(?:ute)?s?|分钟|h|hr|hour(?:s)?|小时|day(?:s)?|天|week(?:s)?|周|"
    r"month(?:s)?|个月|year(?:s)?|年)(?![\w])",
    re.IGNORECASE,
)
VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<value>v?\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9.-]+)?)",
    re.IGNORECASE,
)
TECH_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?P<value>(?=[A-Za-z0-9._+-]*[A-Za-z])"
    r"(?=[A-Za-z0-9._+-]*\d)[A-Za-z0-9][A-Za-z0-9._+-]{2,})"
    r"(?![A-Za-z0-9_.-])"
)

KNOWN_SOFTWARE = (
    "Adobe After Effects",
    "After Effects",
    "Adobe Premiere Pro",
    "Premiere Pro",
    "Final Cut Pro",
    "DaVinci Resolve",
    "Visual Studio Code",
    "Microsoft PowerPoint",
    "Microsoft Excel",
    "Microsoft Word",
    "Stable Diffusion",
    "Photoshop",
    "Illustrator",
    "Blender",
    "Figma",
    "Canva",
    "CapCut",
    "ChatGPT",
    "Midjourney",
    "PowerPoint",
    "DeepSeek",
    "ComfyUI",
    "Obsidian",
    "Gemini",
    "Claude",
    "Copilot",
    "Notion",
    "Cursor",
    "Excel",
    "Word",
    "Qwen",
    "Llama",
    "FFmpeg",
    "Python",
    "JavaScript",
    "TypeScript",
    "剪映",
)

UNIT_ALIASES = {
    "％": "%",
    "美元": "usd",
    "刀": "usd",
    "$": "usd",
    "¥": "cny",
    "元": "cny",
    "万元": "10k-cny",
    "rmb": "cny",
    "cny": "cny",
    "usd": "usd",
    "毫秒": "ms",
    "sec": "s",
    "second": "s",
    "seconds": "s",
    "秒": "s",
    "min": "min",
    "minute": "min",
    "minutes": "min",
    "分钟": "min",
    "hr": "h",
    "hour": "h",
    "hours": "h",
    "小时": "h",
    "day": "day",
    "days": "day",
    "天": "day",
    "week": "week",
    "weeks": "week",
    "周": "week",
    "month": "month",
    "months": "month",
    "个月": "month",
    "year": "year",
    "years": "year",
    "年": "year",
}


def _number(value: str) -> str:
    cleaned = value.replace(",", "")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned


def _unit(value: str) -> str:
    cleaned = value.strip().lower()
    return UNIT_ALIASES.get(cleaned, cleaned)


def _context(text: str, start: int, end: int, radius: int = 44) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - radius) : min(len(text), end + radius)]).strip()


def _claim_subject(claim_type: str, text: str, start: int, end: int) -> str:
    """Return a conservative nearby subject label without the claim value itself."""
    clause_start = max(
        (text.rfind(token, 0, start) for token in ("\n", "。", "！", "？", "；", ";", "，", ",")),
        default=-1,
    ) + 1
    clause_end_candidates = [
        position
        for token in ("\n", "。", "！", "？", "；", ";", "，", ",")
        if (position := text.find(token, end)) >= 0
    ]
    clause_end = min(clause_end_candidates, default=len(text))

    if claim_type == "version":
        nearby = text[clause_start:clause_end]
        candidates: List[Tuple[int, str]] = []
        for name in sorted(KNOWN_SOFTWARE, key=len, reverse=True):
            for match in re.finditer(
                rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                nearby,
                re.IGNORECASE,
            ):
                absolute_start = clause_start + match.start()
                absolute_end = clause_start + match.end()
                distance = min(abs(start - absolute_end), abs(absolute_start - end))
                candidates.append((distance, name.lower()))
        if candidates:
            return min(candidates, key=lambda item: item[0])[1]

    prefix = re.sub(r"^[\s#>*\-\d.、]+", "", text[clause_start:start]).strip()
    prefix = re.sub(r"\s+", " ", prefix)
    return prefix[-32:].lower()


def _claim(
    claim_type: str,
    raw: str,
    normalized: str,
    text: str,
    start: int,
    end: int,
    **fields: Any,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "type": claim_type,
        "raw": raw,
        "normalized": normalized,
        "context": _context(text, start, end),
        "start": start,
        "end": end,
    }
    subject = _claim_subject(claim_type, text, start, end)
    if subject:
        row["subject"] = subject
    row.update(fields)
    return row


def _overlaps(span: Tuple[int, int], occupied: Iterable[Tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in occupied)


def extract_claims(text: str) -> List[Dict[str, Any]]:
    """Return deterministic, JSON-safe claims in source order."""
    claims: List[Dict[str, Any]] = []
    occupied: List[Tuple[int, int]] = []
    date_spans = [match.span() for match in ISO_DATE_RE.finditer(text)]

    for match in RANGE_RE.finditer(text):
        if _overlaps(match.span(), date_spans):
            continue
        start_value = _number(match.group("start"))
        end_value = _number(match.group("end"))
        unit = _unit(match.group("unit") or "")
        normalized = f"{start_value}..{end_value}" + (f" {unit}" if unit else "")
        claims.append(
            _claim(
                "numeric_range",
                match.group(0),
                normalized,
                text,
                match.start(),
                match.end(),
                lower=start_value,
                upper=end_value,
                unit=unit,
            )
        )
        occupied.append(match.span())

    scalar_patterns = (
        ("amount", AMOUNT_RE),
        ("percentage", PERCENT_RE),
        ("duration", DURATION_RE),
    )
    for claim_type, pattern in scalar_patterns:
        for match in pattern.finditer(text):
            if _overlaps(match.span(), occupied):
                continue
            if claim_type == "amount":
                value = _number(match.group("pvalue") or match.group("svalue"))
                unit = _unit(match.group("prefix") or match.group("suffix"))
            else:
                value = _number(match.group("value"))
                unit = _unit(match.group("unit"))
            claims.append(
                _claim(
                    claim_type,
                    match.group(0),
                    f"{value} {unit}",
                    text,
                    match.start(),
                    match.end(),
                    value=value,
                    unit=unit,
                )
            )
            occupied.append(match.span())

    for match in VERSION_RE.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        raw = match.group("value")
        prefix = text[max(0, match.start() - 18) : match.start()].lower()
        # Model names such as qwen3.5 are handled as a whole tech token below.
        if prefix and re.search(r"[a-z][a-z0-9._+-]*$", prefix):
            continue
        normalized = raw.lower().lstrip("v")
        claims.append(
            _claim("version", raw, normalized, text, match.start(), match.end(), value=normalized)
        )
        occupied.append(match.span())

    for name in sorted(KNOWN_SOFTWARE, key=len, reverse=True):
        for match in re.finditer(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text, re.IGNORECASE
        ):
            if _overlaps(match.span(), occupied):
                continue
            claims.append(
                _claim(
                    "model_or_software",
                    match.group(0),
                    name.lower(),
                    text,
                    match.start(),
                    match.end(),
                    value=name.lower(),
                )
            )
            occupied.append(match.span())

    for match in TECH_TOKEN_RE.finditer(text):
        if _overlaps(match.span(), occupied):
            continue
        raw = match.group("value")
        normalized = raw.lower().rstrip(".")
        if normalized in {"http", "https", "utf-8"} or normalized.startswith(("00:", "01:")):
            continue
        # Skip file paths, domain names, and documentation references.
        if any(
            normalized.endswith(ext)
            for ext in (
                ".md", ".webp", ".json", ".py", ".txt", ".html", ".yml",
                ".yaml", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".css",
                ".js", ".swift", ".lock", ".toml", ".cfg", ".ini", ".mp4",
                ".mov", ".mkv", ".webm", ".flv", ".m4a", ".mp3", ".wav",
                ".srt", ".vtt", ".ass", ".lrc", ".csv", ".tsv", ".pdf",
                ".docx", ".pptx", ".xlsx", ".zip", ".tar", ".gz", ".whl",
            )
        ):
            continue
        if normalized.startswith(("www.", "http.", "media/")) or "/" in normalized:
            continue
        if normalized.startswith(("0:", "1:", "2:", "3:", "4:", "5:")):
            continue
        claims.append(
            _claim(
                "model_or_software",
                raw,
                normalized,
                text,
                match.start(),
                match.end(),
                value=normalized,
            )
        )
        occupied.append(match.span())

    return sorted(claims, key=lambda item: (item["start"], item["end"], item["type"]))


def _context_tokens(value: str) -> set[str]:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", value)
        if token.lower() not in {"summary", "transcript"}
    }
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens - {"核心", "结论", "关键", "参数", "视频"}


def _context_related(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_tokens = _context_tokens(left.get("context", ""))
    right_tokens = _context_tokens(right.get("context", ""))
    return bool(left_tokens & right_tokens)


def _subjects_related(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    left_subject = str(left.get("subject") or "")
    right_subject = str(right.get("subject") or "")
    if not left_subject or not right_subject:
        return _context_related(left, right)
    if left_subject == right_subject:
        return True
    return bool(_context_tokens(left_subject) & _context_tokens(right_subject))


def _same_subject(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    if left["type"] != right["type"]:
        return False
    claim_type = left["type"]
    if claim_type == "numeric_range":
        shared_boundary = left.get("lower") in {right.get("lower"), right.get("upper")} or left.get(
            "upper"
        ) in {right.get("lower"), right.get("upper")}
        same_unit = left.get("unit") == right.get("unit") or not left.get("unit") or not right.get("unit")
        return bool(shared_boundary and same_unit) or (_subjects_related(left, right) and same_unit)
    if claim_type in {"amount", "percentage", "duration"}:
        return left.get("unit") == right.get("unit") and _subjects_related(left, right)
    if claim_type == "version":
        return _subjects_related(left, right)
    if claim_type == "model_or_software":
        ratio = SequenceMatcher(None, left["normalized"], right["normalized"]).ratio()
        return ratio >= 0.62 or _context_related(left, right)
    return False


def _normalize_evidence_source(value: Any, source_index: int) -> Dict[str, Any]:
    if isinstance(value, dict):
        text = str(value.get("text") or "")
        source_type = str(value.get("source_type") or f"evidence_{source_index}")
        label = str(value.get("label") or source_type)
        raw_trust = value.get("trust", 100)
        trust = raw_trust if isinstance(raw_trust, int) and not isinstance(raw_trust, bool) else 100
    else:
        text = str(value)
        source_type = f"evidence_{source_index}"
        label = source_type
        trust = 100
    return {
        "text": text,
        "source_type": source_type,
        "label": label,
        "trust": max(0, min(100, trust)),
    }


def audit_claims(summary_text: str, evidence_texts: Sequence[Any]) -> Dict[str, Any]:
    """Compare summary claims with trusted evidence and identify severe contradictions."""
    summary_claims = extract_claims(summary_text)
    evidence_claims: List[Dict[str, Any]] = []
    evidence_sources = [
        _normalize_evidence_source(value, source_index)
        for source_index, value in enumerate(evidence_texts)
    ]
    for source_index, source in enumerate(evidence_sources):
        for claim in extract_claims(source["text"]):
            claim = dict(claim)
            claim["source_index"] = source_index
            claim["source_type"] = source["source_type"]
            claim["source_label"] = source["label"]
            claim["source_trust"] = source["trust"]
            evidence_claims.append(claim)

    supported: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    for claim in summary_claims:
        exact = [
            item
            for item in evidence_claims
            if item["type"] == claim["type"] and item["normalized"] == claim["normalized"]
        ]
        exact_source_indexes = {item["source_index"] for item in exact}
        contradictions = [
            item
            for item in evidence_claims
            if item["type"] == claim["type"]
            and item["normalized"] != claim["normalized"]
            and item["source_index"] not in exact_source_indexes
            and _same_subject(claim, item)
        ]
        if contradictions:
            conflicts.append(
                {
                    "severity": "severe",
                    "type": claim["type"],
                    "summary_claim": claim,
                    "supporting_evidence": exact,
                    "evidence_claims": contradictions,
                    "highest_conflicting_trust": max(
                        item.get("source_trust", 0) for item in contradictions
                    ),
                    "message": (
                        f"摘要中的 {claim['raw']} 与其他来源证据不一致；"
                        "低可信来源中的同值不能覆盖该冲突"
                    ),
                }
            )
        elif exact:
            supported.append(
                {
                    "summary_claim": claim,
                    "evidence_claim": max(
                        exact, key=lambda item: (item.get("source_trust", 0), -item["source_index"])
                    ),
                    "all_evidence_claims": exact,
                }
            )
        else:
            unsupported.append(
                {
                    "severity": "warning",
                    "summary_claim": claim,
                    "message": f"未在已提供证据中找到 {claim['raw']} 的对应项",
                }
            )

    counts = {
        claim_type: {
            "summary": sum(item["type"] == claim_type for item in summary_claims),
            "evidence": sum(item["type"] == claim_type for item in evidence_claims),
        }
        for claim_type in CLAIM_TYPES
    }
    return {
        "schema_version": "claim-audit/v1",
        "ok": not conflicts,
        "counts": counts,
        "evidence_sources": [
            {key: value for key, value in source.items() if key != "text"}
            for source in evidence_sources
        ],
        "summary_claims": summary_claims,
        "evidence_claims": evidence_claims,
        "supported": supported,
        "unsupported": unsupported,
        "conflicts": conflicts,
        "severe_conflict_count": len(conflicts),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="审计摘要中的高风险事实声明")
    parser.add_argument("--summary", required=True, help="摘要 Markdown 文件")
    parser.add_argument("--evidence", action="append", default=[], help="可重复的证据文件")
    args = parser.parse_args(argv)
    summary = Path(args.summary).read_text(encoding="utf-8")
    evidence = [Path(path).read_text(encoding="utf-8") for path in args.evidence]
    result = audit_claims(summary, evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
