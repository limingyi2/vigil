# Creator 实机排查与修复（2026-10-16）

## 根因与版本证据

- 客户端实际运行 Vigil 1.0.3 / code 20，Electron 28.3.3。
- 源码基线为 P2 `49ac370314fe485739dd68aac05946bc3bbb321a`（`e330d76` 的版本提交）。
- 客户端配置中的显式 sidecar 路径优先于安装包内置 Brain；因此升级客户端后，仍启动 10 月 15 日的旧独立 Brain。
- 旧进程 PID 27740、地址 `http://127.0.0.1:61285`；已认证 GET `/api/creator/jobs` 返回 404 `no such creator api`，POST 返回 404 `no such api`。无效 kind、空需求的隔离探针不会触发模型。
- 同样使用客户端现有凭据存储的认证重复确认上述两个 404；没有绕过认证。
- 旧 EXE 的嵌入 `server` 模块缺少 `/api/creator/jobs` 字符串。新 `server.py::_creator_admin` 已注册此路由；旧 GET 落入 Creator 未知路由，旧 POST 落入通用未知 API 分支。这不是 MiMo 返回的错误，也不是新源码缺路由。
- 此次没有发现就绪后回退到其他 Brain：sidecar PID 与 health PID 一致。配置中的备用 base 与实际 API 目标不同，以实时 IPC 返回的目标为准。

## 最小修复

- 备份旧 EXE、运行库、客户端 ASAR；使用已验证的 1.0.3 内置 Brain 更新显式 sidecar 路径下的 EXE 与运行库，保留原 config/data 和已安装插件。配置文件更新前后 SHA-256 相同。
- 运行时 Brain 不提供语义版本/commit 接口，因此通过 EXE SHA-256 与内置构建逐字节比对、并比较嵌入的 server/model_hub/creator_jobs/model_providers.cloud 代码对象确认版本：`8bc2e14c88508081b1aaf9c5d304caa5a0579816189fe26d13b3cb2a5917777d`。
- IPC API 响应附带实际目标 origin，去掉 URL 用户信息、路径、查询参数；前端保留 HTTP 状态。
- Creator 创建/修改遇到确定的 4xx（408 除外）持久化为 failed。404 明确显示接口缺失、HTTP 状态、当前目标和版本/路径检查建议。
- 网络断开、超时和服务端错误仍保留未确认状态，避免自动重发导致重复生成。已有历史 pending 缺少当时 HTTP 证据，未批量猜测或重发。
- 错误显示使用 textContent；不回显 provider 原始响应、Token 或 API Key。不修改任务创建、审批及插件能力。

## 实机验证

- 修复后 sidecar ready，PID 38052，API 目标 `http://127.0.0.1:61168`；health 返回相同 PID、frozen=true。
- 通过正在运行的 Electron preload/主进程 IPC，使用现有身份：GET jobs 200；无效 POST 400（参数验证命中，无模型调用）。
- 当前 Brain 的模型接口返回 provider=xiaomi、model=mimo-v2.6-flash、has_key=true、key_belongs_to=xiaomi；未打印密钥内容。
- 单独 session `creator-runtime-check-20261016`，固定 request ID `probe_mimo_creator_20261016_once`；先查已有作业，仅在不存在时提交一次。隔离需求只生成无副作用验收插件草稿，不安装或启用。
- job `job_9269cfc9f96249c6b449d51dd8944a44`：succeeded；finish_reason=stop；prompt_tokens=494、completion_tokens=185、total_tokens=679。
- draft `cr_20261016230323_5e3314` 保留为验收证据，未安装。
- Node 回归 10/10 通过；实际 Electron 隔离测试验证 20 轮聊天、7 类 Creator 错误、导航恢复，以及 404 的 failed 持久化、HTTP/目标展示和不重发。

- 最后正常重启（关闭临时调试端口）后，Brain PID 33116、地址 `http://127.0.0.1:52575`，health 200；原隔离作业 GET 200 且仍为 succeeded，没有重新提交。动态端口每次启动可能变化。
- 本地全量 pytest JUnit：952 tests、941 passed、11 skipped、0 failures、0 errors。

## 交付范围与限制

本机修复客户端 ASAR 与显式 sidecar 的二进制匹配；源码补丁提交到 P2。没有合并 master，没有发布替换安装包，也没有部署云端 Brain。当前发布的 1.0.3 安装包本来含正确 Brain，但旧的外部 sidecar 配置会覆盖它。未来升级仍需检查显式 sidecar 是否与客户端配套；运行时 build/能力版本握手可作为后续独立改进。
