# Vigil

Vigil 是一个 Windows 桌面 Agent 工作台。Electron 提供界面，本地 Python Brain 负责模型请求、工具执行和数据存储。

你可以用它进行带图对话、执行本机任务、检索本地资料、切换代码或写作专家，也可以安装插件和技能。模型接口需要自行配置；使用远程模型时，对话内容会发送给对应的服务商。

[下载 Windows 安装包](https://github.com/limingyi2/vigil/releases/latest) · [使用说明](docs/USER_GUIDE.md) · [开发源码](https://github.com/limingyi2/vigil/tree/dev) · [更新日志](CHANGELOG.md)

## 安装与使用

1. 在 Releases 中下载 `Kestrel.Setup.<版本>.exe` 并安装。Kestrel 是客户端的构建名称。
2. 打开设置，填写模型服务商、接口地址、模型名和 API Key。
3. 在工作台开始对话。需要操作文件或执行命令时，先选择本机工具的权限模式。

更新入口在客户端左下角，也可在关于页检查更新。安装包尚未签名；文件大小和 SHA-256 记录在每次发布的 `release-manifest.json` 中。

![Vigil 工作台界面示意](docs/assets/workspace-preview.png)

*截图使用测试数据，界面以当前安装版为准。*

## 功能

| 功能 | 说明 |
| --- | --- |
| 对话与任务 | 流式回复、图片输入、工具执行记录、取消和任务状态查询 |
| 专家模式 | 代码与写作专家，可在人格与语气页面启用或卸下，保留原有对话历史 |
| 本机工具 | 文件、目录、命令等操作，受客户端权限设置与执行范围约束 |
| 知识库与记忆 | 知识库引擎与混合检索、记忆管理和只读星图；知识库管理页需要已安装的 kb 插件 |
| 视频工作台 | 本地视频播放、视频检索与问答；分析功能需要另行配置 EgoAgent 服务 |
| 插件与技能 | 插件市场、按清单链接安装、Python 插件、JavaScript 技能和指令型 Skill |
| 外部应用 | 注册和验证 MCP 或 CLI 接入；账户登录和第三方授权仍需分别完成 |
| Creator | 生成和修改插件草稿，查看作业、快照与安装结果 |

桌宠及关联的 Agent Center 改版仍在 `zk` 分支，不包含在公开安装包中。

## 运行方式

安装版由 Electron 启动随包 Brain，使用动态本机端口。配置、会话、记忆、任务与已安装插件保存在客户端用户目录下，独立于程序安装目录。实际位置可以在本地内核状态中查看。

源码版默认将运行数据放在 `brain/` 下，也可用 `VIGIL_HOME` 指定独立目录。模型 API、插件下载、已配置的外部应用和可选连接功能可能访问网络；数据保存在本地不代表运行过程完全离线。

## 从源码运行

开发代码在 `dev` 分支。需要 Python 3.12 和 Node.js 22；Windows 安装包使用 GitHub Windows runner 构建。

```powershell
git clone --branch dev https://github.com/limingyi2/vigil.git
cd vigil
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r brain/requirements.txt
Copy-Item brain/config/config.example.json brain/config/config.json
# 编辑本地模型配置后，在独立终端运行 Brain
.\.venv\Scripts\python.exe brain/server.py
```

另开终端启动客户端：

```powershell
cd client
npm ci
npm start
```

源码调试与 Brain 连接方式见 [客户端开发说明](client/README.md)。测试和提交要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档

- [使用说明](docs/USER_GUIDE.md)：模型、工具权限、专家、资料与扩展。
- [架构说明](docs/ARCHITECTURE.md)：进程、模块、数据和执行流程。
- [运维手册](docs/OPERATIONS.md)：数据目录、备份、更新与故障排查。
- [发布流程](docs/03-客户端/客户端打包与推送更新流程.md)：版本、安装包、验收与更新清单。
- [文档索引](docs/README.md)：接口契约、专题说明与历史资料。

## 使用范围

取消任务会停止后续工具派发，已经开始的外部操作可能继续运行。进程重启不会自动重放中断的工具操作。

插件安装会验证清单、来源与文件哈希，但不替代对插件行为的审查。专家模式只调整提示词和可用工具范围，不会增加文件或账户权限。涉及外部账户的功能需要在对应应用中完成登录。

仓库提供示例配置。实际 API Key、Brain 令牌、用户资料和日志不应提交到 Git。凭据处理与历史克隆注意事项见 [维护说明](PRIVACY_MAINTENANCE.md) 和 [轮换清单](TOKEN_ROTATION_CHECKLIST.md)。
