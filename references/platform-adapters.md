# 抖音与哔哩哔哩来源适配器

## 共同规则

- 只接受受支持平台的 `http(s)` URL。
- 短链接解析后的最终域名必须再次验证。
- 不把评论、相关推荐和页面导航当作视频正文。
- 不输出 Cookie、Authorization 头和带签名的媒体 URL。
- 每个适配器拿到真实媒体文件和元数据后即停止回退。

## 抖音

获取顺序：

1. 解析分享短链。
2. `yt-dlp` 获取元数据、字幕和媒体。
3. Playwright 捕获页面实际加载的公开媒体流。
4. 需要登录时，只有用户明确配置专用 Playwright 档案并主动完成登录后才保存该 Skill 的会话；验证码或权限控制仍不得绕过。
5. 仍失败时建议用户上传本地视频。

抖音经常把音频和视频分成两个流，并同时暴露数秒预览片段。浏览器适配器会下载有限数量的高分候选，用 `ffprobe` 识别真实轨道、时长和分辨率，再选择视频流与音频流无重编码合并。页面宣称时长与捕获时长明显不一致时，返回 `INCOMPLETE_MEDIA`，不把预览片段宣称为完整来源。

默认 Playwright 使用一次性隔离上下文。配置 `acquisition.browser_profile_dir` 后改用持久上下文，档案目录权限在类 Unix 系统上收紧为仅当前用户可访问，并写入专用管理标记。该目录必须与缓存和输出目录分离；非空但未标记的普通目录会被拒绝，并发占用时返回 `BROWSER_PROFILE_IN_USE`。

## 哔哩哔哩

获取顺序：

1. 识别 BV/AV、短链和分P参数。
2. `yt-dlp` 获取视频信息、平台字幕和媒体。
3. 遇到 403/412、验证码或媒体缺失时使用 Playwright。
4. 没有字幕时提取音频执行 ASR。
5. 多分P、合集或课程只处理用户明确指定的范围，避免意外批量下载。

## 错误类型

- `AUTHENTICATION_REQUIRED`：需要登录态或新鲜 Cookie。
- `CHALLENGE_REQUIRED`：平台验证码或人机验证。
- `ACCESS_RESTRICTED`：403、412、地域或账户权限限制。
- `MEDIA_NOT_FOUND`：页面可读但没有取得媒体。
- `INCOMPLETE_MEDIA`：只捕获到预览或不完整媒体。
- `INVALID_MEDIA`：候选文件无法被 `ffprobe` 识别。
- `INPUT_TOO_LARGE`：候选流超过配置的下载上限。
- `MISSING_DEPENDENCY`：缺少来源获取所需的 `yt-dlp`、Playwright 或 `ffprobe`。
- `NETWORK_ERROR`：DNS、代理、连接或下载失败。
- `ALL_ADAPTERS_FAILED`：全部回退均失败。

这些错误都必须附带可执行建议，但不得建议绕过平台安全控制。
