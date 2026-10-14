# Windows 换机开发交接（2026-10-14）

以后从 `dev` 开发和构建安装包。此次没有打包或部署。

## 整合来源与冲突

- 基线：`fix/mimo-structured-tools`，`3559c18f5d28e92787040a38fc023406b50dd1c0`。
- 附件：`codex/chat-attachments`，`4336aaa625a208bdeae649304ac15215335c4d30`。
- 视频修复：`codex/fix-video-attachments`，`91b54e7441e229c29fbfccb639dd098fe1e21650`；它已包含附件提交，一次合并同时保留两者历史。
- 基线已包含 Agent 工作室、Creator MVP-2、模型设置/密钥地址绑定、模型快速切换、启动性能和 MiMo 修复。
- `main.js` 冲突逐段保留启动计时与原生右键菜单；`pages.js` 的三个冲突分别保留新附件按钮、Creator/模型控件、Creator 状态初始化与附件发送链路。Creator 请求仍走草稿 API，普通文字走流式聊天，附件走上传后元数据聊天。没有用整文件覆盖解决冲突。
- Master、旧分支和原 worktree 保留。旧附件分支基于旧 master，单独用它构建会丢失工作室界面；后续请统一使用 `dev`。

## 已安全保存的工作

检查了原有 10 个 worktree。脏工作区为：主仓库 12 个未跟踪文件；MVP-1 baseline 1 个运行标记；P4-3 2 个本地脚本；模型快速切换工作区 1 个运行标记；P3 freeze 8 个已跟踪证据文件修改。

旧电脑本地备份目录：`F:\sync-checks\vigil-handoff-backup-20261014-120408`。保存了全部 Git refs 的 bundle、每个 worktree 的暂存/未暂存二进制补丁、未跟踪文件和 inventory。Bundle 校验通过。备份可能包含私人脚本，仅通过私人介质迁移，不上传公开仓库。

没有只在本地、所有 origin refs 均不可达的提交。`ui/v2-chat-redesign` 相对其同名远程领先 4 个提交，但这些提交已由远程 master 保存；P3 验收分支的同名远程也已保存完整 HEAD。原工作区未 stash、未清理、未改动。

忽略的依赖、安装包和私人配置未放进上述源码备份；按下方清单人工迁移。恢复补丁时，在对应 inventory HEAD 的新 worktree 中执行 `git apply --index staged.patch`、`git apply unstaged.patch`，再逐个复制该工作区 `untracked/` 下文件；不要覆盖已有同名文件。

## 新电脑初始化

先手动安装 Git、Python 3.12 x64（或更高版本）、Node.js 20+ x64。FFmpeg/FFprobe、Codex CLI 仅在需要对应功能时手动安装并配置 PATH；脚本不会安装系统软件。

```powershell
git clone --branch dev https://github.com/limingyi2/vigil.git
cd vigil
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap-windows.ps1 -CheckOnly
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap-windows.ps1 -WithDevDependencies
```

Python 不在 PATH 时追加 `-Python "D:\Python312\python.exe"`。脚本在当前仓库创建 `.venv`，安装 Brain、PDF 提取与可选 pytest 依赖，按 lockfile 执行客户端 `npm ci` 并检查 Electron/keytar/ws。Skill Host 只有 Node 内置模块，无 npm 依赖。`-WithPackaging` 可安装项目内 PyInstaller；`-SkipPython` / `-SkipNode` 可跳过相应依赖安装。

脚本不读取/修改配置或凭据，不启动服务。安装过程需要包仓库网络；fastembed 模型可能在以后首次使用时下载，本次没有下载模型。旧 `scripts/start_dev.bat` 含旧电脑绝对路径和不存在的 `npm run dev`，请按下面启动方式操作。

## 本地启动（不使用生产配置）

仓库根目录终端 A，创建独立 `.local-dev` 家目录。若已有配置，以下命令拒绝覆盖：

```powershell
$projectRoot = (Get-Location).Path
$env:VIGIL_HOME = Join-Path $projectRoot '.local-dev'
$cfgFile = Join-Path $env:VIGIL_HOME 'config\config.json'
if (Test-Path -LiteralPath $cfgFile) { throw '配置已存在，请人工确认后使用' }
New-Item -ItemType Directory -Force (Split-Path $cfgFile) | Out-Null
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$randomBytes = New-Object byte[] 32
$rng.GetBytes($randomBytes); $rng.Dispose()
$localConfig = @{bind_host='127.0.0.1'; brain_token=[Convert]::ToBase64String($randomBytes);
  api_key=''; model=''; model_mode='cloud'; chat_mode='api';
  vision=@{enabled=$false}; proactive=@{enabled=$false};
  management_agent=@{enabled=$false}} | ConvertTo-Json -Depth 4
# Windows PowerShell 5.1 的 Set-Content -Encoding UTF8 会写 BOM；Python 配置使用无 BOM UTF-8。
[IO.File]::WriteAllText($cfgFile, $localConfig, (New-Object Text.UTF8Encoding $false))
Copy-Item -LiteralPath (Join-Path $projectRoot 'brain\plugins') -Destination $env:VIGIL_HOME -Recurse
$env:VIGIL_PORT = '8788'
$env:BRAIN_MUTEX = 'Local\VigilDevBrain'
.\.venv\Scripts\python.exe brain\server.py
```

此配置没有模型密钥，真实聊天要由用户通过模型设置添加自己的开发用配置。端口被占用时选另一个空闲端口，不停止其他服务。终端 B：

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
cd client
npm start
```

客户端设置连接 `http://127.0.0.1:8788`，本机回环连接可以通过现有连接流程注册设备。新 clone 没有 Brain EXE，默认不会启动旧 sidecar；若迁移了旧客户端配置，先关闭 sidecar/转发/云同步并确认地址，避免运行旧 EXE 或连接生产。

需要测试 Skill 时，在同一环境中生成一个新的本地 `ZX_SKILL_AUTH_TOKEN`（至少 32 字符），让 Brain 和 Host 继承相同值，在受控终端执行 `node brain/skill_host/server.js`。独立家目录还应把 `ZX_SKILL_DIR` 指向 `.local-dev/skill_host/skills`，并在本地 Brain 配置中填写对应回环 Host 地址。不要借用真实 Management Agent Token。旧 Host BAT 含机器路径和端口替换逻辑，换机先使用上述直接启动方式。

## 必须人工迁移/重新配置

- Brain 的 `config/config.json`、模型/API Key、主 Token、world、人设、同步/渠道配置：通过私人介质或重新输入；更新旧机器绝对路径。配置存储格式没有改动。
- Brain `data/`：聊天历史、记忆、Creator 草稿与审批记录、已上传附件；按实际运行家目录查找，可能在源码、独立家或旧 sidecar EXE 旁边。已消费/过期审批不要当作新授权复用。
- 已安装 Plugin/Skill、Host Token、扩展配置与所需权限：审核后迁移，不把未知生成代码自动安装或执行。
- Electron 用户数据（通常 `%APPDATA%/vigil-client`，以实际运行版本为准）：连接、授权目录、壁纸配置。Windows 凭据管理器/keytar 中的 Token 不能靠复制文件迁移，请重新注册设备；检查 `token.json` 的本地降级存储。
- 微信 Bridge 私人配置、SSH 凭据、GitHub 登录、Codex 登录与路径：按需人工配置。不要整体复制旧配置到新测试环境；本轮没有读生产凭据。
- 不复制 `.venv`、`node_modules`；旧构建产物可以私人留档，但以后从 `dev` 重新构建。

## 本次验证与待验收

- Python 相关回归 **25 项通过**：模型/密钥/MiMo、Creator 安装授权边界、附件保存与 FFmpeg 成败路径。运行于临时家目录，无真实模型调用。
- Agent Center 状态机 **12 项通过**，owned/observed、过期状态及版本错误识别保持。
- 新的 `client/tools/test-dev-integration-electron.js` **5 项通过**：控件共存、普通文字流式路由、Creator 分流、粘贴不可解码视频仍上传并发送元数据、工作室三卡片与缺失接口提示。真实 renderer/preload，服务为隔离 IPC fixture；不代表真实 Agent/模型 E2E。
- 回环真实 Brain HTTP 烟测通过：未授权 401、无确认 428、视频字节保存/读取一致、抽帧失败 422 后原文件仍完整。
- Bootstrap 环境检查与新建 `.venv` / `node_modules` 的实际安装通过；`pip check`、主要 Python 包导入、JS/Python 语法和差异检查通过。
- 未验证：新电脑实际安装、真实 137.6 MB 原视频及其编码、原生右键菜单的真人操作、真人 Creator/Skill Host 执行安装、真实 MiMo/云端连接、P3 原生真人批准/拒绝。微信仍受现有全局模型路由限制，独立多模型尚未完成。没有运行全量测试矩阵。
