# 贡献指南

## 开发环境

```bash
uv sync --extra dev
uv run pytest
uv run ruff check scripts tests
uv run python scripts/self_test.py
```

修改浏览器适配器时，另外安装：

```bash
uv sync --extra browser --extra dev
uv run playwright install chromium
```

## 责任边界

- 只提交公开、有权访问的测试样本。
- 不提交 API Key、Cookie、Authorization 头、签名 URL、完整原视频或本机绝对路径。
- 不增加绕过验证码、DRM、付费、私密或地域限制的能力。
- 错误消息必须脱敏，并保持结构化错误类型。

## 提交要求

1. 先添加或更新不联网单元测试。
2. 上述本地检查全部通过。
3. 涉及平台适配时，记录平台、链接类型、期望行为和实际错误类型，但不附带私密凭据。
4. 不要为无关代码执行批量格式化或重构。

## 真实链接测试

真实抖音/B站测试不放入 CI，因为它受地区、网络、风控和页面变化影响。发布前由维护者手工验收：

- 抖音短链能解析，输出同时包含真实视频轨和音频轨。
- B站 BV 链接能解析，时长与页面元数据基本一致。
- 只取得数秒预览时必须失败，不得返回假成功。
