# P0 证据与复查

审查基线与结论见 [P0 报告](../agent-center-codex-p0.md)。

## 文件含义

- `environment.json`：本机版本、源码哈希、实验统计。
- `discover.json` / `managed.json`：只读历史探测 / 两个真实 turn 的摘要。
- `fixture.json` / `ws-fixture.json`：真实 App Server 加本机确定性模型夹具的结果。
- `vigil-auth.json`：隔离 AST 纯函数检查，7/7；不是端到端测试。
- `*-events.jsonl`：字段白名单轨迹。GUID 使用别名，消息正文、工具命令、登录信息、账户身份不归档。`at` 是探针的 monotonic 秒数，不能当作 UTC 时间；单个轨迹内可以计算间隔。
- `schema-manifest.json`：本机 CLI 生成的全部 447 个 schema 哈希。
- `schema/`：相关 14 个 schema；不是完整 SDK。
- `scripts/`：实验脚本。归档时将 CLI 路径改为 PATH 发现，给真实实验增加显式参数闸门。原始实验使用同版本本机绝对路径。脚本 CLI 闸门不等于生产安全授权机制。

没有归档生产配置、凭据、完整会话正文、App Server 的原始 stderr，或测试 home 的原生 rollout。`fixture.json` 中顶层工具目录检查 false 保留原值；提问通过强制夹具响应验证，不能据此声称自然工具选择已经验证。

## 本机复查（Windows / PowerShell）

依赖：同版本 Codex CLI、Python、`jsonschema`、`websocket-client`。先将 `scripts/` 的三个脚本复制到一个新的临时目录，再生成该版本的完整 schema。不要在本证据目录内直接执行，避免覆盖原始结果。

```powershell
# $probeDir 指向新建的临时目录，目录中已有三个脚本。
codex --version
codex app-server generate-json-schema --experimental --out "$probeDir/schema"
python "$probeDir/fixture.py"
python "$probeDir/ws_fixture.py"
```

上述两个夹具仅连接本机模型端点；随机 Bearer token 由脚本生成。App Server 和模型夹具均只绑定 loopback，测试 home / workspace 落在临时目录，不复制登录文件。WebSocket token 文件在结束时删除。

`probe.py --mode discover` 会读取当前 Codex home 的历史元数据，并可在内存中读取当前会话历史；仅导出计数与状态。需要审查该行为后再运行。只有 Codex 任务环境提供 `CODEX_THREAD_ID` 时，当前会话探测才有明确目标。

`probe.py --mode managed --allow-real-model` 会产生最多两个真实 turn，并沿用现有原生登录 home；会使用账户额度。**本次授权的两个真实 turn 已执行完毕；复查前应取得新的真实调用授权。**

复查不要对正在使用的 Desktop thread 调用 resume、interrupt 或审批答复。所有这里的控制实验目标都应是脚本自己创建的测试 thread。

## 限制

夹具验证原生协议行为，不能证明真实模型效果；7 项 AST 检查不能证明部署链路；版本变化应重新生成 schema 和复核字段。目录不提供 Agent Center 实现，不代表审批归属、安全沙箱或完整 UI 已完成。
