# 存储与清理

## 两类目录

- `temp_root`：原视频、音频、密集抽帧、转码和上传缓存。允许按策略清理。
- `output_root`：摘要、逐字稿、关键截图和必要短片。不得按缓存策略自动删除。

## 安全不变量

- 清理脚本只能操作配置的 `temp_root`。
- `temp_root` 不能是文件系统根目录、用户主目录或输出目录。
- 根目录必须包含 `.media-content-understanding-managed`。
- 每个可删除任务目录必须包含 `.job-managed`。
- 默认清理命令只预览；只有显式 `--apply` 才删除。
- 未带任务标记的目录永远不进入清理候选。
- 容量清理不选择近期状态为 `running` 的任务，避免影响并发分析。

## 自动生命周期

- 每次创建受控任务前，主流程清理已过期任务，并在超过 `max_cache_gb` 时按最旧优先清理非运行任务。
- `mcu analyze` 只有在输出包通过验证后，才根据 `cleanup_on_success` 删除当前任务。
- 分析失败或发生异常时，任务标记为 `failed`，按 `failed_job_retention_hours` 保留。
- `keep_source_media=true` 时，成功任务保留在缓存中，但仍可能在 TTL 到期或容量超限后清理。
- `mcu acquire` 会保留其成功结果，因为媒体文件就是该命令的交付物；任务状态记录为 `completed`，仍受后续缓存策略管理。

## 推荐流程

```bash
python3 <skill_dir>/scripts/cleanup.py init-root
python3 <skill_dir>/scripts/cleanup.py register <job_dir>
python3 <skill_dir>/scripts/cleanup.py clean --dry-run
python3 <skill_dir>/scripts/cleanup.py clean --apply
```

自动清理只在 Skill 创建新任务时触发。长期没有新任务但仍希望定期释放空间时，可由外部自动化显式运行 `cleanup.py clean --apply`；Skill 本身不创建后台定时任务。
