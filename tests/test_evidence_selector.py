from scripts.evidence_selector import build_evidence_plan


def test_plan_combines_visual_language_and_scene_changes_with_limits_and_deduplication():
    transcript = [
        {"start": 2.0, "end": 4.0, "text": "这里可以看到设置页面的参数"},
        {"start": 3.0, "end": 5.0, "text": "看这里的界面参数"},
        {"start": 12.0, "end": 15.0, "text": "点击后会有一段过渡动画和状态变化"},
        {"start": 13.0, "end": 16.0, "text": "切换时的动效就是这样"},
        {"start": 32.0, "end": 34.0, "text": "这张图表展示最终结果"},
    ]

    plan = build_evidence_plan(
        transcript,
        scene_changes=[3.4, 13.8, 25.0, 33.2],
        duration=40.0,
        max_images=2,
        max_clips=1,
        dedupe_seconds=4.0,
        clip_seconds=10.0,
    )

    images = [item for item in plan if item["type"] == "image"]
    clips = [item for item in plan if item["type"] == "clip"]
    assert len(images) == 2
    assert len(clips) == 1
    assert clips[0]["start_seconds"] < 13.8 < clips[0]["end_seconds"]
    assert "动态" in clips[0]["reason"]
    assert all(abs(first["timestamp_seconds"] - second["timestamp_seconds"]) >= 4 for first, second in zip(images, images[1:]))


def test_plan_uses_scene_changes_and_keeps_a_bounded_baseline_image_without_transcript():
    plan = build_evidence_plan(
        [],
        scene_changes=[1.0, 1.5, 9.0, 17.0],
        duration=20.0,
        max_images=2,
        max_clips=1,
        dedupe_seconds=3.0,
    )

    assert [item["type"] for item in plan] == ["image", "image"]
    assert len({item["timestamp_seconds"] for item in plan}) == 2
    assert all("scene-change" in item["signals"] for item in plan)


def test_plan_clamps_short_dynamic_clip_to_media_duration():
    plan = build_evidence_plan(
        [{"start": 0.2, "end": 1.0, "text": "滑动后界面状态变化"}],
        scene_changes=[],
        duration=3.0,
        max_images=1,
        max_clips=1,
        clip_seconds=12.0,
    )

    clip = next(item for item in plan if item["type"] == "clip")
    assert clip["start_seconds"] == 0.0
    assert clip["end_seconds"] == 3.0
    assert next(item for item in plan if item["type"] == "image")["timestamp_seconds"] <= 3.0
