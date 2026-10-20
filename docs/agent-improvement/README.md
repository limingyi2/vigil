# Vigil Agent 自动评测与改进闭环

在仓库根目录运行。执行器只接受清单中的案例 ID，并且清单命令必须是 Python `pytest` argv 数组；不接受用户传入命令，不经 shell 执行。脚本不会编辑业务源码、联网、提交、推送或合并。

## 评测命令

```powershell
python scripts/agent_eval.py --help
python scripts/agent_eval.py validate
python scripts/agent_eval.py list
python scripts/agent_eval.py run --dry-run                         # 默认 smoke
python scripts/agent_eval.py run --suite smoke
python scripts/agent_eval.py run --suite regression
python scripts/agent_eval.py run --set security
python scripts/agent_eval.py run --suite full-local               # all pytest.ini tests, bounded by 300s
python scripts/agent_eval.py run --case agent-run-lifecycle --case runtime-trace
python scripts/agent_eval.py compare <before-report.json> <after-report.json>
```

每份报告为完整清单视图：未选择的自动案例和行为场景均写为 `NOT RUN`。自动案例结果状态为 `PASS`、`FAIL`、`TIMEOUT`、`BLOCKED` 或 `NOT RUN`。只有所有选中的案例均执行且通过才返回 0；失败、超时、无法启动或未执行均返回非零。输出尾部、异常和命令保存在报告中。`full-local` 会运行项目 `pytest.ini` 收集的 `tests/` 与 `brain/tests/` 全部测试，并用 `-rs` 留下跳过原因。

默认报告和可续接任务状态都存放在用户本机状态目录：Windows `%LOCALAPPDATA%/Vigil/agent-eval/`，macOS/Linux `$XDG_STATE_HOME/vigil/agent-eval/`（未设置时 `~/.local/state/vigil/agent-eval/`）。报告可能包含机器本地路径和测试输出，不要把报告复制进 Git。`--state-dir` 可为验证或隔离运行指定另一目录；`--report` 可显式指定单次报告路径。两者都拒绝写入仓库工作区；`.gitignore` 也屏蔽了常见本地覆盖目录。

## 续接改进任务

```powershell
python scripts/agent_eval.py task create --title "问题简述" --priority P1 `
  --root-cause-evidence "报告路径与失败证据" `
  --acceptance "可观察的验收条件" --case runtime-trace `
  --baseline-report <before-report.json>
python scripts/agent_eval.py task list
python scripts/agent_eval.py task show IMP-0001
python scripts/agent_eval.py task update IMP-0001 --status BLOCKED --evidence "阻塞原因与证据"
python scripts/agent_eval.py task resume IMP-0001                 # BLOCKED 只显示状态，不运行
python scripts/agent_eval.py task update IMP-0001 --status IN_PROGRESS --evidence "阻塞解除证据"
python scripts/agent_eval.py task resume IMP-0001 --dry-run       # 恢复任务上下文并预览定向回归
python scripts/agent_eval.py task resume IMP-0001                 # 运行任务关联的测试并附加报告
python scripts/agent_eval.py compare <before-report.json> <after-report.json>
python scripts/agent_eval.py task update IMP-0001 --status READY_FOR_VERIFY --comparison <comparison.json>
```

问题 ID 为 `IMP-0001` 形式并在本地任务目录递增。任务记录优先级、状态、根因证据、验收条件、测试 ID、基线/后续报告、比较和状态历史。状态包括 `OPEN`、`IN_PROGRESS`、`BLOCKED`、`READY_FOR_VERIFY`、`COMPLETED`、`NOT_RUN`。完成状态需要任务先进入 `READY_FOR_VERIFY`，所有关联测试在最新报告中通过，并关联相同测试/环境的比较且无回归或新增失败。失败证据追加保存；失败的回归不会自动完成任务。

对比结果逐案例标记 `FIXED`、`REGRESSION`、`NEW FAILURE`、`UNCHANGED`、`CHANGED` 或 `NOT RUN`。只有测试选择和 Python/平台环境一致时 `same_conditions` 才为真；完成任务另需把比较 JSON 关联到任务。选择的是定向测试集时，不应把未运行的其他功能写成通过。

## 安全边界与能力限制

- `benchmark-cases.json` 是唯一执行命令清单。每条命令为 argv，需以 `{python} -m pytest` 开头，测试 ID 必须在 manifest 中；可声明仓库内 `cwd`，路径穿越会被拒绝；不支持 shell 字符串。
- 自动化套件只运行本地自动化测试。真实模型、Electron UI、会话重启、真实插件/外部系统行为场景单独列出并保持 `NOT RUN`，直到由相应环境执行并记录证据。
- 不自动修复业务代码，不自动循环重试失败，不修改超时/资源限制来伪造通过。超时只能由用户在单次命令显式覆盖。
- 本阶段建立流程和验证设施，不启动 Vigil 业务 Bug 修复循环。首次基线与待验证能力见 [capability-baseline.md](capability-baseline.md)，历史追加在 [improvement-log.md](improvement-log.md)。
