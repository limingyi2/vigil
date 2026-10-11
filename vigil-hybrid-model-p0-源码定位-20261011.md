# P0 源码定位报告：Vigil 混合模型模式 v1

日期：2026-10-11 · 阶段：P0（只审查不开发）· 依据：《vigil-hybrid-model-v1-plan.md》

结论先行：**方案的技术前提全部属实**，当前代码没有 Provider 抽象，但模型出口集中、
调用链干净，按方案"先 local 再 hybrid"做最小改造可行，不需要动 server.py / brain.py
的整体结构。本报告给出实际调用链、事实差异、修改文件清单、单测方案和回滚点。

---

## 1. 实际模型调用链（代码事实，非推测）

### 1.1 聊天主链路（P1 必须覆盖的）

```text
HTTP POST /api/chat                         brain/server.py:1518
  └─ ask_with_retry(text, src=...)          brain/server.py:393 起
       └─ [可选] planner.plan/execute_plan   brain/planner.py（见 1.4 的 src 缺陷）
       └─ Brain.chat(text, src=...)          brain/brain.py:1220
            └─ Brain.draft_native(...)       brain/brain.py:821
                 └─ httpx.post(api_base + "/chat/completions")   ← 无 Provider 抽象
                    · brain.py:940   工具循环主请求（带 tools/tool_choice）
                    · brain.py:1042  轮次/预算耗尽后的"去工具表收尾"请求
                 工具执行：
                    tools.all_specs(brain, src)              brain/tools.py:298
                      ├ _static_tool_array(src)              内置+插件规格
                      ├ plugin_host.tool_specs(src)           插件按渠道过滤
                      └ skill.tools(src)                      动态技能
                    tools.invoke_spec(name, args, ctx)        brain/tools.py:397
                      └ plugin_host.Turn(src=...) 全程携带渠道身份
```

- Brain 全程**同步、单聊天锁**（`_chat_lock`，300s），工具循环在请求线程内完成。
- 流式出口 `Brain.stream_chat()`（brain.py:1105）独立直连 httpx.stream，**当前不支持工具**。

### 1.2 全部 chat/completions 出口清单（9 处）

| 文件 | 行 | 用途 | P1 是否走本地模型 |
|---|---|---|---|
| brain/brain.py | 940 | FC 工具循环主请求 | **必须** |
| brain/brain.py | 1042 | 超轮次/超预算后的收尾请求 | **必须** |
| brain/brain.py | 1164 | stream_chat（SSE） | 首版可降级非流式 |
| brain/brain.py | 1584 | speak_on_scene 主动搭话 | 可后续 |
| brain/planner.py | 290 | `_ask_flash`：规划/校验/修订三处共用 | 建议随主循环 |
| brain/memory_store_v2.py | 1543 | 后台记忆抽取 extract | 优雅降级（见 4.3） |
| brain/memory_store_v2.py | 1629 | 她的承诺抽取 extract_her_promise | 优雅降级 |
| brain/recap_store.py | 423 | 后台历史摘要压缩 | 优雅降级（后台、可停） |
| brain/vision.py | 150 | 看图描述 | local 下标记不支持 |
| brain/plugins/gate.py | 649 | 群聊 @ 闸门判定 | 本来就 try/except 静默 |

> 生图（imggen）、语音（voice）走的是各家非 chat 接口，本方案不涉及。

### 1.3 配置与装配链

```text
config.json（brain/config/ 或 sidecar 家目录）
  └─ runtime.load_config() → API_CFG（白名单子集，键清单 runtime.API_CFG_KEYS）
       └─ Brain(api_config=API_CFG)；self.api 即这份共享字典
  /api/models/apply → model_hub.apply_cfg() → model_hub.save()
                    → server 内 API_CFG.clear()+api_cfg_subset(update)（热生效，免重启）
```

- 服务商注册表 `model_hub.PROVIDERS`：siliconflow / xiaomi / **custom（OpenAI 兼容）**。
  也就是说今天把 provider 选"自定义"、base 填 `http://127.0.0.1:11434/v1` 就能把
  请求打到 Ollama —— 但缺 options 注入/think 开关/健康检查/占位 key 等关键件（见第 3 节），
  这正是 OllamaProvider 要补的，不建议靠"用户手填 custom"硬凑。

### 1.4 方案点名的"Planner 丢失 src"——属实

- `planner.plan(text)`（planner.py:341）**不接收 src**；
- `_available_tools_text()` 里硬编码 `tools.all_specs(src="app")`（planner.py:193），
  规划提示词里的可用工具清单永远按 app 渠道算；
- 而执行侧 `execute_plan(steps, src=..., allowed_tools=...)`（planner.py:383）
  **是对的**：按真实 src 重算 active 集合并做交集 fail-closed。
- 后果：微信/群聊渠道触发规划时，提示词列出的工具 ≠ 最终授权集合，模型可能规划一个
  该渠道被关掉的工具，执行时被"工具未授权"打回。修法很小：src 从 server 调用点
  （server.py:455 `planner.plan(text)`）一路透传到 `_plan_prompt/_available_tools_text`。

### 1.5 网络拓扑（决定 hybrid 形态的关键事实）

```text
云主机：server.py（云端 brain）  ──×──  127.0.0.1:11434（那是云主机自己，不是用户 PC）
PC：Electron 养着 sidecar（vigil-brain.exe，独立家目录 config/data）
       └─ 能直连本机 Ollama:11434
PC：Electron 同时是 localtools 远程工具客户端（1.5s 轮询云端/本地大脑）
```

- **OllamaProvider 直连只在 sidecar（PC 本地 brain）成立。**
- 已存在的 localtools 通道（服务端排队 → PC 客户端轮询认领 → 本机执行 → 回传）
  是一条现成的"云端借 PC 执行"隧道。仓内《混合模式方案v2》主张未来 `local_model`
  走这条隧道；v1 方案的 `delegate_to_local` 直连模型的形态首版应锁定 sidecar 场景。

### 1.6 本机环境事实（2026-10-11 实测）

- Ollama 在线：`GET http://127.0.0.1:11434/api/tags` 正常；
- 已装模型：`qwen27b-efficientthink:latest`（约 13.3 GB）——**P1 真实模型验收可在本机直接做**。

---

## 2. 隔离实战证据复核（vigil-eval）

`vigil-eval/adapters/ollama_worker.py`（520 行）+ `run_ollama_smoke.py` 已审查。

### 2.1 可直接继承的资产

1. **端点与 payload 事实**（G0/A0x 实测）：
   - Ollama OpenAI 兼容层 `http://127.0.0.1:11434/v1/chat/completions` 能驱动
     brain.py 原生 FC 多轮工具循环（25 次模型请求 / 27 次工具执行的实战记录）；
   - shim 会转发 `options`：必须注入 `options.num_ctx=16384`、
     `options.num_predict=2048`，顶层 `think: true`（efficientthink 模型）。
     brain.py 现在**不发**这些字段，默认 4096 ctx 会截断 ReAct 循环；
   - Ollama 忽略 Authorization，但 brain.py 有 `if not self.api.get("api_key"): return None`
     的门（brain.py:834/1117），所以占位 key（"ollama"）必须给。
2. **沙箱与断路器蓝本**：`safe_path()`（resolve 后必须在 workspace 根内，
   挡绝对路径/`..`/junction/symlink）、`allowed_tools` 白名单、
   6 请求 / 8 工具 / 180s 墙钟熔断 —— 与方案第 5 节的合同限额一一对应，
   可直接搬到 delegation/dispatcher.py。
3. **进度与计数**：prompt_eval/eval token、耗时、熔断原因落 progress 文件的做法，
   对应方案"记录 prompt_eval_count/eval_count、耗时、模型/配置和超时"。
4. **selftest 模式**（不调模型验 safe_path/allowed_tools/dispatch）可改造成
   OllamaProvider 的离线健康自检单测。

### 2.2 不能搬进生产路径的东西（差异清单）

| eval worker 的做法 | 为什么不能进生产 |
|---|---|
| 进程启动时 `httpx.post = counted_post` 全局 monkeypatch | 同进程还有云路径、后台线程；生产要做成 provider 内的正常调用层 |
| 整体替换 `tools.all_specs/run_tool/invoke_spec` | eval 要的是隔离工具集；生产必须走真实 Tool Gateway（渠道/权限/确认） |
| `IsolatedBrain._mem_for → None`、独立 VIGIL_HOME | eval 刻意无记忆无历史；生产 Brain 要完整人设/记忆/历史 |
| stdin 收 JSON spec / stdout 打 `VIGIL_EVAL_RESULT=` | 评测 harness 协议；生产 Dispatcher 是进程内组件 |
| 自带 file_list/read_file/replace_text/pytest 实现 | 首版委派白名单（read_only_bug_analysis 等）只读，且应复用 Gateway，不另造 |

---

## 3. Provider 接缝设计（P1 落地形状，最小改动）

现状 4 处 brain.py 出口 + planner 一处，每处都在各自拼 `{model, messages, temperature,
max_tokens, reasoning_effort}` 并直调 `httpx.post`。推荐的最小收口：

```python
# brain/model_providers/base.py
class ModelProvider:
    def chat(self, payload, *, timeout=90) -> ChatResult: ...      # 非流式
    def stream(self, payload, *, timeout=120): ...                 # P2/P3 再用
    def capabilities(self) -> dict: ...                            # tools/stream/vision/think

# brain/model_providers/cloud.py   包装今天的 httpx 直调，payload 语义一字不改
# brain/model_providers/ollama.py  Ollama /v1 + /api/tags：
#   · list_models() 从 /api/tags 读，用户明确选择，默认不硬编码 tag
#   · chat() 注入 options(num_ctx 16384 / num_predict 按任务 1024|2048)、
#     顶层 think 请求级开关；归一化 usage 与 reasoning；
#   · health()：未安装/未启动/模型未加载/OOM/超时/坏格式 分类返回
#   · 不强制 format=json（只在委派结构化子任务时按合同开）
```

- 在 brain.py 加**一个**模块级收口（如 `_provider()` 按 API_CFG 的 `model_mode` 取），
  4 处 httpx.post 改为 `_provider().chat(payload)`；cloud provider 内部就是今天的代码，
  保证云端行为零变化。
- 生产 Ollama 请求**不靠 monkeypatch**：options/think 在 OllamaProvider.chat 里组装。

---

## 4. P1 实际修改文件清单

### 4.1 新增（独立模块，删目录即可回滚）

| 文件 | 内容 |
|---|---|
| `brain/model_providers/__init__.py` | 工厂 `get_provider(cfg)`、capability 查询 |
| `brain/model_providers/base.py` | Provider 基类、ChatResult/ProviderError、错误类别枚举 |
| `brain/model_providers/cloud.py` | 现有云端直调的薄包装（默认 provider） |
| `brain/model_providers/ollama.py` | OllamaProvider：tags、health、options/think 注入、usage 归一、错误分类 |
| `tests/test_model_providers.py` | httpx 打桩单测（沿用 test_native_fc 的 stub 手法，不联网） |

### 4.2 修改（小而集中）

| 文件 | 改动 | 风险 |
|---|---|---|
| brain/brain.py | 4 处 httpx 出口走 provider 收口；语义保持 | 中（核心，但有 test_native_fc 兜） |
| brain/planner.py | `plan(text, src=...)` → `_plan_prompt/_available_tools_text` 透传 src（修 1.4） | 低 |
| brain/runtime.py | `API_CFG_KEYS` 增加 `model_mode`、`ollama` 两个键 | 低 |
| brain/model_hub.py | `apply_cfg` 允许写入 model_mode/ollama 段（或 P1 先用独立小接口，二选一，推荐前者） | 低 |
| brain/server.py | 只读接口：Ollama 在线状态 + /api/tags 模型清单（给 UI 连接测试/下拉） | 低 |
| client/electron + client/src/js | P1 最小 UI：运行方式三选、Ollama 地址/模型/连接测试、状态灯（方案第 7 节 1/2/5 项） | 低（纯增量） |

### 4.3 明确不在 P1 改的

- server.py HTTP/鉴权/同步主体、plugin_host、tools.py、localtools、
  persona/memory/recap 的数据格式与同步协议；
- 辅助模型出口（记忆抽取/摘要/看图/gate）：local 模式下策略为
  **记忆抽取自动跳过**（config 里 `memory_search.enabled=false` 已支持；抽取调用本就
  包在后台 try/except）、摘要后台循环空转、看图返回"本地模型不支持看图"、
  gate 静默放行规则不变。主循环（A01/A02/A04）不依赖它们；
- 不新增数据库、不搬文件、不起常驻 GPU 服务、不做 P2 的 delegate_to_local。

### 4.4 顺手记录的小存量问题（不在 P1 必做，建议夹带）

- `server.build_local_brain()` 第 98 行 `get_default_store(key, "app")`
  仍用已退役的 "app" 作兜底分区名，应与"wechat 为基础渠道"的现行政策对齐
  （`_mem_for` 正常路径不受影响，只有异常兜底和初始 seed 走这条）。

---

## 5. P1 单测与验收方案

### 5.1 自动化单测（不依赖本机有 Ollama，进 CI 思路）

参照 `tests/test_native_fc.py`（把 `brain.httpx` 换成顺序吐响应的 stub，检查真实 payload）：

1. **payload 注入**：Ollama 模式下请求体含 `options.num_ctx=16384`、
   `options.num_predict`、顶层 `think`；cloud 模式请求体与今天逐字段一致（回归保护）。
2. **多轮 FC**：tool_calls → role:tool 配对回灌 → 最终正文，在 OllamaProvider 下走通
   （复用 test_native_fc 的消息断言）。
3. **usage/错误归一**：prompt/completion token 入账；连接拒绝→`unavailable`、
   HTTP 500→可重试、坏 JSON→格式错误；占位 key 自动补。
4. **tags/健康检查**：/api/tags 解析、空列表、超时。
5. **planner src 透传回归**：wechat_group 下规划提示词的工具清单与
   `all_specs(src="wechat_group")` 一致，不再硬编码 app。
6. **模式缺省**：config 没有 `model_mode` 时行为 = cloud（逐请求等价）。

### 5.2 真实模型验收（本机，手动/半自动）

- 用真实 Ollama 对 sidecar brain（**生产路径，不用 eval worker 的 monkeypatch**）
  跑 A01 文件发现、A02 只读 Bug 分析、A04 工具失败恢复；
- `vigil-eval/run_ollama_smoke.py` 保留为**隔离对照基线**，不作为生产验收；
- 负测：关掉 Ollama 后 local 明确报不可用；hybrid（P2）回主模型、不偷偷外发数据；
- 断网验证：local 全程不触云端 API（可用断网/防火墙规则实测）。

---

## 6. 回滚方案

1. 新增代码全在独立新目录 `brain/model_providers/`，删除即彻底回滚；
2. `model_mode` 缺省 = `cloud`：不写该键时，brain.py 收口取到的就是 CloudProvider，
   请求体/时序/工具循环与今天完全一致；
3. brain.py 的 4 处改动是"直调 → provider.chat()"的等价替换，git revert 单文件即可；
4. 不涉及存档、记忆、人设、同步协议格式变化，无数据迁移；
5. planner src 透传是纯增量参数，回滚不影响其它调用方。

---

## 7. 需要你拍板的点（我按推荐先记下，可改）

| # | 待决项 | 我的推荐 |
|---|---|---|
| 1 | local/hybrid 在哪运行 | **P1 只在 PC sidecar 支持**；云端 brain 上该选项显示"本地大脑不在线"。云端借 PC 的形态走 v2 的 localtools 隧道，留到 P2 之后 |
| 2 | 模式配置键 | 新增 `model_mode: cloud\|local\|hybrid`，**不复用** `chat_mode`（遗留值仍有代码读）；Ollama 设置放 `ollama: {base, model, num_ctx, num_predict, think}` |
| 3 | local 下的流式 | P1 先非流式（UI 已有降级）；Ollama SSE 接通后再开 |
| 4 | 辅助模型调用（记忆抽取/摘要/看图） | P1 优雅降级（跳过/标记不支持），不调云端 —— 保证"local 不依赖云 API"的承诺 |
| 5 | 默认模型 tag | 不硬编码 `qwen27b-efficientthink:latest` 给所有用户；开发默认值仅写进测试/文档，UI 从 /api/tags 拉取并要求用户选 |
| 6 | hybrid 委派通道 | P2 在 sidecar 场景做进程内 Dispatcher；`delegate_to_local` 是主模型工具表里的受控工具，第一版只开方案第 5 节五个只读 kind |
| 7 | planner src 修复 | 随 P1 一起做（方案第 5 节明确要求，改动极小） |

---

## 8. P1 开工前的 checklist 对照（方案第 11 节完成定义映射）

- [ ] cloud 模式原样可用 → 5.1-1/6 单测 + 现有测试全绿保证
- [ ] local 不需要云端 API → 5.2 断网实测；辅助调用按 #4 降级
- [ ] 真实 A01/A02/A04 通过（生产路径，非 stub）→ 5.2
- [ ] Ollama 不在线负测 → 5.1-3 + 5.2
- [ ] 可展示任务轨迹与成本 → provider 归一化的 usage + 现有 trace.jsonl 字段扩展
- [ ] 无新增未授权高风险执行路径 → P1 不碰 Tool Gateway；P2 只读 kind + 沙箱


---

# 附：P1 实施与验收记录（2026-10-11 完成）

P0 审查后直接完成了 P1。新增与修改：

- 新增 `brain/model_providers/`（base/cloud/ollama/工厂）；
  `model_mode`：cloud（默认）/ local / hybrid（P2 前主模型仍走云端）。
- `brain/brain.py` 4 个模型出口（FC 主循环/收尾/stream/主动搭话）收口到
  provider，cloud 请求逐字节等价；入口门改为 provider.ready()（本地不要 key）。
- 配置：`runtime.API_CFG_KEYS` 增 `model_mode`、`ollama`；
  `model_hub.apply_cfg` 支持写入（字段白名单收口）；
  `GET /api/ollama/status` 健康检查/连接测试（管理员）。
- 修复 `planner.plan(text, src=...)` 渠道透传（规划提示词不再硬编码 app）。
- 客户端模型设置页（本机视图）新增：运行方式三选（混合置灰）、Ollama
  地址/模型/num_ctx/num_predict/think、测试连接（拉 /api/tags 回填模型）。
- 单测：`tests/test_model_providers.py`（28 项）、`tests/test_planner_src.py`；
  `pytest.ini` 固定收集范围，排除评测工件目录。

**两个实测推翻旧结论的关键事实（已写进代码注释）：**

1. 当前 Ollama 的 **/v1 OpenAI shim 忽略 options.num_ctx**（vigil-eval G0
   的旧结论已过时），大上下文一律 400（n_ctx=4096）。生产 provider 改走
   **原生 /api/chat**（认 num_ctx/num_predict/think），并做双向适配：
   响应侧 arguments(dict) 转 JSON 字符串、thinking 转 reasoning_content、
   prompt_eval_count/eval_count 转 OpenAI usage；请求侧多轮回灌时把
   arguments 字符串转回对象（否则 400），删 reasoning_effort、
   max_tokens 映射成 num_predict。实测原生端点 OpenAI 风格 tool_calls +
   role:tool 回灌直接兼容，Brain 的消息构造零改动。
2. `brain.py` 的 `_TOOL_PARAM_RE` 曾整组丢失（模型吐 XML 工具调用时
   NameError 被吞成"API失败"），已补回。

**真实模型验收（生产路径，无 monkeypatch；qwen27b-efficientthink:latest）：**

| 用例 | 结果 | 用时 | 实际工具 |
|---|---|---|---|
| A01 文件发现 | PASS | 12s | local_file_list（正确报出 app/auth.py、error.log） |
| A02 只读 Bug 分析 | PASS | 64s | local_file_list + local_file_read（定位 auth.py:4：find_user 恒 None） |
| A04 工具失败恢复 | PASS | 65s | tracking 真调用、失败后不编造 |
| 负测：Ollama 不在线 | PASS | - | health=False；chat 抛 ProviderUnavailable |
| 负测：模型名错误 | PASS | - | 404 归类 model_not_found |

全量测试：**752 passed / 26 failed / 8 skipped**，26 个失败与 P1 前基线完全
相同（test_tools 旧 API、ws_forward BOM、缺 rebuild_apk.py 等存量问题），零回归。

**P1 明确未做（留给 P2/P3）：** delegate_to_local 与 Dispatcher/合同/Validator；
本地流式（/api/chat 是 NDJSON，P3 再做 SSE 适配，当前 local 下 stream_chat
回 error 事件、客户端降级非流式）；记忆向量检索/看图在 local 下仍指向各自配置
（要完全离线需关 memory_search 或接 Ollama embed，P3 评估）。
按方案约定，**P2 开始前等确认**。


---

# 附2：P2 实施与验收记录（2026-10-11 完成）

最小混合委派闭环，按方案第 4.2/5/6 节落地，未触碰云端语义与 Tool Gateway 权限。

**新增 `brain/delegation/`：**
- `contracts.py`：5 个只读 kind 白名单（log_root_cause / file_discovery /
  read_only_bug_analysis / structured_extraction / plan_draft）、硬顶
  6 请求/8 工具/180s、workspace 必须落在 config 的
  `delegation.allowed_roots`（空名单 fail closed）、input_refs 越界过滤、
  schema 名白名单、不可变 src 固化进合同。
- `sandbox.py`：进程内只读沙箱（file_list/file_find/read_file/file_grep），
  safe_path realpath 防穿越、junction/symlink 不跟随、200KB/200 条等读取上限；
  与 Electron localtool 通道刻意分离（那是"她操作电脑"，这是"有界子任务"）。
- `validator.py`：bug_analysis/file_list/extraction/plan_draft 四类 JSON
  结构断言；证据文件必须真实存在（含简写同名容错）；无工具调用的分析不算完成；
  plan_draft 永久带 needs_main_agent_review。
- `dispatcher.py`：有界 Ollama 子循环，三重熔断、失败分类
  （unavailable/oom/model_not_found/tool_limit/request_limit/timeout/
  validation_failed），验收不过带修正意见**最多重试一次**，结果原子落盘
  `data/delegations/<task_id>.json`，支持 cancel_event；本地失败绝不自动提权。
- `bridge.py`：`delegate_to_local` 工具。门控 = model_mode=hybrid 且
  delegation.enabled=true 且已配 Ollama；工具表由 `tools.all_specs` 条件挂载，
  `tools.invoke_spec` 直达桥；合同拒绝/本地故障都回字符串给主模型，永不抛异常。

**配置**：`delegation.enabled`（默认 false，需用户显式打开）+
`delegation.allowed_roots`；config.example.json 已加示例。P2 不做委派 UI
（按方案 UI 在 P3），目前改 config.json 开启。

**单测** `tests/test_delegation.py`：37 项（合同越界/限额/schema、沙箱
绝对路径与 .. 拒绝、证据真伪、熔断、取消、重试一次、离线失败、门控、
cloud 工具表回归）。

**真实 Ollama 端到端验收（qwen27b-efficientthink，无桩）：**
| 用例 | 结果 |
|---|---|
| Dispatcher 只读 Bug 委派：自主 read/grep 取证 → bug_analysis_v1 JSON | PASS（10s，2 请求 2 工具，正确定位 orders.py:2，证据真实，已归档） |
| 主模型 FC 经 delegate_to_local 拿到结构化结果 | PASS |
| Ollama 不在线：桥返回失败说明、建议主模型自己处理，主循环不崩 | PASS |
| cloud 模式工具表不含委派工具 | PASS |

全量 **790 passed / 26 failed / 9 skipped**，失败集与各阶段基线完全一致，零回归。

**P2 明确未做（P3）：** 第二阶段 sandbox_patch_and_test（写+pytest 白名单）；
委派 UI 开关/状态展示；A/B 对照评测（6 任务、含 2 个不应委派样本）与
token/耗时报告；NDJSON 流式；云端大脑借 localtools 隧道使用本机 Ollama
（当前 hybrid 设计运行点是 PC sidecar）。
