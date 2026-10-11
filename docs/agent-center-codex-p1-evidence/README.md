# P1 验收证据

- `real/summary.json`：2 个真实 Codex turn，均 completed，各 1 次工具失败；不是任务成果正确性评分。
- `real/events.jsonl`：真实模型监督轨迹；没有消息正文、工具命令或凭据。
- `local-fixture/summary.json`：原生 CLI + 本机确定性模型端点，4 次本机请求；0 次真实模型调用。
- `local-fixture/events.jsonl`：正常、失败、等待审批、重复投递、进程退出、重连、显式新 turn 的脱敏轨迹。
- `regression.txt`：最终 27 项回归输出。
- `source-manifest.json`：验收基线、协议版本和最终实现的源码 SHA-256。

`connection` / `generation` 是 Adapter 生成的随机标识；task、thread、turn 是哈希化关联标识，不能用于操作原始会话。`at` 为 epoch 秒；环境日期采用 Asia/Shanghai 的 2026-10-11。

事件字段采用白名单。`live=true` 指实时来源，不表示任务一定仍运行；状态为 completed 时任务已结束。重连时没有收到执行证据就保持 unknown / history / live=false。

真实实验之后增加的修改只加强协议答复禁用、迟到事件处理和回归覆盖；没有再次调用真实模型。后续原生夹具回归覆盖了最终代码。

复查请输出到新临时目录，避免覆盖此次证据。详情见 [P1 报告](../agent-center-codex-p1.md)。
