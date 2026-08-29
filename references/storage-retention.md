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

## 推荐流程

```bash
python3 <skill_dir>/scripts/cleanup.py init-root
python3 <skill_dir>/scripts/cleanup.py register <job_dir>
python3 <skill_dir>/scripts/cleanup.py clean --dry-run
python3 <skill_dir>/scripts/cleanup.py clean --apply
```

任务成功后可删除当前任务临时目录。任务失败时按 `failed_job_retention_hours` 保留，便于续跑。每次 Skill 启动时清理过期缓存即可；真正的定时清理属于外部自动化，不是 Skill 自身能力。
