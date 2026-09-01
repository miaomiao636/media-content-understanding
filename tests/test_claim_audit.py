from scripts.claim_audit import CLAIM_TYPES, audit_claims, extract_claims


def test_extracts_every_high_risk_claim_category():
    text = (
        "预算 300-3000 元，另收 $25，成功率 87.5%，处理时长 12 分钟，"
        "版本 v0.2.2，使用 qwen3.5-omni-plus 与 Photoshop。"
    )

    claims = extract_claims(text)

    assert {claim["type"] for claim in claims} == set(CLAIM_TYPES)
    assert next(claim for claim in claims if claim["type"] == "numeric_range")["normalized"] == (
        "300..3000 cny"
    )
    names = {
        claim["normalized"] for claim in claims if claim["type"] == "model_or_software"
    }
    assert {"qwen3.5-omni-plus", "photoshop"} <= names


def test_audit_detects_order_of_magnitude_range_conflict():
    result = audit_claims(
        "这套方案的报价范围是 300-30000 元。",
        ["原视频明确说报价范围是 300-3000 元。"],
    )

    assert result["ok"] is False
    assert result["severe_conflict_count"] == 1
    conflict = result["conflicts"][0]
    assert conflict["type"] == "numeric_range"
    assert conflict["summary_claim"]["normalized"] == "300..30000 cny"
    assert conflict["evidence_claims"][0]["normalized"] == "300..3000 cny"


def test_audit_reports_unsupported_claim_as_warning_not_severe_conflict():
    result = audit_claims("额外费用是 99 元。", ["来源未提到价格。"])

    assert result["ok"] is True
    assert result["severe_conflict_count"] == 0
    assert result["unsupported"][0]["summary_claim"]["type"] == "amount"


def test_chinese_text_does_not_require_spaces_around_claims():
    summary = "成功率90%，处理时长10分钟，版本v0.2.2，模型qwen3.5-omni-plus。"
    evidence = "成功率80%，处理时长12分钟，版本v0.2.3，模型qwen3.5-omni-turbo。"

    result = audit_claims(summary, [evidence])

    assert {claim["type"] for claim in result["summary_claims"]} == {
        "percentage",
        "duration",
        "version",
        "model_or_software",
    }
    assert {conflict["type"] for conflict in result["conflicts"]} == {
        "percentage",
        "duration",
        "version",
        "model_or_software",
    }


def test_exact_derived_description_cannot_mask_conflicting_transcript():
    result = audit_claims(
        "报价范围是 300-30000 元。",
        [
            {
                "text": "原始转写：报价范围是 300-3000 元。",
                "source_type": "transcript",
                "trust": 100,
            },
            {
                "text": "截图描述：报价范围是 300-30000 元。",
                "source_type": "media_description",
                "trust": 60,
            },
        ],
    )

    assert result["ok"] is False
    conflict = result["conflicts"][0]
    assert conflict["highest_conflicting_trust"] == 100
    assert conflict["supporting_evidence"][0]["source_type"] == "media_description"
    assert conflict["evidence_claims"][0]["source_type"] == "transcript"


def test_extracts_unversioned_models_and_office_software_and_detects_name_conflict():
    claims = extract_claims("使用 Claude、Gemini 和 Microsoft Excel 制作。")

    assert {
        claim["normalized"] for claim in claims if claim["type"] == "model_or_software"
    } == {"claude", "gemini", "microsoft excel"}

    result = audit_claims("使用 Claude 完成。", ["来源明确使用 Gemini 完成。"])
    assert result["ok"] is False
    assert result["conflicts"][0]["type"] == "model_or_software"


def test_iso_dates_are_not_treated_as_numeric_ranges():
    claims = extract_claims("发布时间 2026-07-06，更新于 2026-08-01。")

    assert not [claim for claim in claims if claim["type"] == "numeric_range"]
    result = audit_claims(
        "发布时间是 2026-07-06。",
        ["发布时间 2026-07-06，更新于 2026-08-01。"],
    )
    assert result["severe_conflict_count"] == 0


def test_paths_domains_and_metadata_keys_are_not_software_claims():
    claims = extract_claims(
        "来源 v.douyin.com 和 bilibili.com；文件 source.mp4、image-analysis.md；"
        "字段 published_at；模型 qwen3.5-omni-plus。"
    )

    names = {
        claim["normalized"] for claim in claims if claim["type"] == "model_or_software"
    }
    assert names == {"qwen3.5-omni-plus"}


def test_identical_multi_claim_evidence_does_not_conflict_with_itself():
    text = (
        "使用 Python 3.9 和 FFmpeg 6.1；套餐 A 5 元，套餐 B 10 元；"
        "阶段一耗时 5 分钟，阶段二耗时 10 分钟；成功率 80%，覆盖率 90%；"
        "使用 Claude 生成文字，用 Gemini 分析图片。"
    )

    result = audit_claims(text, [text])

    assert result["ok"] is True
    assert result["severe_conflict_count"] == 0


def test_version_conflict_is_scoped_to_the_nearest_software_subject():
    result = audit_claims(
        "使用 Python 3.8，并配置 FFmpeg 6.1。",
        ["使用 Python 3.9，并配置 FFmpeg 6.1。"],
    )

    assert result["ok"] is False
    python_conflict = next(
        conflict
        for conflict in result["conflicts"]
        if conflict["summary_claim"]["normalized"] == "3.8"
    )
    assert [item["normalized"] for item in python_conflict["evidence_claims"]] == ["3.9"]
    assert not [
        conflict
        for conflict in result["conflicts"]
        if conflict["summary_claim"]["normalized"] == "6.1"
    ]
