# Vigil Agent 自动化改进前置

本目录为 Vigil 的持续改进闭环提供最小基础设施，不替换现有 Electron + Brain 架构，不引入新的 Agent 框架。

## 本阶段交付

- `capability-baseline.md`：能力域、已知风险和初始基线状态。
- `benchmark-cases.json`：可重复运行的针对性测试清单，以及需要真实客户端/模型才能评估的行为场景。
- `improvement-log.md`：每轮诊断、根因、修改、测试和回归结果的记录模板。
- `../../scripts/agent_eval.py`：运行指定基准测试并将结果写入仓库外的本地报告目录。

## 使用

在仓库根目录运行：

```powershell
python scripts/agent_eval.py --list
python scripts/agent_eval.py --suite smoke
python scripts/agent_eval.py --suite regression
python scripts/agent_eval.py --case agent-run-lifecycle
```

首次运行前不要把任何历史测试结果视为当前基线。先运行 `smoke`，检查环境和失败情况；确认环境可用后再运行 `regression`。真实模型测试、Steam/GPU、真实微信、外部 Bridge 和用户数据迁移不会由此脚本自动执行。

报告默认保存在用户本地状态目录，不写入 Git 工作区：
- Windows：`%LOCALAPPDATA%/Vigil/agent-eval/`
- macOS/Linux：`~/.local/state/vigil/agent-eval/`（可用 `XDG_STATE_HOME` 覆盖）

可通过 `--report <path>` 指定单次报告路径。脚本不调用 shell、不自动修改源码、不自动提交或推送。

## 改进循环

1. 从基线与失败记录选出一个高优先级问题。
2. 复现问题，保存最小证据。
3. 先写回归测试，再做最小兼容修复。
4. 运行该问题的测试、所属 smoke/regression 和必要关联测试。
5. 对比前后报告，检查相关功能是否退化。
6. 通过后记录提交与结果；失败则继续诊断，不得把未运行写成通过。
7. 由 Codex 继续下一项，但每轮范围应小到能明确判断改动是否有效。

## 安全边界

- 不上传 API Key、用户聊天、用户壁纸/素材、私有路径或其他用户数据。
- 不在真实用户数据上运行破坏性测试。
- 不因测试失败而绕过权限或删除既有功能。
- 自动化执行只允许运行清单中明确列出的 argv 命令；不使用 shell 字符串。
- 保留既有未提交改动和审计文件；不自动修改 master、不自动发布。
