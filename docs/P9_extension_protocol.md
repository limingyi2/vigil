# P9 扩展通信与兼容契约（提案，P9.0）

状态：供审核的目标契约；**本阶段没有实现安装器、扩展进程隔离或新 RPC**。现存路由和 UI 仍按本文“当前已证实”部分运行。

## 1. 当前基线和边界

```mermaid
flowchart LR
  UI[Electron Renderer] -->|受限 IPC| Core[Electron Main / Vigil Core]
  Core -->|现有 HTTP IPC| WH[wallpaper-host.js]
  WH -->|loopback HTTP + 随机 capability| WE[捆绑的 Wallpaper Engine]
  Core -->|本机 API| Brain[Brain Python]
  Bridge[独立 WeChat Bridge 运行机] -->|当前 /api/chat 与 /api/plugins| Brain
  Brain -->|可选 /ws/brain| Forward[本机 Brain 可选转发]
  NodeForward[client/electron/forward.js] -. 当前未由 main.js 启用 .-> Forward
```

当前 Electron 启动时加载 `client/src/plugins/wallpaper.js`，主进程创建 `client/electron/wallpaper-host.js`，其宿主在首次请求时启动并从 `client/vendor/dsh-wallpaper-engine/` 加载运行库；数据位于 Electron `userData/wallpaper-engine`。Electron 构建 glob 包含 `electron/**`、`src/**`、Wallpaper vendor，故 Wallpaper 客户端代码与引擎当前在核心安装包中。Brain 的 `brain/plugin_host.py` 会扫描 `brain/plugins/`，`brain/plugins/wallpaper/__init__.py` 注册旧 `/api/plugin/wallpaper/...` 路由，构建 spec 也递归收集该目录。旧 Brain 插件与 Electron 当前 Wallpaper 页面并无已确认的调用关系；保留旧路由直到外部调用者清点完成。

当前渲染器通过 preload 暴露 `wallpaper.request/importFile/directory`，主进程接收相应 IPC。宿主只监听 loopback，使用随机 session secret/capability；但主进程 Wallpaper IPC handler 尚无明确的扩展身份或 sender/frame 授权检查。新扩展不得直接复用这些高权限入口；未来须由 Core 校验调用来源、权限和目标操作。

WeChat Bridge 是运行在独立桥接机器上的渠道服务。公开 GitHub 仓库 `limingyi2/zhixia-wechat-bridge` 的 `master` 是独立发行项目的源码仓库；Vigil 仓库的 `wechat-bridge/wxauto_bridge.py` 是独立副本/快照，当前 Git blob 与公开 `master` 的同名文件不同（本次读取到的远端 blob 为 `13790ddba69a9a9536d753943cc6ff7f9eb535b9`，本地 blob 为 `a4d650a939117a5aa7dbec9bd795a8978ec335e1`）。这证明副本存在漂移；仓库间自动同步/发布规则尚未找到，P9.1 前必须明确后续修改的权威源和同步责任。实际部署路径依赖桥接机配置，Vigil 的 `deploy/wechat/deploy_bridge_m4.py` 与 `restart_bridge_m4.py` 是运维脚本，含旧路径假设；P9.0 不运行它们。当前 Vigil 副本中，Bridge 经 `/api/chat`、`/api/plugins` 和配置凭据连接 Brain；用户聊天使用 `src=wechat` 或 `src=wechat_group`。Brain 当前把未认可的 src 归一化为 `wechat`，所以今后协议必须明确渠道标识注册/白名单规则，不可依赖任意字符串形成隔离边界。

`brain/ws_forward.py` 是 Brain 当前可选 WebSocket 服务端；微信路由为 local 且相关在线条件成立时使用。`client/electron/forward.js` 是可单独调用的 Node 客户端，但当前 `client/electron/main.js` 不加载或启动它。双 Brain 转发是单独可选能力，既不是 Wallpaper 桌面扩展，也不是 WeChat Bridge 渠道桥接本身。

## 2. 目标职责

| 部件 | 唯一职责 | 不应获得的权力 |
|---|---|---|
| Core | 安装与生命周期编排、版本和签名校验、权限授予、IPC 代理、用户数据位置与恢复记录 | 不托管微信登录态；不默认允许扩展任意文件访问、任意 IPC、插件管理或 Brain 管理 |
| 桌面扩展（Wallpaper） | 通过受限桌面扩展 API 提供界面和 Wallpaper 操作 | 不直接访问 Core 私有 API、任意路径、shell 或 Brain 管理接口 |
| 独立渠道桥接服务（WeChat Bridge） | 在专用运行机管理渠道适配、消息收发和本机登录态；通过渠道 RPC 请求 Brain 对话 | 不把微信桌面登录态搬入 Core；不获得 Brain 管理/任意工具权限 |
| 双 Brain 转发 | 作为单独启用的可选能力转发指定渠道的对话请求及结果 | 不与 Bridge 生命周期、插件 ID 或渠道凭据合并 |

## 3. 标识、Manifest、版本与健康状态

- 扩展 ID 全局稳定、小写、反向域名风格；发布后不复用、不因改名改变用户数据归属。
- 版本使用 SemVer。`min_core` 是最低兼容 Core 版本；`protocol.name` 由类型绑定，`major` 不兼容时拒绝加载，较低 `min_minor` 可协商。Core 同时验证当前版本满足 `min_core`。
- Manifest 使用 [严格 Schema](schemas/extension-manifest.schema.json)。类型只有 `desktop-extension` 与 `channel-bridge`；平台包必须提供 SHA-256。Schema 采用 `additionalProperties:false`，未声明字段拒绝。权限按类型用有限枚举；未知权限、未知类型一律拒绝，不可静默放行。
- 签名可证明发行来源和内容未被替换，**不能替代运行时隔离**；Schema 合法也不证明代码可信。安装时还须校验来源策略、哈希、包内路径和权限授予。Manifest 不得自行要求任意文件、任意 IPC、shell、Brain 管理权限。
- 健康探测由宿主发起，返回 `{extension_id, version, protocol, state, checked_at}`；不返回凭据、微信标识或用户内容。桌面扩展探测由 Core IPC ping 完成；远程渠道服务探测为经认证的 `health.get`，响应需在 2 秒内且只含存活/协议状态。健康失败不自动重置用户配置。

## 4. 生命周期状态语义

| 操作 | 成功状态 | 失败/回滚语义 |
|---|---|---|
| 安装 | `installed-disabled`：包已验证并落入程序目录，尚未启动 | 下载、签名/哈希、Schema 或路径校验失败时不切换活动版本；清理仅限本次 staging 程序目录 |
| 启用 | `enabled`：权限已由用户确认，进程/界面可用，健康检查通过 | 启动或健康检查失败则恢复上个活动版本并保留数据，转为 `installed-disabled`，记录脱敏原因 |
| 禁用 | `installed-disabled`：停止新请求、取消在途请求、关闭 IPC/RPC 会话 | 超时则隔离进程并报告；不删除用户数据、凭据或历史 |
| 更新 | `updating` → 原子切换到新验证版本 → `enabled`（若原先启用） | staging、迁移或健康检查失败时回切旧程序版本；迁移数据保留双份/快照直到确认 |
| 卸载 | `removed`：程序文件与注册信息移除 | 用户数据默认保留；只删明确的程序目录。清理用户数据必须是单独、二次确认且有导出/备份的动作 |

升级/卸载须对活动版本采用原子目录切换；路径解析后须确保目标位于该扩展自己的程序根目录。迁移脚本不得自行指定绝对路径或跨越用户数据根目录。

## 5. RPC 与 IPC 消息格式

传输层按类型分离：桌面扩展经 Core 管理的 Electron IPC；远程桥接经 TLS HTTP/WebSocket 到 Brain/Core 网关。所有请求有请求 ID、截止时间和取消语义；传输层凭据必须放在安全凭据存储或受保护的头部，不放 URL、Manifest、参数日志或聊天文本。

```json
{
  "protocol": { "name": "vigil-channel-bridge", "major": 1, "minor": 0 },
  "request_id": "req-example-001",
  "extension_id": "com.example.vigil.wechat-bridge",
  "method": "brain.chat.create",
  "deadline_ms": 8000,
  "params": { "channel": "wechat", "text": "示例消息" }
}
```

成功响应为 `{request_id, ok:true, result}`；错误为 `{request_id, ok:false, error:{code,message,retryable}}`。错误码固定集合：`INVALID_REQUEST`、`UNAUTHENTICATED`、`FORBIDDEN`、`UNSUPPORTED_PROTOCOL`、`NOT_FOUND`、`CONFLICT`、`RATE_LIMITED`、`DEADLINE_EXCEEDED`、`CANCELLED`、`UNAVAILABLE`、`INTERNAL`。`message` 面向用户且不得包含凭据或私密标识；日志只记录 ID、方法、错误码、耗时和脱敏状态。

- 请求超过 deadline 后 Core 发送 cancel 并丢弃迟到响应；取消是尽力而为，发送操作需幂等键避免重复消息。
- 客户端不在断连后自动重放非幂等的发送操作。退避重试仅用于健康探测和幂等读取。
- 同主版本且服务端支持请求使用的字段时允许 minor 协商；未知方法/必需字段拒绝。Major 不同拒绝，不允许静默降级成更宽权限。
- 现有 Brain `/api/chat` 与 `/api/plugins`、Bridge 的 `msg/ack/result` 及 `ws_forward` 不等于本契约的新 RPC；适配器必须显式映射、限制超时和渠道字段，并保留旧端直到所有调用者迁移。

## 6. 权限与渠道隔离

桌面权限以操作为最小粒度，例如 `desktop.wallpaper.control`、`desktop.ui.contribute`；桥接权限以已登记渠道为边界，例如 `channel.events.read`、`channel.messages.send`、`brain.chat.invoke`。用户授权是每次安装/权限提升的显式动作；Manifest 只是申请，不是授予。Core 不接受扩展声明 `filesystem.*`、`ipc.*`、`brain.admin` 等任意能力。用户数据访问由 Core 专用接口提供，不由声明路径决定。

Bridge 必须继续分别传递 `pc`、`wechat`、`wechat_group` 渠道上下文；人设、历史、记忆及授权按渠道隔离。私聊 owner 校验、群聊 safe-only、remote control 默认关闭是不可削弱的不变量。桥接服务出错或身份不明时 fail closed，不升级为高权限。

## 7. 双协议迁移和退出条件

迁移期间可由同一适配器同时支持旧接口和新协议，但须按客户端版本/能力显式选择，禁止一次请求在两个协议上重复产生副作用。双写只允许不可变诊断记录或经过幂等设计的数据；不对外重复发送消息。

旧接口退出条件需逐项满足：调用方清单覆盖桌面、Bridge、脚本和运维工具；所有调用方升级到新协议并在生产外兼容验证；至少一个发布周期无旧协议成功请求；数据/凭据迁移可回滚且恢复演练通过；明确公告移除版本。达到条件后才在后续阶段删除兼容适配器。本 P9.0 不停旧实现。

## 8. 已证实与待验证

已证实：Wallpaper 在当前 Electron 客户端初始化并随核心包打入；旧 Brain Wallpaper Plugin 会装载但未确认被当前 UI 调用；Bridge 源码位于本仓库 `wechat-bridge/` 且另有公开独立仓库；`brain/plugins/gate.py` 是当前 Brain 插件宿主内的实现，`wechat-bridge/brain-plugin/gate.py` 是显著落后的副本；当前 JS `forward.js` 未启用。

待验证：Bridge 的生产部署具体使用公开仓库版本、Vigil 副本还是额外运行副本；两份 gate 的线上部署来源；旧 Wallpaper API 的外部调用者；实际 Bridge 部署的当前路径和调用方。不得据目录名或文档断言这些实现已经废弃。
