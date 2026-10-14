# Windows 开发环境：克隆、安装与启动

本文适用于从 GitHub 克隆 `dev` 的新 Windows 开发机。启动只使用当前仓库中的脚本和本机隔离配置，不需要复制旧电脑的配置、凭据或构建产物。

## 当前项目结构

```text
vigil/
├── brain/                 # Python Brain；server_launch.py 是本地开发启动入口
│   ├── config/            # 配置模板；真实配置不要提交
│   ├── plugins/           # 随项目提供的 Brain 插件
│   └── skill_host/        # 可选的 Node.js Skill Host
├── client/                # Electron 客户端
│   ├── electron/          # Electron 主进程与 preload
│   ├── src/               # 聊天、设置和 Agent Center 页面
│   └── package.json       # npm start 等客户端命令
├── scripts/
│   ├── bootstrap-windows.ps1 # 检查并安装项目依赖
│   └── start_dev.bat         # 从仓库相对路径启动 Brain 和 Electron
├── .venv/                 # bootstrap 生成的 Python 虚拟环境
├── client/node_modules/   # npm ci 生成的客户端依赖
└── .local-dev/            # 本机 Brain 配置和数据；由开发者本地创建
```

`.venv`、`client/node_modules` 和 `.local-dev` 是本机生成内容。不要把真实 API Key、Brain Token、模型配置或运行数据提交到 Git。

## 新电脑安装

先安装 Git、Python 3.12 或更高版本，以及 Node.js 20 或更高版本（需包含 npm）。bootstrap 脚本只安装仓库依赖，不会安装系统软件。

在 PowerShell 中执行：

```powershell
git clone --branch dev https://github.com/limingyi2/vigil.git
Set-Location vigil

# 检查本机工具版本和命令；不会安装依赖或修改文件。
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -CheckOnly

# 创建 .venv、安装 Brain/PDF/测试依赖，并按 lockfile 安装 Electron 客户端依赖。
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -WithDevDependencies
```

如果 Python 不在 PATH 中，给 bootstrap 指定已安装的解释器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -Python "C:\Tools\Python312\python.exe" -WithDevDependencies
```

只做常规运行时开发时，可以省略 `-WithDevDependencies`；需要本地打包时再加 `-WithPackaging` 安装项目内的 PyInstaller。某一侧依赖已准备好时，可用 `-SkipPython` 或 `-SkipNode` 跳过对应安装。

## 创建本机开发配置

首次启动前，在仓库根目录打开 PowerShell，创建独立的 `.local-dev` 家目录和仅监听回环地址的配置。此配置不包含模型密钥；有模型后可在客户端的模型管理中添加自己的开发配置。

```powershell
$repoRoot = (Get-Location).Path
$localHome = Join-Path $repoRoot '.local-dev'
$configDir = Join-Path $localHome 'config'
$configFile = Join-Path $configDir 'config.json'
New-Item -ItemType Directory -Force $configDir | Out-Null

if (-not (Test-Path -LiteralPath $configFile)) {
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  $randomBytes = New-Object byte[] 32
  $rng.GetBytes($randomBytes)
  $rng.Dispose()
  $localConfig = @{
    bind_host = '127.0.0.1'
    brain_token = [Convert]::ToBase64String($randomBytes)
    api_key = ''
    model = ''
    model_mode = 'cloud'
    chat_mode = 'api'
    vision = @{ enabled = $false }
    proactive = @{ enabled = $false }
    management_agent = @{ enabled = $false }
  } | ConvertTo-Json -Depth 4
  # 使用无 BOM 的 UTF-8，确保 Python 配置读取兼容。
  [IO.File]::WriteAllText($configFile, $localConfig, (New-Object Text.UTF8Encoding $false))
}

$localPlugins = Join-Path $localHome 'plugins'
if (-not (Test-Path -LiteralPath $localPlugins)) {
  Copy-Item -LiteralPath (Join-Path $repoRoot 'brain\plugins') -Destination $localHome -Recurse
}
```

自动生成的 Brain Token 保存在 `.local-dev` 中，仓库忽略规则已排除该目录。后续启动继续使用同一配置和数据。不要把生产环境的 `config.json`、`data/`、Electron 用户数据或凭据复制到这个开发目录。

## 启动 Brain 和 Electron

在仓库根目录设置启动环境，然后运行仓库内的启动脚本：

```powershell
$repoRoot = (Get-Location).Path
$env:VIGIL_HOME = Join-Path $repoRoot '.local-dev'
# bootstrap 创建 .venv；将其中的 Python 放到 PATH 前面供启动脚本使用。
$env:PATH = (Join-Path $repoRoot '.venv\Scripts') + ';' + $env:PATH
$env:VIGIL_PORT = '8788'
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue

.\scripts\start_dev.bat
```

`start_dev.bat` 根据脚本自身位置计算仓库根目录，启动 `brain/server_launch.py`，等待三秒后在 `client` 目录执行 `npm start`。它不依赖仓库所在的固定路径。这里将本机 Brain 端口设为 `8788`，Electron 可以自动探测。可在浏览器打开 `http://127.0.0.1:8788/api/health` 确认 Brain 已就绪。

如果 `8788` 已被占用，将 `VIGIL_PORT` 改成另一个空闲端口。然后在客户端「设置 → 连接」中填写 `http://127.0.0.1:<端口>` 并测试连接。不要为了释放端口而停止不相关的进程。

本机配置没有模型密钥，因此应用和聊天页面可以启动；要生成模型回复，请在「设置 → 模型管理」中添加模型。基础 Brain/Electron 启动不依赖 Skill Host；需要使用技能时，再生成仅供本机使用的 `ZX_SKILL_AUTH_TOKEN`，并让 Brain 与 Host 进程使用同一个值。

停止开发时关闭 Brain 控制台。Electron 窗口关闭后可能会缩到系统托盘；需要完全退出时，在托盘菜单中选择「退出」。

## 常见问题

- **提示找不到 Python 包：**确认 bootstrap 成功，并在启动 PowerShell 中把 `.venv\Scripts` 放在 PATH 前面后再运行 `start_dev.bat`。
- **Electron 被当成普通 Node 运行：**清除 `ELECTRON_RUN_AS_NODE` 环境变量，再重启客户端。
- **客户端未连接 Brain：**检查 Brain 控制台的启动结果、`VIGIL_PORT` 和客户端设置中的服务地址；本机直连地址使用 `127.0.0.1`。
- **首次使用本地知识检索较慢：**相关嵌入模型可能会在首次运行时下载，需要网络连接。
