# 聊天附件改版交接

## 已完成

- 回形针选择和拖放支持 JPG/PNG/GIF/WebP、MP4/MOV/WebM、PDF、TXT、Markdown、CSV、JSON，以及常见源码/配置格式（如 PY/JS/TS/HTML/CSS/SQL/SH/PS1/CPP/GO/RUST）。
- 图片沿用视觉聊天入口，限制 8 MB；其他附件通过 Electron 主进程分块发送到 `/api/attachments/upload`，单块 1 MB，服务端上限 256 MB。成功后聊天 JSON 只携带附件 ID 和脱敏元数据。
- 待发送附件显示名称、大小、类型、进度和状态；超限、读取失败或分块上传失败会显示失败状态并禁用发送，移除附件后才能继续提交。
- 服务端按扩展名/MIME、大小、连续偏移和安全 ID 校验，文件保存到 `data/upload/attachments`，不接受路径。
- 上传必须经过客户端明确确认；服务端没有确认头会返回 428。
- PDF/TXT/代码/JSON 等附件只提取有限文本；视频不会被伪称为已看过完整内容。原视频先完成分块保存，再经用户确认抽取最多 3 个关键帧；浏览器路径使用 `canvas.drawImage`，浏览器解码失败时仅复用本机已有 FFmpeg（通过 PATH 或 `VIGIL_FFMPEG`，不会自动安装），失败时仍保留并发送原视频。原附件或关键帧上传失败时不会进入聊天分析。
- 聊天输入框支持拖放和 Electron 原生右键“粘贴”；剪贴板里的图片或文件进入同一套类型、大小和确认流程，普通文本仍按文本粘贴。
- 聊天历史仅保存附件 ID、名称、类型、大小，不保存绝对路径或二进制。

## 快速验证

在 `brain` 目录运行：

```text
python -m unittest tests.test_attachments -v
```

覆盖本地分块视频保存/读取、图片与常见源码 MIME、超限文件在写入前拒绝、已有 FFmpeg 生成非空 JPEG 关键帧、不可解析视频保留原文件，以及非法类型和非连续分块拒绝。Electron 隔离回归 `node tools/test-dev-integration-electron.js` 覆盖图片发送、小视频上传、源码上传、超限提示和上传失败阻断聊天。未运行完整 P3/P4/Creator 测试矩阵。

另外用临时 `VIGIL_HOME` 和回环 Brain 做了一次 HTTP 烟测：带确认头的 MP4 两块上传成功，`/api/attachments/frames` 通过本机 FFmpeg 生成 JPEG 关键帧并可下载校验；原视频和关键帧都没有进入聊天 JSON。

Electron 渲染器用无害测试 MP4 验证了元数据读取、`drawImage` 绘制和非空 JPEG 编码。

## 边界

附件接口沿用现有 Brain Token/设备权限。渲染层的确认用于避免误上传；它不是强真人身份认证。未实现云端视频原生理解，用户必须看到能力提示并确认是否提取少量关键帧。
