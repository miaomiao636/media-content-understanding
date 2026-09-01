import argparse
import json
from pathlib import Path

from scripts.package_tool import finalize_package, initialize, render_summary_html, validate


def _manifest() -> dict:
    return {
        "schema_version": "1.0",
        "package_type": "media-analysis-package",
        "status": "partial",
        "source": {
            "input_url": "https://www.bilibili.com/video/BV1test",
            "platform": "bilibili",
            "source_id": "BV1test",
            "title": "HTML 阅读版测试",
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
                "path": "media/images/001.png",
                "timestamp": "00:10",
                "reason": "展示最终界面",
                "description": "展示字段与布局",
            },
            {
                "type": "clip",
                "path": "media/clips/001.mp4",
                "time_range": "00:20-00:28",
                "reason": "展示交互过程",
                "description": "该动态效果无法用单张截图完整表达",
            },
        ],
        "limitations": [],
        "processing": {},
        "errors_file": "errors.json",
    }


def _complete_summary() -> str:
    return """# HTML 阅读版测试

## 一分钟核心结论

这是一份同时包含文字、图片和短视频证据的内容提炼。

## 解决的问题与适用场景

用浏览器直接查看理解包，不需要依赖 Agent 软件的 Markdown 预览。

## 主题结构

| 类型 | 用途 |
| --- | --- |
| 图片 | 静态视觉证据 |
| 短片 | 动态视觉证据 |

## 可执行步骤与关键参数

1. 用浏览器打开 `summary.html`。
2. 查看图片并播放短片。

## 视觉证据与证据作用

![最终界面](media/images/001.png)

![包外图片](../../outside.png)

![远程图片](https://tracker.example/pixel.png)

<script>alert("unsafe")</script>

## 来源与推断、缺失信息

图片和短片来自原视频；对效果用途的说明属于提炼。

## 复刻前仍需验证

需要在目标浏览器中验证当地 MP4 播放能力。
"""


def _write_package(root: Path) -> dict:
    (root / "media" / "images").mkdir(parents=True)
    (root / "media" / "clips").mkdir(parents=True)
    (root / "media" / "images" / "001.png").write_bytes(b"png")
    (root / "media" / "clips" / "001.mp4").write_bytes(b"mp4")
    (root / "summary.md").write_text(_complete_summary(), encoding="utf-8")
    (root / "source-content.md").write_text("# 来源\n\n测试内容。\n", encoding="utf-8")
    (root / "transcript.md").write_text("# 转写\n\n测试内容。\n", encoding="utf-8")
    (root / "errors.json").write_text("[]\n", encoding="utf-8")
    manifest = _manifest()
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def test_initialize_declares_and_creates_html_summary(tmp_path):
    package = tmp_path / "package"
    result = initialize(
        argparse.Namespace(
            package_dir=str(package),
            content_type="video",
            source_url="https://www.bilibili.com/video/BV1test",
            platform="bilibili",
            source_id="BV1test",
            focus="",
            force=False,
        )
    )

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert result == 0
    assert manifest["content"]["summary_html_file"] == "summary.html"
    assert (package / "summary.html").is_file()


def test_html_renders_markdown_images_tables_and_playable_clips_safely(tmp_path):
    package = tmp_path / "package"
    manifest = _write_package(package)

    html = render_summary_html(package, manifest)

    assert "<h1>HTML 阅读版测试</h1>" in html
    assert "<table>" in html
    assert '<img src="media/images/001.png"' in html
    assert '<video controls preload="metadata">' in html
    assert '<source src="media/clips/001.mp4" type="video/mp4">' in html
    assert "00:20-00:28" in html
    assert "<script>" not in html
    assert "&lt;script&gt;alert" in html
    assert "../../outside.png" not in html
    assert "tracker.example" not in html
    assert html.count("已阻止不安全图片") == 2


def test_finalize_regenerates_html_and_declares_it_in_manifest(tmp_path):
    package = tmp_path / "package"
    _write_package(package)

    result = finalize_package(package)

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    html = (package / "summary.html").read_text(encoding="utf-8")
    assert result["ok"] is True
    assert manifest["content"]["summary_html_file"] == "summary.html"
    assert "HTML 阅读版测试" in html
    assert "<video controls" in html


def test_validate_rejects_missing_declared_html(tmp_path):
    package = tmp_path / "package"
    manifest = _write_package(package)
    manifest["content"]["summary_html_file"] = "summary.html"
    (package / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    result = validate(package)

    assert result["ok"] is False
    assert "HTML 阅读版不存在：summary.html" in result["errors"]


def test_validate_rejects_html_path_outside_package(tmp_path):
    package = tmp_path / "package"
    manifest = _write_package(package)
    manifest["content"]["summary_html_file"] = "../outside.html"
    (package / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    result = validate(package)

    assert result["ok"] is False
    assert any("路径越出理解包" in error for error in result["errors"])
