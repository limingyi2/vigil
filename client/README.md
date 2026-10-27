# 客户端开发

客户端使用 Electron 和原生 JavaScript。主进程管理窗口、Brain 子进程、本机工具、更新和凭据；渲染层通过 preload 提供的接口访问这些能力。

## 环境与启动

使用 Python 3.12 和 Node.js 22。在仓库根目录安装 Brain 依赖，在 `client/` 安装前端依赖：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r brain/requirements.txt
cd client
npm ci
npm start
```

如果终端设置了 `ELECTRON_RUN_AS_NODE`，先移除该环境变量，否则 Electron 会按普通 Node 进程运行：

```powershell
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
npm start
```

安装版由 `sidecar.js` 启动随包的 Brain。源码运行需要可用的 Brain 构建产物，或在连接设置中指定独立运行的本地 Brain。不要根据固定端口判断托管 Brain 是否已启动，应查看本地内核状态和启动日志。

独立调试 Brain 时，在仓库根目录复制并编辑示例配置，然后运行：

```powershell
Copy-Item brain/config/config.example.json brain/config/config.json
.\.venv\Scripts\python.exe brain/server.py
```

## 主要目录

| 位置 | 内容 |
| --- | --- |
| `electron/bootstrap.js` | 应用启动与本地补丁选择 |
| `electron/main.js` | 窗口、托盘、配置和 IPC 接口 |
| `electron/preload.js` | 渲染层接口白名单 |
| `electron/sidecar.js` | Brain 进程、就绪检测与启动诊断 |
| `electron/localtool.js` | 本机工具、路径和命令权限 |
| `electron/plugin-resource.js` | 插件资源访问 |
| `electron/video-media.js` | 视频文件票据、Range 请求和转码 |
| `electron/update-manager.js` | 更新状态与下载校验 |
| `electron/public-updates.js` | GitHub Release 和更新清单读取 |
| `src/js/pages.js` | 工作台、聊天与通用页面 |
| `src/js/pages/` | 数据、视频与插件市场等页面模块 |
| `src/js/agent-center.js` | Agent Center |
| `src/js/memory-atlas.js` | 记忆星图接入 |
| `tools/` | Node 与 Electron 验收脚本 |

## 数据与凭据

托管 Brain 的数据目录由 `app.getPath('userData')` 下的 `brain/` 提供，通过 `VIGIL_HOME` 传给子进程。程序安装目录用于存放可替换的应用文件。

渲染层不直接持有 Brain 访问令牌。网络请求经主进程代理；新增 IPC 接口时需要同时检查参数、调用来源与权限。普通浏览器预览不具备本机工具能力。

本机工具使用只读、默认和全部允许等权限模式。操作是否需要确认由当前设置决定，不能在文档中笼统承诺每次写操作都会弹窗。

## 检查

在仓库根目录运行客户端的基础回归：

```powershell
node --test client/tools/test-p2-stream-lifecycle.js client/tools/test-unified-runtime.js client/tools/test-public-updates.js
node --check client/src/js/pages.js
```

涉及 preload、SSE、页面恢复、插件资源或安装目录的改动，需要补充相应的 Electron 验收。用例应使用独立用户目录，避免读取或修改真实会话。

## 构建

完整安装包从仓库根目录构建：

```powershell
.\.venv\Scripts\python.exe scripts/build_release.py --candidate
```

`npm run pack` 和 `npm run dist` 只负责 Electron 部分，不能单独证明随包 Brain 与客户端的版本一致。正式发布流程见 [打包与更新](../docs/03-客户端/客户端打包与推送更新流程.md)。
