# 外部应用接入与重启生效的本地补丁

此功能随 1.1.10 首次整包升级交付。旧版本必须先升级一次；之后支持范围内的客户端改动可以由 agent 暂存，完全退出并重启后加载，无需再次运行安装程序。关闭窗口只会隐藏到托盘，需要从托盘菜单退出。

## 外部应用

Agent 先查 `capability_status("external_apps")`，通过 `capability_call` 调用下列操作。配置保存在 Brain 用户目录 `config/external-apps.json`，独立于安装目录。

| 操作 | 参数 | 行为 |
| --- | --- | --- |
| `list` | `{}` | 返回应用、配置 revision、上次验证状态和工具 schema；不执行外部程序 |
| `register` | `{id, config}` | 保存 MCP / CLI / instruction skill 配置；覆盖配置后必须重新验证 |
| `probe` | `{id, revision}` | 真正连接、初始化并发现工具；CLI 实际执行版本检查 |
| `call` | `{id, revision, tool, arguments}` | 重新连接并执行指定工具；失败不会自动重试 |
| `install_skill` | `{path, id, description?}` | 从本机绝对路径导入 SKILL.md 或 Vigil JS Skill ZIP |
| `skill_read` | `{id, revision}` | 返回已安装指南及配套资源目录；不执行资源里的脚本 |
| `remove` | `{id}` | 删除注册信息，不卸载 CLI、不撤销外部账户授权 |

除 `list`、`skill_read` 外，操作全部复用现有 Capability Registry 一次性审批。`revision` 从 register/list 返回值复制，防止等待批准时配置改变后执行另一个程序。MCP 工具说明、结果和 Skill 内容属于外部数据，不能自行扩张授权。

CLI 配置示例：

```json
{"id":"ffmpeg","config":{"kind":"cli","description":"本地视频处理","command":"C:/tools/ffmpeg/bin/ffmpeg.exe","args":[],"probe_args":["-version"],"timeout":60}}
```

验证成功后可调用 `call`，例如 `{id:"ffmpeg", revision:"返回值", tool:"run", arguments:{argv:["-version"]}}`。执行不经过 shell；Windows 的 npm `.cmd` 包装器应换成 `node.exe` 加实际 CLI 脚本路径。安装依赖和交互登录沿用 `local_exec` 与应用自己的授权流程，不把“安装完成”报告成“账户已连接”。

MCP 支持官方 Python SDK 的 stdio、Streamable HTTP 和旧版 SSE。stdio 配置含 `kind:"mcp", command, args, cwd?, env?`；HTTP/SSE 配置含 `kind:"mcp", transport:"http"|"sse", url, headers_env?`。远程地址要求 HTTPS，HTTP 仅限回环。密钥通过环境变量引用传入：例如 `env:{API_KEY:"MY_APP_API_KEY"}` 或 `headers_env:{Authorization:"MY_APP_AUTHORIZATION"}`；引用的变量必须已存在，Authorization 变量内容包含所需的 `Bearer ` 前缀。不会把密钥值写入应用清单。此版本不内置通用 OAuth 登录向导，需要先完成应用的登录或提供环境变量。

SKILL.md 作为任务指南及其同目录资源一起导入，最大 16MB/1000 文件，忽略 `.git`、`node_modules`、`.venv`、`__pycache__`，拒绝链接。资源存放在 Brain `data/instruction_skills/`。指南不会自动增加文件或账户权限；执行脚本仍需工具授权。JS Skill ZIP 走已有宿主安装事务和装后核验，宿主离线时如实失败。

## 本地客户端补丁

Agent 使用本地工具 `local_patch_status` 获取 `base_id` 与格式。`local_patch_stage` 和 `local_patch_rollback` 属于危险工具，遵循现有本地操控设置；完全访问模式包含这两个工具，其余模式需启用对应工具。

补丁目录格式：

```text
my-patch/
  patch.json
  files/
    electron/main.js
    src/js/pages/video-workbench.js
```

```json
{"schema":1,"base_id":"local_patch_status 返回的 base_id","files":[{"path":"electron/main.js","before_sha256":"当前生效文件的 SHA-256","sha256":"files/electron/main.js 的 SHA-256"}]}
```

新增文件的 `before_sha256` 必须为 null。Agent 应先完成所需测试，再调用 `local_patch_stage({path:"补丁目录绝对路径"})`；只有返回 `ok:true,restart_required:true` 才报告“已准备，重启后生效”。文件哈希、路径、JS/JSON 语法和完整快照在暂存时验证，启动时再次校验完整快照。

允许修改 `electron/` 和 `src/` 内的 JS/CSS/HTML/JSON；不允许修改 bootstrap、补丁加载器、package/build/version 元数据、原生依赖、EXE 或随包 Brain。此类更改仍需完整更新，以保持客户端与 Brain 的构建身份一致。EgoAgent 接口白名单修复属于支持的 JS 改动。

补丁保存为用户目录 `local-patches/versions/<id>/app/` 的完整应用快照，原始 app.asar 不被覆盖。启动器在下一次启动选择已暂存快照；随包 Brain 通过现有身份和接口健康检查后确认该补丁健康。若启动未确认健康，下次启动恢复上一版；`local_patch_rollback` 可主动安排下次启动回退。健康确认后发生的任意运行时故障不会自动判定为补丁错误。完整安装更新后，旧构建的补丁不会加载。历史快照保留以便诊断，会占用额外磁盘空间。

## 验证

```powershell
venv/Scripts/python.exe -m pytest tests/test_external_apps.py tests/test_localtools.py tests/test_p17_capability_tools.py tests/test_p20_2_capability_execution.py -q
node --test client/tools/test-local-patches.js client/tools/test-plugin-resource.js client/tools/test-unified-runtime.js
node client/tools/test-local-patches-electron.js
# 在 client 目录中使用 Electron 运行 tools/test-ego-service-electron.js
```

协议实现依据：[官方 MCP Python SDK 客户端文档](https://github.com/modelcontextprotocol/python-sdk/blob/v1.x/docs/client.md)。真实外部账户和具体第三方应用仍需逐项连接验收；本地协议测试不代表这些账户已经登录。
