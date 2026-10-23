# 微信群桥接

让**指定的人格**在微信群里以真人方式接话。人格是 `preset_group` 这类配置项，
名字由人格自己定义（本项目当前用的是"麦麦"），不是项目名。

桥接服务可部署在独立 Windows 运行机；云主机或虚拟机的实际部署状态
需要查看相应环境。P9.1 只核验源码与公开仓库 revision，没有连接或验证远端运行机。

## 快速上手

- **要部署到虚拟机** → 看 [`docs/部署手册.md`](docs/部署手册.md)
- **想了解为什么这么设计** → 看 [`docs/技术约束.md`](docs/技术约束.md)
- **要改配置** → 复制 [`config-example/wxbridge.local.example.json`](config-example/wxbridge.local.example.json)
  到机器的 `C:\zx\wxbridge.local.json` 再改

## 目录

Vigil 仓库保留了本目录的源码快照；WeChat Bridge 本身是单独部署的渠道服务，
只负责读取/发送微信消息并请求 Brain 对话。微信客户端登录态留在桥接运行机；
对话历史、人设和记忆由所连接的 Brain 按渠道处理，不随 Core 安装包迁移。

```
wxauto_bridge.py              桥接本体：读消息 → 问大脑 → 发消息
brain-plugin/gate.py          云端发言闸门（装到云端 brain/plugins/）
config-example/               脱敏配置示例（真配置含凭据，不进仓库）
docs/                         部署手册 / 技术约束 / 虚拟机脚本说明

vm/                           虚拟机侧脚本
├─ zx.cmd                    ★ 主入口：一键装包 → 开隧道 → 起桥接
├─ GO.cmd                    解压安装包 + 建桌面快捷方式（发给用户的第一入口）
├─ FIX_AND_RUN.cmd           安装出错时的补救
├─ zx_launcher.py            启动器：唤醒无障碍闸门 → attach → 拉起桥接
├─ zx_uia_wake.py            微信 4.x 无障碍闸门唤醒
├─ zxrdp.cmd                 连虚拟机自带的远程桌面
├─ vbox/                     虚拟机本体：装 VirtualBox / 建虚拟机 / 配置
│  ├─ install_vbox.bat         静默装 VirtualBox 到 D 盘
│  ├─ zxvm.cmd                建虚拟机
│  └─ zxvm2.cmd               配虚拟机（网络 / 共享文件夹 / 串口）
└─ runtime/                  日常运行：隧道保活 + 自测
   ├─ VigilTunnel.bat         保持 127.0.0.1:18787 → 大脑 8788 不断
   └─ VigilSelfTest.bat             加群前先在小号上对一遍话
```

### `vm/` 里为什么分三层

`vm/` 下混过三种东西，放在一起时维护的人得自己猜哪个是"装虚拟机"、
哪个是"日常开隧道"、哪个是"一键启动"。现在按**使用时机**分：

- **根目录** —— 用户会双击的那几个（`zx.cmd` / `GO.cmd` / `FIX_AND_RUN.cmd`）
- **`vbox/`** —— 装虚拟机，机器上只做一次
- **`runtime/`** —— 天天用的（隧道保活、自测）

注意别被名字骗了：安装包里也有一个 `bridge\` 目录
（`C:\zx\bridge\wxauto_bridge.py`），那是**装完之后**的运行位置，
跟源码里的目录不是一回事。打包时会把这些脚本摊平放进安装包。

### 源码与部署

当前 Vigil 仓库中的桥接源码是 `wechat-bridge/wxauto_bridge.py`；本仓库中不存在
`brain/wechat_bot/`。`vm/` 脚本负责桥接运行机的安装/启动，`deploy/wechat/`
下的部分部署脚本保留有旧运行路径假设，执行前必须按目标环境重新审查。

WeChat Bridge 是独立进程，独立发行仓库 [`zhixia-wechat-bridge`](https://github.com/limingyi2/zhixia-wechat-bridge)
是桥接服务源码和发行包的权威来源。Vigil 中的 `wechat-bridge/` 是归档兼容快照，不是 Electron/Core 的运行依赖，
也不会随 Core 安装包发布。快照与上游的核验版本及文件摘要记录在 [`UPSTREAM.json`](UPSTREAM.json)；
更新快照必须显式更新来源 revision、摘要和兼容测试。生产运行版本仍须由桥接运行机自己的部署记录核实，
本仓库不据此声称已部署上游当前版本。

`brain/plugins/gate.py` 是 Vigil 当前 Brain 插件宿主的权威 gate；
`wechat-bridge/brain-plugin/gate.py` 是历史兼容参考，保留旧宿主返回值约定，不属于 Bridge 发行包，
也不应安装到当前 Brain。两者不做整文件覆盖同步：共享的 OPEN/PASS 决策契约由 `tests/test_p9_bridge_contract.py`
回归，Brain 侧否决适配仍使用当前 `Turn.veto()`。Bridge 独立发行与 Brain 内 gate 插件是两个不同交付物。

## 安全闸

当前源码包含登录账号期望值校验和运行中复核逻辑；实际行为取决于桥接运行机本地
配置和当前微信客户端状态。本 README 不记录任何账号标识，也不承诺“绝不会”发生
误配置或账号切换。部署前应按源码中的 fail-closed 检查核对本机配置，并用隔离账号
完成受控验收；不要把账号标识放入 issue、日志或聊天内容。

## 凭据处理

仓库是 **PUBLIC**。真实凭据和机器/账号标识不得进入仓库、Manifest、日志、命令行、
聊天历史或普通 UI。以下示例路径/文件只描述字段类别，不代表本次检查访问了本地配置：

- `brain_token`（等同于密码）
- SSH 私钥 `deploy/sshkeys/`
- `wxbridge.local.json`（含 token、桥接机 IP、群名）
- `*.apk` / `*.exe` / 联调截图

`config-example/` 只用于结构示例。即使示例使用占位符，也应在提交前做凭据扫描。
P9 的扩展迁移和通信边界见 `docs/P9_extension_protocol.md` 与
`docs/P9_data_migration.md`；本阶段没有将 Bridge 安装为 Core 插件。

### `zx.cmd` 里的 token 从环境变量读

一键脚本要打 `api/plugins` 探活，那个 token **不在文件里**，从环境变量读：

```bat
set ZX_BRAIN_TOKEN=<你的 brain_token>
vm\zx.cmd
```

没设的话这一步会打印 `[SKIP]` 并跳过，**不会**当成隧道失败 ——
没配检查项和检查没通过是两回事。

早期版本把 token 直接写在命令行里。仓库是 PUBLIC，同一个文件里已经带着
云机 IP，再顺手写死口令等于把两样一起发出去，所以改成环境变量。

## 相关

- 云端大脑本体、人设、记忆 → 仓库 `brain/`、`docs/微信群聊接入.md`
- 插件系统总览 → `docs/插件系统.md`
# Core 退役说明

本目录保留独立 Bridge 源码与历史部署资料。当前 PC Core 不接收微信渠道，不启动或连接 Bridge；旧 `/api/route`、`/ws/brain` 返回 410。下文部署流程属于历史参考，不能直接作为当前 Core 接入说明。参见 [PC Core 边界](../docs/PC_CORE_LEGACY_WECHAT_CLEANUP.md)。
