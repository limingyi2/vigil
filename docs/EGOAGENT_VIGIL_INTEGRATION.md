# EgoAgent 视频子 Agent 接入方案

本文件是接入设计，不表示服务已经接通。

## 已实现的首版内容索引（2026-10-26）

按用户确认，**不使用向量模型、不做失败降级**。新增 `brain/video_analysis.py`：
将全片分成默认 30 秒的连续区间，每段取 3 张画面交给同一个视觉模型生成描述；
提问时由模型分批选择相关片段，再回到原视频取帧并验证回答引用的时间戳。
这里的内容相关性来自语言/视觉模型判断，不是 embedding 相似度。
索引表示采样区间覆盖整片，不能称为逐帧看完；短暂事件仍可能漏掉，音频转写不包含在内。

缓存按视频内容哈希、模型、采样参数及 fake/real 分区；中断可显式续建，
未覆盖全片不能进入问答。解码、模型、JSON、预算或证据校验失败直接报错。
选不到相关片段返回 `insufficient_evidence`，不编造答案。

“设置 → 视频软件”增加独立分析模型选择，沿用模型管理里的已保存模型与凭据。
默认为 MiMo 2.6 Flash；切换模型使用不同描述缓存，保存配置本身不调用模型。
所选模型必须支持图片；不支持时实际请求失败即报错，不换其他模型。

独立开发入口（不是研究实验或准确率评测）：

```powershell
python tools/ego_video_search.py --ego-root F:/EgoAgent-Eval --video C:/clips/demo.mp4 --question "钥匙放在哪里？"
```

上述命令只报告配置与待授权状态，不发送图片。实际调用须显式加
`--allow-model-calls`，可用 `--max-calls` 限制本轮总请求数（默认 40）。
每次请求前写独立审计记录，结果和失败均记录，不自动重试。
该适配层仅复用 EgoAgent 公开的本地视频解码接口，不改其冻结实验或实验账本；
这些独立产品调用不应混入 EgoAgent 研究实验，也不证明它的准确性。

当前尚未把工作台输入框接到此独立入口，也未执行真实付费模型测试。

## 当前范围：先独立接入，不自动委派

用户尚未确认 EgoAgent 的准确性，因此第一阶段只准备视频区域的独立接入，
不注册为 Vigil 自动可调用的子 Agent，不将输出视为已验证事实。
基础内置播放器已经实现；文本问答和 worker 协议仍为设计，尚未连接模型。

## 后续可选：一个后端、两个入口

- Vigil 聊天把视频问答任务委派给 EgoAgent。
- 视频工作台提供播放器、文本输入、答案和证据列表，调用同一个 EgoAgent 后端。
- 外部剪辑软件窗口托管作为单独模式保留；视频理解不依赖剪映窗口或截图。

## 已核查的现有能力

`F:/EgoAgent-Eval/egoagent_eval/vr/session.py` 的 `VideoSession` 已提供
`ask`、`followup`、`cancel`、`close`；一个会话绑定一个视频及其索引。
`VRSessionResult` 有答案、证据时间戳、不确定项、状态、Token 用量、Trace 路径和错误。
CLI 有 `ask` 和 `chat`，但交互 stdout 混合提示语和 JSON，不宜直接当稳定 RPC。
该项目当前负责视频理解与证据检索，不是现成的剪辑软件控制 Agent。

## 进程和协议

优先使用独立 Python worker，通过带请求 ID 的 JSON Lines stdin/stdout 交换消息；
日志写 stderr。避免把 OpenCV、模型依赖和视频资源生命周期塞入 Vigil Brain。
新建 worker 包装层，不直接依赖私有 CLI 函数，不修改正在冻结验收的 EgoAgent 实验逻辑。

建议操作：`session.open`、`question.ask`、`question.followup`、`run.cancel`、`session.close`。
建议事件：`accepted`、`progress`、`evidence`、`answer`、`usage`、`error`、`cancelled`。
每条携带 `request_id`、`session_id`，问题运行另带 `run_id`；支持取消、超时、进程退出恢复。
已有结果保留原始结构，不能把 `insufficient_evidence` 或 `is_fake=true` 显示成真实分析成功。

待独立验收通过、用户确认后，再由 Vigil 注册 `video_analysis` 能力，两个 UI 入口复用相同任务记录和事件流。
绑定已认证 owner、人设、conversation、视频 ID 与内容哈希；不得跨会话继承视频或答案。
任务结果通过现有会话任务回执进入后续聊天上下文。

## 播放器与文本交互

基础版采用 Electron/Chromium 的 HTML video：打开视频、播放/暂停、拖动、音量、倍速、全屏。
通过本地文件选择及受控媒体 URL 授权资源，避免放开任意文件读取。
视频区域旁显示文本输入、回答、不确定项和证据时间戳；点击时间戳 seek 到对应画面。
更换视频创建新分析会话，普通文字追问复用当前会话。卸载页面隐藏播放器，活动任务不因此重发。

不承诺任意编码、逐帧、字幕编辑、时间轴或导出剪辑；这些需独立评估解码/FFmpeg方案。

## 模型与验收

沿用 EgoAgent 的 Provider、请求账本和预算门禁，真实请求不能绕开其冻结和授权要求。
先以 fake 验证进程协议、UI、取消、会话隔离，再以明确的真实 Provider 小预算验证视频问答。
不为制作 UI 自动启动真实付费请求，不把研究用冻结门禁误包装成通用产品 API。

交付顺序：修复设置与窗口几何 → 内置播放和文本 UI → worker + 能力注册 → 真实问答与时间戳跳转。
