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
2. 规范地址是 `/note/<id>` 时，先进入图文适配器；其他来源由 `yt-dlp` 获取元数据、字幕和媒体。
3. 视频获取失败后，Playwright 捕获页面实际加载的公开媒体流。
4. 需要登录时，只有用户明确配置专用 Playwright 档案并主动完成登录后才保存该 Skill 的会话；验证码或权限控制仍不得绕过。
5. 仍失败时建议用户上传有权处理的本地内容。

### 图文与长文本

`/note/<id>` 由 `douyin-content` 适配器读取，并规范化为统一来源对象：

- `long_text`：有作者正文、没有图片。
- `gallery`：有按原始顺序排列的图片、没有作者正文。
- `mixed`：同时有作者正文和图片。

解析首先读取页面内嵌的结构化作品数据，并以来源 ID 匹配作品记录；只有结构化作品数据缺失时，才读取页面中限定的作品详情区域。DOM 回退不会把评论区、相关推荐、导航、侧栏、页头、页尾、广告或登录面板作为作者正文。标题、作者、发布时间、作者正文和图片顺序分别保存，不用图片 OCR 文本替代作者正文。

真实页面的图片地址只在当前获取任务内使用。下载完成后的来源对象和元数据只保存本地图片路径，不保存临时签名 URL、Cookie 或认证请求头。当前硬性边界为：

- 只接受 `http`/`https` 图片 URL，禁止 URL 用户名和密码。
- 下载前解析 DNS，拒绝回环、私网、链路本地、保留和其他非公开 IP；建立连接时再次验证并把 TCP 目标固定为字面公网 IP，HTTPS 仍以原始主机名校验 SNI 和证书，不让 `urllib` 再次解析主机名。
- 每次重定向重复协议、凭据、DNS 和固定 IP 检查；图片下载不使用隐式系统代理，避免代理端对目标域名执行未受控的二次解析。
- 最多跟随 3 次重定向。
- 单个作品最多 30 张图片；单张最多 20 MiB；全部图片合计最多 200 MiB。
- 响应 MIME 必须是受支持的栅格图片，且文件签名必须与 MIME 一致。
- 任一安全或完整性检查失败时不保留 `.part` 文件，也不降级为抓取页面截图冒充原图。

页面明确要求登录时返回 `AUTHENTICATION_REQUIRED`；出现验证码、人机验证或挑战页时返回 `CHALLENGE_REQUIRED`。可见浏览器会同时检查 URL、标题和页面可见正文；检测到登录、验证码或滑块后保留窗口，最多等待 120 秒供用户主动完成，超时即停止。访问阻断检查早于结构化数据和 DOM 解析，即使页面残留作品缓存也不会返回。`/note/` 的图文适配一旦失败，路由器会保留具体错误并停止，不会改走视频捕获。适配器不会点击验证控件、模拟绕过挑战，或把登录提示当作作者正文。

### 真实获取审计收据

`tests/fixtures/douyin/real-public-note-success.json` 是最终实现对公开 `/note/7659275356428852849` 实时成功重放后生成的脱敏收据，观测时间为 `2026-08-30T14:55:45Z`。该次获取使用无持久档案的 headless Playwright，没有读取浏览器 Cookie；结果是 `mixed`，保留标题、作者、发布时间、14 个字符的作者正文和原序 8 张 WebP。

收据不保存图片、签名 URL、Cookie、本机绝对路径或完整作者正文；只保存公开作品 URL、公开元数据、正文 SHA-256、图片顺序/大小/SHA-256 和整份收据的自校验哈希。`build_acquisition_audit_record` 只能从已经成功且本地图片存在、元数据不含远程 URL 的非视频结果生成收据；`validate_acquisition_audit_record` 校验 schema、图片顺序、哈希格式、URL 脱敏和收据完整性。

可以离线核验已登记收据：

```bash
python -c "import json; from pathlib import Path; from scripts.douyin_content_adapter import validate_acquisition_audit_record as v; p=Path('tests/fixtures/douyin/real-public-note-success.json'); print(v(json.loads(p.read_text(encoding='utf-8'))))"
```

收据只证明指定时间的真实成功，不会把之后的挑战失败伪装成成功，也不能代替“当前可用性”的新鲜实时重放。同一公开样本在后续重放中可能因抖音实时风控返回 `CHALLENGE_REQUIRED`；这种结果必须按失败记录，不点击或绕过验证。需要新鲜实时成功时，只能等待平台允许公开访问，或由用户在明确配置的 Skill 专用档案中主动完成登录/验证后再重试。

抖音经常把音频和视频分成两个流，并同时暴露数秒预览片段。浏览器适配器会下载有限数量的高分候选，用 `ffprobe` 识别真实轨道、时长和分辨率，再选择视频流与音频流无重编码合并。页面宣称时长与捕获时长明显不一致时，返回 `INCOMPLETE_MEDIA`，不把预览片段宣称为完整来源。

默认 Playwright 使用一次性隔离上下文。配置 `acquisition.browser_profile_dir` 后改用持久上下文，档案目录权限在类 Unix 系统上收紧为仅当前用户可访问，并写入专用管理标记。该目录必须与缓存和输出目录分离；非空但未标记的普通目录会被拒绝，并发占用时返回 `BROWSER_PROFILE_IN_USE`。

浏览器捕获到媒体地址后，下载器会逐个向 Playwright 查询该 URL 实际适用的 Cookie，不会把整个浏览器 Cookie 集合拼接给所有 CDN。初始媒体 URL、DNS 结果和每次重定向都必须指向公开网络地址；跨域重定向会移除 Cookie、Authorization 和 Proxy-Authorization。Ego Browser 仍是宿主可选替代能力，不是 CLI 内置适配器，因此 Ego 登录与 Playwright 专用档案不会自动互通。

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
- `CONTENT_NOT_FOUND`：页面可读，但限定的作者内容区域和结构化数据都没有可靠正文或图片。
- `UNSUPPORTED_SOURCE_TYPE`：适配器收到不属于自身范围的内容类型。
- `INCOMPLETE_MEDIA`：只捕获到预览或不完整媒体。
- `INVALID_MEDIA`：候选文件无法被 `ffprobe` 识别。
- `UNSAFE_MEDIA_URL`：图片或视频 URL 的协议、凭据、DNS 或目标地址不满足公开网络限制。
- `TOO_MANY_REDIRECTS`：媒体下载超过重定向上限。
- `TOO_MANY_IMAGES`：图集图片数量超过安全上限。
- `INPUT_TOO_LARGE`：候选流超过配置的下载上限。
- `MISSING_DEPENDENCY`：缺少来源获取所需的 `yt-dlp`、Playwright 或 `ffprobe`。
- `NETWORK_ERROR`：DNS、代理、连接或下载失败。
- `ALL_ADAPTERS_FAILED`：全部回退均失败。

这些错误都必须附带可执行建议，但不得建议绕过平台安全控制。
