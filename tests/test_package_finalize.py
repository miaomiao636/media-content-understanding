import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.mcu import build_parser
from scripts.package_tool import finalize_package, validate


def _summary(price_range: str) -> str:
    return f"""# 内容提炼

## 一分钟核心结论

方案的报价范围是 {price_range} 元，适合小团队使用。

## 解决的问题与适用场景

用来解决手工操作繁琐的问题，适用于需要重复处理的场景。

## 主题结构

先说明问题，再演示方案，最后总结限制。

## 可执行步骤与关键参数

先记录当前状态，然后按条件执行，最后核对结果。

## 视觉证据与证据作用

截图用于确认最终界面的字段与布局。

## 来源与推断、缺失信息

价格来自转写；适用场景是根据作者演示得出的推断。

## 复刻前仍需验证

复刻前需要再确认当前软件版本和实际报价。
"""


def _make_package(tmp_path: Path, summary_range: str = "300-3000") -> Path:
    package = tmp_path / "package"
    (package / "media" / "images").mkdir(parents=True)
    (package / "media" / "clips").mkdir()
    (package / "summary.md").write_text(_summary(summary_range), encoding="utf-8")
    (package / "source-content.md").write_text("# 来源\n\n作者介绍了一个自动化方案。\n", encoding="utf-8")
    (package / "transcript.md").write_text(
        "# 时间戳转写\n\n作者说：报价范围是 300-3000 元。\n", encoding="utf-8"
    )
    (package / "errors.json").write_text("[]\n", encoding="utf-8")
    (package / "media" / "images" / "001.jpg").write_bytes(b"image")
    manifest = {
        "schema_version": "1.0",
        "package_type": "media-analysis-package",
        "status": "partial",
        "source": {
            "input_url": "https://www.bilibili.com/video/BV1test",
            "platform": "bilibili",
            "source_id": "BV1test",
        },
        "content": {
            "kind": "video",
            "summary_file": "summary.md",
            "source_content_file": "source-content.md",
            "transcript_file": "transcript.md",
            "visual_evidence_required": True,
        },
        "media": [
            {
                "type": "image",
                "path": "media/images/001.jpg",
                "timestamp": "00:10",
                "reason": "展示最终界面",
                "description": "展示字段与布局",
            }
        ],
        "limitations": [],
        "processing": {},
        "errors_file": "errors.json",
    }
    (package / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return package


def test_finalize_blocks_conflict_and_atomically_keeps_partial(tmp_path):
    package = _make_package(tmp_path, summary_range="300-30000")

    result = finalize_package(package)

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["status"] == "partial"
    assert manifest["status"] == "partial"
    assert manifest["finalization"]["status"] == "blocked"
    assert {item["code"] for item in result["blockers"]} == {"SEVERE_CLAIM_CONFLICT"}
    assert manifest["finalization"]["claim_audit"]["severe_conflict_count"] == 1


def test_mcu_finalize_black_box_blocks_cross_source_conflict(tmp_path):
    package = _make_package(tmp_path, summary_range="300-30000")
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["steps"] = [{"text": "操作步骤标注报价范围是 300-3000 元。"}]
    manifest["media"][0]["description"] = "截图描述：报价范围是 300-30000 元。"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "scripts" / "mcu.py"),
            "finalize",
            str(package),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    result = json.loads(completed.stdout)
    finalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert completed.returncode == 3
    assert result["ok"] is False
    assert result["status"] == "partial"
    assert finalized_manifest["status"] == "partial"
    assert {item["code"] for item in result["blockers"]} == {"SEVERE_CLAIM_CONFLICT"}
    audit = result["claim_audit"]
    assert audit["severe_conflict_count"] == 1
    assert {item["source_type"] for item in audit["conflicts"][0]["evidence_claims"]} == {
        "transcript",
        "steps",
    }
    assert audit["conflicts"][0]["supporting_evidence"][0]["source_type"] == (
        "media_description"
    )


def test_finalize_marks_completed_only_after_every_gate_passes(tmp_path):
    package = _make_package(tmp_path)

    result = finalize_package(package)

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["gates"] == {
        "summary": True,
        "structure": True,
        "visual_evidence": True,
        "severe_claim_conflicts": True,
    }
    assert manifest["status"] == "completed"
    assert manifest["finalization"]["status"] == "passed"
    assert manifest["finalization"]["blockers"] == []
    assert validate(package)["ok"] is True


def test_finalize_replaces_manifest_once_from_same_directory(monkeypatch, tmp_path):
    package = _make_package(tmp_path)
    replacements = []
    real_replace = os.replace

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("scripts.package_tool.os.replace", record_replace)

    result = finalize_package(package)

    assert result["ok"] is True
    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert temporary.parent == package
    assert destination == package / "manifest.json"


def test_finalize_lists_summary_structure_and_visual_blockers(tmp_path):
    package = _make_package(tmp_path)
    (package / "summary.md").write_text("# 内容提炼\n\n> 当前为自动准备稿。\n", encoding="utf-8")
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["media"] = []
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = finalize_package(package)

    codes = {item["code"] for item in result["blockers"]}
    assert {"SUMMARY_INCOMPLETE", "SUMMARY_IS_DRAFT", "VISUAL_EVIDENCE_MISSING"} <= codes
    assert any(code.startswith("STRUCTURE_") for code in codes)
    assert result["status"] == "partial"


def test_mcu_exposes_finalize_command():
    args = build_parser().parse_args(["finalize", "/tmp/example", "--visual-evidence", "required"])

    assert args.package_dir == "/tmp/example"
    assert args.visual_evidence == "required"
