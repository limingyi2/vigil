# EgoAgent 视频问答接入

现状：视频工作台 = 上方本地播放器 + 下方自然语言问答，答案由 **EgoAgent 自己的
VR 会话**产出（`egoagent_eval/vr/serve.py` 的 `/ask`），不是 Vigil 内在实现的
那份内容索引。两边刻意分开，别混。

## 链路

```
视频工作台（渲染层）
  ├─ 选片：点「选择视频」（或把文件拖进来）
  ├─ 播放：主进程签发的票据地址 http://127.0.0.1:<port>/v/<ticket> → <video>
  └─ 提问：fetch POST http://127.0.0.1:8799/ask（SSE）→ 答案 + 证据时间戳
Electron 主进程
  ├─ client/electron/video-media.js：单文件票据 + Range 206
  └─ client/electron/ego-service.js：
     /health 探活 → 未启动就 spawn <EgoAgent>/.venv/Scripts/python.exe
     -m egoagent_eval.vr.serve --port 8799 --provider mimo
```

- 端口固定 8799；`VIGIL_EGO_ROOT` 覆盖仓库位置（默认 `F:\EgoAgent-Eval`），
  `VIGIL_EGO_PROVIDER` 覆盖 provider（默认 `mimo`，设成 `fake` 是脚本演示）。
- 渲染层直连回环，不经过主进程转发：CSP 的 `connect-src http://127.0.0.1:*`
  本来就允许，把 SSE 塞进 IPC 反而要自己重造分块协议。
- 播放走票据回环而不是 `blob:`：大文件拖进度条依赖 HTTP Range。票据只放行被选
  中的那一个文件、只对 127.0.0.1 生效；非视频扩展名与空文件在登记阶段就拒掉。
- 打开前先探编码（`video-media.js` 的 `prepare()`）。Chromium 只认 MP4/MOV 里的
  H.264/AV1/VP9 和 WebM 里的 VP8/VP9/AV1，所以：能播就原样给地址；H.264 装在
  MKV/AVI 里就 `-c copy` 换封装（几秒、画质不变）；HEVC/MPEG4/ProRes 这类解不了
  的编码才用 libx264 重编码（带 `-progress` 百分比）。ffmpeg 的找法：`VIGIL_FFMPEG`
  → 随包 `resources/ffmpeg.exe` → EgoAgent `.venv` 里 imageio-ffmpeg 自带那份；
  一个都找不到就照原样播，并在状态行说清楚为什么可能放不了。
- 转换产物落在 `%TEMP%\vigil-media\<路径+大小+mtime 的 sha1>.mp4`，同一文件再次
  打开直接复用；超过 7 天的在下次开工时清掉。问答读的也是转换后的路径（内容一致，
  但编码一定能解）。
- 建会话用磁盘路径，来自主进程的 `video:pick` 回执（不再依赖渲染层的
  `File.path`）。拿不到路径就明说，不猜。
- 换视频 = 新会话，上一段问答同时清空；索引按视频内容哈希缓存在 EgoAgent 那边，
  换回同一个视频不重建。
- 离开页面 POST `/close`；App 退出时主进程把自己 spawn 的进程收掉。

## 服务端改了什么（F:\EgoAgent-Eval）

`vr/serve.py` 原本硬拦 `provider != "fake"`，理由是「真实 MiMo 要冻结清单与
ledger 门禁，交互式场景无法满足」。改成：

- `--provider {fake,mimo}` + `--env-file`（默认 `.env`，读 `MIMO_API_KEY`）。
- `open_session` 放开真实 provider；没有 key 直接报错，不静默退回假 provider。
- factory 在真实模式返回 `MiMoVLMProvider(store, run_id, request_gate=…)`。
- **台账分离**：`vlm.py` 强制真实调用必须带 request gate。研究用的
  `ExperimentLedger` 要冻结清单和实验注册，人为问答套不上；新增
  `InteractiveLedger`（`artifacts/vr/interactive_requests.sqlite`）保留同样的
  「先落名额再发请求、崩溃后停在 unknown 不重试」语义，但不写研究账本，
  也不产生可引用的实验结论。
- `--scripted` 只对 fake 生效；`/health` 如实回报 `provider` / `is_fake`。

## 事件与呈现

`/ask` 是 `data: {json}\n\n` 的 SSE，事件类型：`store`、`classification`、
`memory`、`tool`、`round`、`final`、`error`、`done`。

工作台只消费三样：`round`（第几轮）、`tool`（正在看什么）、`final`
（答案 + `evidence_timestamps` + `uncertainties` + `rounds`/`observed_frames`/
`usage`）。证据时间戳渲染成可点按钮，点了就把播放头跳过去并暂停，方便对着
画面自己核。`final.is_fake` 为真时界面明写「假 provider：不含真实视觉判断」。

## 与内置 `brain/video_analysis.py` 的关系

那份是另一条路：不走 EgoAgent 的 agent 循环，用 Vigil 的视觉模型做 30 秒分片
描述 + 回原视频取帧 + 校验时间戳，`/api/video-analysis/settings` 是它唯一的
HTTP 入口，没有问答端点，界面也不再用它。保留是因为它自带缓存/预算/审计，
将来若要「不依赖 EgoAgent 也能问」可以接上；现在不作为答案来源。

## 验收

- 协议：fake 模式 `/health` → `/session` → `/ask` 全通（含 `store/round/final`）。
- 真答案：`synthetic.mp4`（0–2 秒红、2–4 秒蓝）问「视频里有什么？」→ 答
  「前半段红色、后半段蓝色，约 2.08 秒切换」，4 个证据时间戳，`citations_valid: true`。
- 界面：Electron 夹具里真开视频、真提问、真收 SSE；点 `0.5s` 播放头跳到 0.5 秒并暂停；
  亮/暗两套主题文字都可读。

**没有做的事**：没做准确率评测。EgoAgent 答得对不对仍需人工按证据时间戳核对。
