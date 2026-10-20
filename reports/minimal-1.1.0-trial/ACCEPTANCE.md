# Vigil 1.1.0 最小试用候选验收

## 构建来源

- 仓库：`limingyi2/vigil`
- 源提交：`935ac8e8ba23ec82d043c9b7cf75e77208c8a141` (`master`)
- 候选版本：`1.1.0`
- 安装包：`Vigil.Setup.1.1.0.exe`
- 大小：`101,661,596` bytes
- SHA-256：`dd61e0065647746d0858ad42b571b1f3445a24e5381a294dd7728672963a8530`
- GitHub Actions 构建/升级验收：[run 39042464120](https://github.com/limingyi2/vigil/actions/runs/39042464120)
- Artifact：`vigil-1.1.0-windows-acceptance-52`，预计于 2027-01-15 过期。
- 本地下载包与 `release-manifest.json` 的提交号、大小和 SHA-256 一致。

## 验收

- **通过：** 精确提交的 CI（[run 39042464090](https://github.com/limingyi2/vigil/actions/runs/39042464090)）：Python 3.12 `pytest tests -q`、Brain/Pet `compileall`、Node 共享订阅与 Creator 回归。
- **通过：** Windows 原打包工作流：官方 1.0.2 基线安装、1.1.0 全新安装、升级迁移、安装失败保护与回滚；多会话 A/B 隔离；插件安装、工具 schema、模型工具请求与工具结果闭环；Brain 启动失败保护。
- **通过：** 同一候选包的隔离生命周期脚本：核心文件校验、Electron 启动、Brain 健康检查、欢迎页、Fake Provider 首条聊天、Electron 退出重启后的历史恢复、卸载清除临时程序文件并保留隔离用户数据（68 个文件）与外部 sentinel。
- **通过：** 对 `935ac8e` 工作树执行 `node --check`（app/pages/Electron main/Electron 测试脚本/安装运行时脚本）和打包/生命周期 Python 脚本 `py_compile`。
- **未通过但不阻塞试用：** 复用 artifact 的第二次远程生命周期 workflow run（39042864747）因工作流下载步骤硬编码名称 `vigil-1.1.0-windows-acceptance-candidate`，而上传 artifact 实际名含 run number，未进入生命周期测试。之后已用仓库同一生命周期脚本对同一包在隔离 Temp 安装路径和用户配置下运行并通过；无需改包或绕过断言。
- Wallpaper 冷启动/WebGL 未纳入本次试用阻塞项，未继续调查。

## 欢迎页改版

欢迎页改版仅存在于另一工作区中的未提交文件，不在 `935ac8e` 中，因此没有混入此次候选包。基于该提交创建了本地试用分支 `codex/minimal-1.1.0-trial`，工作树位于 `%TEMP%\vigil-master-935ac-worktree`。

## 安装与试用

从上方 GitHub Actions run 页面下载 `vigil-1.1.0-windows-acceptance-52` artifact 并解压，然后运行 `Vigil.Setup.1.1.0.exe`。首次试用建议先核对上述 SHA-256；安装后完成首次启动、创建聊天、切换历史会话，再正常退出并重启，确认会话恢复。此包为未发布候选，当前没有正式 Release 或自动更新渠道。
