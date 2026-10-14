# Vigil Creator Mode P4 Windows 交接

## 当前基线和远程分支

本地 Windows 优先拉取并切换到 `creator-mode-p4-4b-2`。该分支基于 P4-4B-1，包含完整 P4 历史；本次交接提交后再推送到 GitHub。

| 阶段 | 主要提交 | 远程分支 |
| --- | --- | --- |
| P3 审批冻结 | `db56c29718ab3af9fd5eb38703d81469087ff822` | `p3-approval-freeze` |
| P4-1 Manifest 验证 | `0a7502c` | 无独立远程分支；已包含在 P4-2 历史 |
| P4-2 插件模板和结构检查 | `96b114a457d0dce321539fd1b6ad7ac50fbf6c6f` | `creator-mode-p4-2` |
| P4-3 插件安装事务 | `0275152581abed62b5e936dc358b5398a59d4d70` | `creator-mode-p4-3` |
| P4-4A Skill 静态验证 | `8f4268fecc96781a805f60dde5060009e34d63c4` | `creator-mode-p4-4a` |
| P4-4B-1 Host 安全加固 | `c28ea07dead361b7fb5e641473beca4494f48451` | `creator-mode-p4-4b-1` |
| P4-4B-2 Skill 事务 | `9a3fc41c1f81e0a0b1ac3409da0176e0dc139b79` | `creator-mode-p4-4b-2` |

P4-4B-2 交接文档提交会追加在上述代码提交之后。不要合并 master。

## 已实现能力

- P4-1：插件 Manifest 字段、名称、路径和类型验证。
- P4-2：插件模板与目录结构检查。
- P4-3：插件市场路径安全、安装更新事务、失败回滚和数据目录保留。
- P4-4A：Skill 单层/同名双层结构、`index.js`/`index.mjs`/`index.cjs` 契约、`skill.json` 静态验证、路径和符号链接拒绝、模板及 CLI。
- P4-4B-1：Node Skill Host Bearer Token、回环监听、入口和 Skill 根目录边界检查；Python Bridge 使用同一运行时凭据。
- P4-4B-2：Skill 文件、渠道配置和 Host 注册状态的受控事务；更新或卸载失败恢复旧版本；并发配置修改保护；reload 失败保留旧注册表并返回失败；回滚失败保留恢复证据。

`setup()`/`register()` 已执行的线程、网络和其他外部副作用没有通用撤销机制。更新和卸载可能返回 `restart_needed`。本阶段不提供运行时沙箱、进程崩溃后的自动恢复或跨进程事务。

## Windows 环境准备

1. 安装受支持的 Python 3.11+、Node.js LTS 和 Git。
2. 在仓库根目录创建隔离虚拟环境，不使用生产凭据：

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. 不执行 `npm install` 或 `pip install` 来安装第三方 Skill 依赖；Host 和模板使用 Node 内置模块。
4. 为测试设置临时 `VIGIL_HOME`/配置目录和临时端口。不要覆盖用户现有 `brain/config/config.json`。
5. `ZX_SKILL_AUTH_TOKEN` 只通过 Windows 服务管理器、受限环境变量或系统凭据注入，使用测试专用随机值；不要写入仓库、脚本或聊天记录。

## Linux 已通过的测试

在 Cloud Linux 已通过：

```bash
PYTHONPATH=brain python -m unittest discover -s brain/tests -p 'test_skill*.py'
PYTHONPATH=brain python brain/tests/test_plugin_transactions.py
PYTHONPATH=brain python brain/tests/test_plugin_manifest.py
PYTHONPATH=brain python brain/tests/test_plugin_structure.py
node --check brain/skill_host/server.js
python -m py_compile brain/skill_transaction.py brain/plugins/skill.py
```

Linux 结果：Skill/Host 34 项、插件事务 21 项、Manifest 7 项、插件结构 9 项通过。测试使用临时目录、临时配置、测试 Token 和故障注入。

## Windows 必须补验

### NTFS 和路径安全

- 使用临时目录测试 NTFS 文件锁下的 `os.replace`、目录备份和恢复。
- 创建 junction 和符号链接，确认 Skill 安装、更新、卸载和 Host reload 均拒绝越界对象。
- 测试 `CON`、`PRN`、`AUX`、`NUL`、`COM1`、`LPT1` 等保留名称。
- 测试大小写冲突、尾随点/空格、反斜杠和绝对路径。
- 使用普通用户权限运行，确认失败时不需要提升权限，也不会触碰生产 Skill。

### Host 和 Python Bridge

```powershell
$env:ZX_SKILL_HOST = "127.0.0.1"
$env:ZX_SKILL_PORT = "临时端口"
$env:ZX_SKILL_AUTH_TOKEN = "仅测试用的随机长值"
node brain\skill_host\server.js
```

验证：无 Authorization 返回 401，错误 Bearer 返回 401，正确 Bearer 可访问 `/health`、`/skills`、`/skills/call`、`/reload`；`0.0.0.0` 等公开监听地址和缺少 Token 必须拒绝启动。Python Bridge 必须携带同一测试凭据；客户端 `ctx` 字段不能代替认证头。

### restart_needed 和服务重启

1. 用临时 Skill 做一次更新，确认文件提交成功但结果包含 `restart_needed`。
2. 停止 Host 服务并重新启动，确认旧 Skill 的后台线程/网络副作用不被误报为已回滚。
3. 卸载后重启 Host，确认文件、渠道配置和注册状态一致，用户数据目录仍保留。
4. 注入 reload、配置写入和目录恢复失败，确认返回失败并保留人工恢复证据。

### Electron 与 Brain 联动

在 Windows 本地运行 Electron Agent Center 和 Brain 测试入口，确认：

- 安装、更新、卸载显示静态检查、文件提交、reload、运行确认的真实状态。
- reload 失败不会显示安装成功。
- `restart_needed` 显示为需要重启，而不是声称运行时副作用已回滚。
- P3 审批策略和权限隔离保持原行为。

## 尚未完成和风险

- P3 真人批准/拒绝的 Windows + Electron 端到端验收仍是独立待办。
- Management Agent 旧 Token 尚未轮换。移除 Git 跟踪不等于旧 Token 已失效；管理员需在中心、server-b 和 local-pc 分别生成新值，按维护窗口先更新中心再更新节点，确认新心跳后对旧值做负向验证并从备份/部署变量清理旧值。不要把真实值提交到仓库或交接文档。
- Windows NTFS、junction、文件锁、服务重启和 Electron 联动尚未在 Linux 证明。

## 合并 master 前验收条件

- `creator-mode-p4-4b-2` 及交接文档已推送且远端 SHA 核对一致。
- Linux Skill、插件和 Host 回归全部通过。
- Windows NTFS、Host/Bridge、服务重启和 Electron 联动测试通过。
- P3 真人批准/拒绝端到端验收单独记录，不能用模拟测试替代。
- Management Agent Token 已由管理员完成轮换和旧值失效验证。
- 审查提交差异不含真实 Token、密码、私钥、临时文件或生产配置。
