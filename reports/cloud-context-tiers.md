# 云端上下文三档与历史检索状态验收

2026-10-19，基线 `origin/dev`：`b1a8f1f2d653a80f2d660333a40544cc18106962`。独立 worktree `F:\vigil-context-tiers`，原工作区及 P8.5 安装包均保留。桌面版本 **1.1.6 / code 27**；构建本地候选安装包，不创建 GitHub Release、不修改 master。

## 修改范围与行为

| 文件 | 改动 |
|---|---|
| `brain/conversation_context.py` | 在现有统一预算中加入十进制三档映射：96000/72000/48000，128000/96000/64000，300000/225000/150000。保存的偏好与实际生效窗口分开，按真实配置容量、输出预留、安全余量和独立输入上限取安全交集。容量不够时降为支持的较小档；不足 96K 时显示实际受限容量，未知时最多使用保守 8192 容量。沿用本地预算。 |
| `brain/model_hub.py` | 模型能力按 provider、endpoint、model 绑定；切模型/地址不携带上一模型的能力，切回精确相同身份可恢复。配置继续沿用现有模型中枢保存入口，改用已存在的原子 JSON 写入，保留备份及其他字段。 |
| `brain/runtime.py` | 现有配置白名单补传 `context_compression`、`history_retrieval`。修复“配置文件已保存但 Brain 实际请求仍用默认值”的调用链缺口；没有修改委派设置或模型加载/卸载流程。 |
| `brain/server.py` | 新增受原权限边界保护的 `POST /api/models/context`，只更新档位偏好。已有模型清单提供生效窗口与检索状态；已有 stats 诊断按实际运行时配置显示状态。 |
| `brain/history_retrieval.py` | 只增加无需联网的状态投影和后端身份诊断。BM25、向量、RRF、隔离、原文校验、数量与 Token 限制均不变。未配置或失败显示“BM25 关键词检索”；已配置但尚未成功验证显示“BM25 关键词检索（向量待验证）”；已验证可用的后端显示“BM25 + 语义向量检索”。 |
| `client/src/js/pages.js`、`client/src/chat.css` | 聊天框模型菜单内的二级单选菜单只列 96K/128K/300K，有选中勾与键盘导航；显示实际生效窗口和受限/未知原因。菜单及原上下文诊断位置显示历史检索模式。不增加 Embedding 配置页，不暴露 work/trigger/target 控件。混合模式主模型的详情菜单可访问云端档位。 |
| `brain/config/config.example.json` | 云端示例简化为 `cloud_context_tier: 128000`，本地配置不变；不改用户实际 config.json。 |
| `tests/test_cloud_context_tiers.py` | 31 项新验收，包括真实 HTTP handler、原子保存失败、实际运行时热更新及检索注入共用预算。 |
| `tests/test_p85_context_history.py` | 保留 39 项验收；其中云端 200000 容量场景的默认工作上限预期由旧二进制 131072 更新为本轮十进制 128000，符合新三档定义。 |
| `client/tools/test-model-settings-electron.js` | 在原真实 Electron/预加载回归中增加三档保存、容量不足/未知、跨进程恢复、BM25 文案、模式切换保留消息及草稿、键盘操作。 |
| `client/package.json`、`client/package-lock.json`、`client/version.json` | 1.1.6 / code 27。 |

## 容量保护

用户偏好保存在 `context_compression.cloud_context_tier`，不会覆盖 Ollama、Key、模型名称或其他配置。显式选择后自动映射云端策略，旧云端 work/trigger/target 数据不再控制已选择的档位，也不会被用来修改本地值。未显式选择的旧配置仍尊重更低的既有云端工作上限。

能力依据为项目现有的 `model_capabilities`，不按模型名字猜测容量，不自动发云端探测请求。缺少已确认容量时，菜单明确显示保守窗口，不能因选择 300K 而宣称实际生效 300K。选择 300K、模型容量 200000 时实际为 128K；容量 100000 时实际为 96K；128000 容量档还需扣除输出预留与安全余量，不能把策略值当成可全部填满的输入预算。

本地验收保持 `num_ctx=12288`、输出预留 2048、工作预算 9000、触发 7500、目标 6000。云端/混合各档与本地互不覆盖。Embedding 状态查询只读取配置和已有成功/失败诊断，不联网、不下载、不初始化向量数据库，不添加云端依赖。

## 真实测试结果

- 全量 `python -B -m pytest tests brain -q --tb=short -ra`：**1359 收集，1342 通过，17 跳过，0 失败、0 错误**。已有跳过项为 Windows 链接/FIFO 权限和未随仓库分发的旧资源等，不计为通过。
- P8.4 **18/18**；P8.5 **39/39**；新三档与状态测试 **31/31**。
- 预算/模型切换/HTTP/运行时相关定向回归：**117/117**；更早包含完整运行模式与模型 provider 的定向回归 **128/128**。
- CI 对应 Node 回归：**24/24**。
- 打包运行时 Python 3.12.14：新测试 + P8.5 + 版本一致性共 **76 收集，75 通过、1 条旧母本缺失跳过**。
- 真实 Electron renderer/preload 测试：**通过**。三档选择保存，只有一个选中标记；能力不足降档，未知显示保守；全客户端子进程重启后恢复 300K 偏好；云端→本地→混合→云端过程中原消息节点、消息内容和输入草稿仍在；模型名称、Key 掩码和管理窗口原验收继续通过。暗色菜单截图保存在本 worktree 的 `.local-dev/context-evidence/model-picker-dark.png`。
- Python 编译、JS 语法检查、`git diff --check`：通过；版本更新后另跑版本一致性检查。

测试发现并修复了模型切换完成后详情按钮仍处于禁用状态的问题；另有 Electron 测试夹具因向 IPC 返回 DOM 节点而无法序列化，已改为返回布尔值。失败未计为通过。新预算与状态测试使用合成配置、FakeProvider/Embedding 和临时 VIGIL_HOME，没有读取真实聊天、调用付费模型或下载 Embedding 模型。

## 交付与边界

安装包路径、源提交、SHA-256、大小、manifest 与打包后实际 API 验证结果记录在 `reports/cloud-context-delivery.md`。本轮未声称实测云端模型支持 300K；在能力未配置的现有模型上，保守状态是预期保护。没有重写压缩器/检索算法，没有修改委派 dispatcher/router/contracts、Ollama 加载卸载或 recap。
