# Vigil 大文件模块化审计 R1

审计基线：`dev`，`cc3e143ec132cd6c568b6611fdac2aead5320005`；指定 P19 最终提交 `85f834d1d3f8cad5728a09c09871f0e75d653952` 是基线祖先。本文只建立拆分设计和回归基线，不迁移业务实现。行号对应该基线。

## 结论

`brain/server.py` 同时承担模块兼容导出、Brain 组装、聊天编排和 HTTP Handler，路由类又定义在 `start_remote_api()` 的闭包里。拆分风险主要来自 Handler 方法闭包、共享配置和路由内的流式响应，不是文件本身的体量。应先把可独立调用的纯逻辑和小型领域 API 移出，再拆聊天编排；`start_remote_api()`、嵌套 Handler 和 `run_server()` 暂时保留为唯一装配入口。

`client/src/js/pages.js` 包含共享显示/记录工具、约 2,490 行聊天页面和多种独立管理页面。`app.js` 已集中负责页面注册、导航、清理与插件页面生命周期。先迁出视频工作台、插件市场和数据页面，聊天页面最后处理。页面脚本目前是有顺序的 classic scripts，不能把“新文件位于 `src/`”误认为运行时会自动加载。

## `brain/server.py` 职责和依赖

| 区域（基线行号） | 职责与直接依赖 | 分类与迁移判断 |
|---|---|---|
| imports、`__all__`（1–88） | 导入 `runtime` 共享配置，加载回复/图片/主动任务/后台任务模块；向旧脚本重导出符号 | **必须留在装配/兼容入口**。`__all__` 是兼容面；移动导入会改变导入副作用和初始化次序。先盘点调用方再逐项弃用，R1 不改。 |
| `build_local_brain`、`load_plugins`（92–143） | 动态导入 `Brain`，插件需在构造 Brain 前加载，随后加载历史；依赖模块配置和插件注册 | **Brain/运行时组装方法**，初始化顺序敏感，留在 `server.py`。 |
| 图片辅助（169–295） | 解码、格式检查、模型多模态载荷构造、保存会话图片；解码和格式识别可纯测，保存访问文件系统 | `_native_image_content`、`_supports_native_image`、`_image_ext`、输入受限的 `_decode_turn_image`、`_image_note` 可放 `brain/chat/images.py`；`_save_turn_image` 依赖路径/身份，应显式接收路径和归属，不能标成无状态。保留现有错误与图片事件语义。 |
| 身份、历史、锁、认证（358–587） | `_device_path_allowed`（配置权限）、`_device_role`（设备状态）、`_norm_src`、`_persona_for_src`、历史错误记录/清理、按身份聊天锁、loopback 检查、`_auth_ok`、Brain 历史快照恢复 | `_norm_src`、`_is_loopback`、给定参数的 `_auth_ok`、路径判定规则可纯测；`_device_role` 读 `devices`，历史清理/恢复依赖数据库和 Brain；`_chat_lock` 是模块级共享锁表，带进程状态，均不可作为纯函数。按 `brain/auth.py` 与 `brain/chat/history.py` 拆时显式注入配置、存储和 Brain，不要反向导入 `server`。 |
| `_chat_with_tools`（590–880） | 工具调用循环、历史检索/实时工具、事件发射、任务意图和执行入口；读写 Brain、共享 API 配置、工具/插件状态、追踪输出 | **有状态聊天管线**，不是纯函数。依赖 P19 路由，不应随 HTTP API 拆走。后续放入 `brain/chat/tool_loop.py`，参数显式传入 Brain、工具集合、事件 sink、请求上下文；不要导入 `server`。 |
| `ask_with_retry`（883–999） | 组装聊天请求与重试，处理图像、Persona/来源、历史和模型路由，调用 `Brain`/云端路径并记录追踪 | **Brain/管线方法**，共享配置和历史状态明显；归 `brain/chat/service.py`。迁移前锁住模型请求次数、路由分支、异常/SSE 顺序。 |
| `_query_param`、运行任务统计、云历史、插件热重载（1006–1138） | 参数归一、作业状态/统计、trace 文件读取、插件刷新与 Persona 重载 | `_query_param` 可纯迁移至 `brain/api/request.py`；其余分别依赖作业/trace/插件状态，不能合并成“通用工具”。插件刷新依赖初始化后的 Brain 生命周期。 |
| `_sync_route_get/post`（1141–1264） | 本机兼容/同步路由，将老路径委托给现有 API | 输入归一部分可测试，但 dispatch 对 API 路径、配置和旧客户端协议有依赖。作为 Handler 适配边界留在装配层，待具体路由模块就绪后再薄化。 |
| `start_remote_api`、嵌套 `_Handler`（1267–4832） | Handler 实例的读写、响应、状态码、请求体、SSE；外层闭包捕获 Brain、配置及启动时设置 | **依赖 Handler/Brain/闭包，且必须保留为原装配入口**。不要直接把嵌套方法复制到新文件；新模块应接受显式窄参数，Handler 保留 HTTP 解析/序列化和响应写入。 |
| Handler 辅助（约 1285–2385） | `_attachment_upload`、插件管理、Agent/Workflow、Creator、插件自有路由和通用 JSON/SSE/身份/工作区辅助 | 附件、Agent、Creator、插件路由当前直接使用 Handler 写响应及 auth/workspace helper；可按 API 域拆，但边界先定义“解析/授权/调用/序列化”顺序。插件路由还需保证安装、加载、卸载与 HTTP 路由可见性同一生命周期。 |
| `do_POST`（2386 起）、`do_GET`（4089 起） | 大型路径分发器；聊天 POST、配置写入、模型/模式、工作区、检索、附件、插件、运行/工作流；GET 查询和 SSE | **必须暂留 Handler**。可逐个把非流式 endpoint 的业务处理提取为域函数；POST 聊天与 GET SSE 最后拆，保持响应头、事件类型/顺序、断连清理及状态码。 |
| `run_server`、`main`（4835–4917） | 构造本地 Brain、启动远程服务/后台任务、命令行入口 | **只能留在启动装配层**，避免模块导入时启动线程或改变启动模式。 |

### API 域与安全边界

- **请求/错误处理**：`_query_param`、JSON 解析、错误映射可以先作为窄辅助；但 HTTP 错误必须保留当前状态码和 JSON 形状。请求体读取是 Handler 行为，不能把 `self` 带进通用函数。
- **认证/身份/权限**：Bearer/query token 解析最终依赖配置、来源地址、设备和用户身份。纯比较函数可测试；设备角色、owner/session/workspace 校验有状态。工作区权限必须在 API 层调用既有授权实现，不能因代码移动而改用路径前缀或放宽根目录。
- **配置读写、模型/模式**：配置是 `runtime` 共享对象且 `_refresh_api_cfg` 原地更新供并发线程读取。配置 API 和模型/运行模式 API 可分域，但模块接收 `runtime`/配置接口，不得另存配置副本；保留持久化时机和热切换次序。
- **工作区、RAG 配置、Agent Run/Workflow**：这些路由应只做 HTTP 适配，业务实体现已有 `workspaces`、`agent_runs`、`task_workflows` 等模块。新增 `brain/api/workspaces.py`、`agent_runs.py`、`retrieval.py` 的前提是它们依赖这些领域模块而不反向依赖 `server`。权限由当前 Handler/领域函数传入并维持一致。
- **聊天、SSE、图像/附件**：SSE writer 绑定 socket/Handler，不能提纯；提取纯事件格式化器可以，但不可改变事件顺序或并发时模型请求数。附件持久化已由 `attachments` 模块承担，Handler 只负责接收/授权/响应；图片处理独立于上传附件生命周期。
- **插件 HTTP**：`_plugin_admin`、`_plugin_route` 依赖插件管理状态和 Handler。插件提供的 URL/方法 dispatch 应保持原始路由优先级、通道鉴权、热加载与卸载语义。不要在新模块 import 时扫描或加载插件。

## `client/src/js/pages.js` 职责与依赖

页面文件由 IIFE 共享浏览器全局 `U`、`API` 和 `global`。`U` 提供转义、图标、格式化、toast 等 UI；`API` 提供 `get/post/pick/state`；聊天页还读写 `window.vigil` 的流/桌面桥接。`app.js` 维护当前页、导航序号和销毁回调。页面大体依赖图如下：

```text
index.html classic-script 顺序
  ui.js ─────────────┐
  chart.js ──────────┤
  api.js ────────────┼─> pages.js (共享 helpers + 所有页面 + PAGES)
  agent-state/view ──┘             │
                                    └─> app.js (导航/切换/销毁)
agent-center*.js 在 pages.js 之后修改 PAGES；插件在 boot 后注册/移除页面。

renderChat ─> API/U + 桌面桥接 + 工作区/会话/附件 + SSE/Agent Run/Todo
renderData ─> API/U + chart.js
renderMarket ─> API/U + 插件市场 API + vigil-market-changed 事件
renderVideo ─> API/U + 附件/上传 API + 桌面桥接
其他管理页 ─> API/U + 各自后端域 API
```

| 页面/区域（基线行号） | 责任和直接耦合 | 拆分建议 |
|---|---|---|
| IIFE 顶部通用对话框、记录、流订阅、Creator UI（1–722；共享片段散布至 723–3213） | DOM、`localStorage`、`API.state`、`U.toast`、Creator job/Event stream | 记录身份隔离、流订阅销毁、Creator 视图属于共享支持逻辑，先单独列依赖；不和任何单页一起搬。 |
| `renderChat`（723–3213） | 会话/工作区导航、模式选择、Composer、图片/文件/视频附件、SSE、工具进度、Agent Run/Todo、Creator、dispose listener | **最高耦合，最后拆**。先不细分交互；未来按已有 API 边界拆视图/流/附件，但每步保持请求次数和 SSE 协议。 |
| `renderTools`（3214–3375）、Persona/Memory/KB/Life/Ability（3376–4111） | 各自读写对应配置/数据，主要通过 API/U 和 `ctx` | 作为第二批候选；先确认页面共享 helper 的引用，再按单页拆。 |
| `renderData`（4112–4380） | API/U、图表组件 `Chart`/`chart.js`，以 DOM 为主要输出 | 低耦合候选之一；搬迁时显式维持 Chart 脚本在前、renderer 在页面装配前。 |
| Settings 与子页（4381–5373） | 模型配置/运行模式/系统管理及 `FUNC_GROUPS` 动态子页生成，页面间共享 settings state | 拆分价值较低且页面注册组合紧密，等单页模式稳定后再处理。 |
| Creator 视图/卡片辅助（5374–5737） | jobs API、状态格式化、卡片 DOM 和 chat 记录 | Creator 生命周期与聊天 SSE 卡片相连，优先提纯格式化/组件边界，暂留共用模块。 |
| `renderPluginMarket`（5738–5937） | 插件市场 API、UI helpers、`vigil-market-changed` 监听与 dispose | 低耦合候选；app 对 chat→market 有 parking 行为，迁移需验证 chat 保留和事件监听销毁。 |
| `renderVideoWorkbench`（5938–6076） | 附件/视频工作区 API、UI helpers、DOM 事件与 dispose | **最优先低耦合候选**；页面边界清楚，避免抽出后把 upload 逻辑重复放入 chat。 |
| `global.PAGES`（6077–6086） | 页面 id/顺序/renderer 总表 | 暂留 `pages.js` 或独立 `pages/index.js` 作为唯一静态页面装配表；插件动态注册仍走 app 生命周期。 |

导航与生命周期目前由 `app.js` 的 `go()` 统一管理：切页先 dispose，创建带 `onDispose` 的 ctx，await 页面 render，导航竞态由 navigation id 丢弃；chat→market 会暂存 chat DOM 和 dispose，返回时复用；插件 `boot` 期间注册、替换、卸载页面并更新导航。新的页面文件只能提供 renderer，不能自建第二套导航或 cleanup 管理。

## R1 建议目标结构

```text
brain/
  server.py                 # 兼容 re-export、Brain/插件装配、HTTP Handler、启动入口
  api/
    request.py              # 纯请求归一与错误形状辅助
    config.py               # 配置读写 HTTP adapter
    models.py               # 模型/运行模式 HTTP adapter
    workspaces.py           # 工作区 API adapter
    retrieval.py            # RAG 配置/检索 API adapter
    agent_runs.py           # Agent Run/Workflow HTTP adapter
    plugins.py              # 插件管理/提供路由 adapter（生命周期仍属插件系统）
  chat/
    images.py               # 图片解码/载荷/保存辅助
    history.py              # 历史归属、恢复、检索边界
    service.py              # ask/retry 与 chat orchestration
    tool_loop.py             # 工具循环和实时 Function Calling
client/src/js/
  pages.js                  # 共享 UI helpers + PAGES 装配（初期）
  pages/
    video-workbench.js      # 首批候选
    plugin-market.js        # 第二候选
    data.js                 # 第二候选（依赖 chart.js）
    ...                     # 其余领域页面按需迁移
```

这是目标而不是 R2 一次性创建的清单。首批可以只建 `brain/api/request.py` 与 `client/src/js/pages/video-workbench.js`，以导入边界明确、无反向 `server` 依赖为准。Python 包的 `__init__.py` 是否需要取决于当前 Python 版本/运行方式；优先使用显式包导入，避免动态导入。

## 按代码区域划分的迁移表

| 批次 | 源区域 | 新边界 | 验收重点 |
|---|---|---|---|
| R2 | 视频工作台 renderer；`_query_param` 等纯输入辅助 | `client/src/js/pages/video-workbench.js`、`brain/api/request.py` | renderer 唯一注册、切页/返回/销毁；请求解析单测；不碰聊天/HTTP 流。 |
| R3 | 插件市场 renderer；插件只读/配置 API 的薄业务适配 | `client/src/js/pages/plugin-market.js`、`brain/api/plugins.py` | 市场刷新事件监听 dispose；插件 install/enable/route 生命周期与 HTTP 冒烟。 |
| R4 | Data renderer、独立配置/模型/运行模式和工作区 API | `client/src/js/pages/data.js`、`brain/api/{config,models,workspaces}.py` | 配置持久化、原地共享配置更新、工作区 owner/权限、chart 加载。 |
| R5 | 检索配置 API、Agent Run/Workflow Handler adapter、附件 HTTP adapter | `brain/api/{retrieval,agent_runs,attachments}.py` | Function Calling、Todo/Workflow、鉴权/权限、附件分块及错误协议。 |
| R6 | `ask_with_retry`、`_chat_with_tools`、聊天页共享逻辑、图片/SSE | `brain/chat/*`、`client/src/js/pages/chat.js` | P19 分类与三种路由、历史 RAG 增量 SSE、实时工具调用、图片、请求次数、事件序列、页面 dispose。 |

每批执行“新模块接入 → 对应回归通过 → 删除旧实现”；不得先删旧路径再补新模块。R1 不开始 R2。

## 保护性回归覆盖

| 契约 | 现有保护 |
|---|---|
| P19 四类任务分类 | `tests/test_p19_task_intent.py` 覆盖 chat/retrieval/simple/complex 分类、entry selection、未授权回退。 |
| P19.1 本地/云端/混合模型路由 | `tests/test_p19_routing_contract.py` 与 `brain/tests/test_run_modes_http.py`；路由合同和真实 HTTP 模式测试。 |
| P19.2 Agent Run/Todo | `tests/test_p19_2_workflow_agent_run_integration.py`、`tests/test_p18_workflows.py`、`tests/test_p2_agent_http.py`；包含计划/Todo 状态、权限、隔离和恢复。 |
| 历史 RAG 增量 SSE / 实时检索 Function Calling | `tests/test_p2_agent_http.py::test_z_chat_and_history_retrieval_keep_incremental_sse`；P19 路由测试验证 `history_rag` 和 live tool 的 entry/工具需求。 |
| 用户鉴权/工作区权限 | `tests/test_p2_agent_http.py`（401/403、会话 owner）和 `tests/test_p161_image_workspace.py`、`tests/test_p18_workflows.py`（选定 workspace、scope 复核）。 |
| 图片/附件 | `tests/test_p16_stream_image.py`、`tests/test_p161_image_workspace.py`、`brain/tests/test_attachments.py`。FFmpeg 用例依赖本机 ffmpeg，按 skip 条件执行。 |
| 配置写入和持久化 | 多数领域配置有专门合同测试；插件渠道配置写盘另有 `brain/tests/test_plugin_transactions.py`/manager tests。迁移时增加针对 `_refresh_api_cfg` 并发可见性的测试前应先检查 runtime 专项覆盖。 |
| 页面初始化/切换/销毁 | `client/tools/test-p2-stream-lifecycle.js` 直接保护生产聊天流关闭/终态；`app.js` 的 chat parking 和插件页面生命周期暂无发现同等全面的自动化生命周期测试。迁移页面前应补 harness 覆盖首次渲染、两页切换、onDispose 一次执行、chat→market→chat parking、插件卸载当前页。 |
| 插件生命周期和 HTTP 路由 | `tests/test_pc_core_context.py` 有隔离的真 Handler/API 测试，覆盖插件路由 403/200、enable/disable、市场/管理视图一致性、通道配置持久化与卸载；`brain/tests/test_plugin_manager.py`、`test_plugin_transactions.py`、`test_plugin_structure.py` 和客户端市场合同补充插件系统覆盖。`brain/_test_plugin_admin_http.py` 文件名不会被 pytest 默认收集且需要运行中服务，不算 CI 回归，但已有 `test_pc_core_context.py` 的自动化替代覆盖。 |

现有测试具备 P19 拆分所需的主合同，R1 未发现需要为了本次“仅建基线”新造生产行为测试的确定缺口；页面导航和插件路由自动化缺口已列为对应迁移批次的前置验收项。特别是 `brain/tests/test_attachments.py` 会自动收集，`brain/_test_plugin_admin_http.py` 不会。

## 打包、导入和循环依赖风险

- Windows Brain 构建实际入口为 `scripts/build_release.py` 调用 `deploy/brain/vigil-brain.spec`，入口脚本是 `brain/server_launch.py`，`pathex` 指向 `brain/`。静态 import 的模块会被 PyInstaller 分析；运行时字符串导入、`spec_from_file_location` 插件依赖需维护 `hiddenimports`。新增 `brain/api`/`brain/chat` 常规显式导入预计自动纳入；若按字符串反射导入，则必须更新 spec 和对应 build 验收。
- `deploy/brain/vigil-brain.spec` 仅对跟踪的插件源码与 `skill_host` 资源做显式收集。不要把新 Python 模块误放到插件资源路径；插件目录型资源有单独的复制规则。仓库另有旧 `brain/vigil-brain.spec`，它与正式 release spec 容易漂移，改打包设置时需要确认正式入口并在报告/变更中指出。
- Electron Builder 的 `files` 包含 `src/**`，新页面 JS 文件会进入 `app.asar`；但 `client/src/index.html` 必须显式加入 `<script>` 且顺序正确。`ui.js`/`api.js`/agent state 在页面之前，`app.js` 最后；新增页面脚本必须位于其依赖之后、`app.js` 之前。开发 harness、浏览器预览、Electron 都需使用同一生产入口。
- 测试导入路径存在历史兼容：若干图片测试从 `server.py` AST/隔离命名空间抽取函数并伪造全局依赖；迁移会让这些测试脱离真实模块边界。搬迁时需将它们改为直接导入新模块，并保留 HTTP 端到端用例，禁止为了测试而保留第二份函数实现。
- 主要循环依赖风险：`server -> api module -> server`（最危险，禁止）；`server -> chat -> runtime -> server`（runtime 共享配置初始化顺序）；API 模块反向引用嵌套 Handler；页面模块互相引用聊天私有 helper；插件路由模块 import 时加载插件。新依赖应单向：`server/Handler -> api adapters -> domain modules/runtime`；`app -> PAGES -> page renderer -> U/API/browser bridge`。
- 运行 `build_release.py --candidate --brain-only` 会改写 build identity、重建 `dist/` 并清理构建目录，不属于 R1 只读打包审计的必要步骤；R1 核对了 spec、收集规则和加载顺序，未生成安装包。

## R1 实际执行

本 PR 仅新增本审计报告，无业务代码搬迁、API/JSON/SSE/数据库或权限策略变更。指定基线关系已通过 Git 历史确认。Python 回归选择了 P19 分类/路由/Agent 集成、P18 workflow/Todo、P2 Agent HTTP/SSE、图片/工作区/持久化、附件、运行模式 HTTP 和插件核心上下文；结果为 **175 passed、3 skipped、9 subtests passed、0 failed**（3 项按条件跳过：本机缺少 FFmpeg 的附件视频用例）。JS `node --check` 对 `pages.js`、`app.js` 通过；`client/tools/test-p2-stream-lifecycle.js` 为 **17 passed、0 failed**。插件实际 API 覆盖包含在 `tests/test_pc_core_context.py` 中。R1 不自动启动 R2。
