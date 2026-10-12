# Creator Mode MVP-1 交接文档（给 Codex Cloud / 后续开发者）

- 仓库：`limingyi2/vigil`
- 分支：`creator-mode-mvp-1`
- 基线：`45f847353ddc38f429457f02eedc5f06ab7809a3`（P4 收官点）
- 提交：`a176ab1eac841a42b58cd265eb8ba23f2a5dd8ee`（实现）、`b7c099d7fccdc54135d04f2587152404ea741d43`（报告）
- 状态：MVP-1 已完成实现 + 报告 + Electron 界面演示验收。**未合并 master、未做安装功能。**

---

## 1. 这个功能是什么（一句话）

在**主页聊天的输入框里**开一个「创作」模式，用自然语言跟 Agent 说话，
Agent 用真实模型生成一份**插件 / 技能草稿**，落进独立工作区并跑静态验证，
以一张**草稿卡气泡**的形式回到聊天流里。全程不安装、不装载、不执行生成的代码。

> ⚠️ 产品定位提醒：初版曾把入口做进「能力与扩展」组当独立页面（FUNC_GROUPS 第 035 项），
> 已被用户明确否掉。**入口必须在主页对话的 composer 里**，不要再往 FUNC_GROUPS 加页面。

---

## 2. 主要代码

| 文件 | 行数变化 | 职责 |
|---|---|---|
| `brain/creator.py` | 新增 548 | 核心模块：提示词契约、模型调用、文件名安全闸、落盘、版本历史、静态验证 |
| `brain/server.py` | +90 | `_creator_admin()` + 五条路由接线（do_POST 与 do_GET 各一处调用点） |
| `client/src/js/pages.js` | +262 | composer 创作开关、草稿卡渲染与绑定、右栏「我的草稿」、消息流草稿卡保活 |
| `client/src/chat.css` | +6 | `.creatorbar`、`#creatorBtn` |
| `client/src/styles.css` | +12 | `.cr-code`（复用聊天页 `.codeblock` 结构） |
| `brain/tests/test_creator_mode.py` | 新增 525 | 20 项测试（单元 8 + HTTP 12） |
| `docs/creator-mode-mvp-1/harness-creator-demo.js` | 新增 606 | Electron 界面演示 harness |
| `docs/creator-mode-mvp-1/ev-45f8473/results.json` | 新增 123 | 演示结果 20/20 |

### 关键函数（`brain/creator.py`）

```
drafts_root()                    <DATA_DIR>/creator_drafts/，不存在就建
_draft_dir(id)                   id 必须匹配 cr_[0-9a-zA-Z_]+，realpath 防逃逸
_safe_relpath(name, kind)        落盘前硬闸：拒绝 .. / 绝对路径 / 反斜杠 / 控制字符 /
                                 Windows 保留名 / 契约外扩展名 / 超 3 层
_extract_json(text)              从模型回复抠 JSON，容围栏与废话，括号在字符串内不误判
_build_messages(kind, req, prev, feedback)   组 (system, user)
_call_model(api_cfg, system, user)           返回 (text, err)，四类 ProviderError 分别明说
_persist(...)                    解析 → 快照旧版 → 写文件 → 验证 → 存 meta
generate_draft / revise_draft / validate_draft / list_drafts / get_draft / delete_draft
```

### API

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/creator/drafts` | 已认证 | 草稿清单（含 `valid` / `error_count`） |
| GET | `/api/creator/draft?id=` | 已认证 | 单份草稿（meta + `files[{name,content}]`） |
| POST | `/api/creator/draft` | **管理员** | `{kind, requirement}` → 生成新草稿 |
| POST | `/api/creator/revise` | **管理员** | `{id, feedback}` → 同目录升 version，旧版进 `.history/` |
| POST | `/api/creator/validate` | **管理员** | `{id}` → 重跑静态验证 |
| POST | `/api/creator/delete` | **管理员** | `{id}` → 删草稿 |

**没有任何安装 / 装载 / 启用入口。** 这不是遗漏，是 MVP-1 的刻意边界。

### 数据落点

```
<DATA_DIR>/creator_drafts/
    cr_<yyyymmddHHMMSS>_<hex6>/
        meta.json          id/kind/requirement/summary/risks/model/version/status/
                           files[]/rejected[]/validation{}
        <生成的文件>        插件: <NAME>.py + 可选 zxplugin.json
                           技能: index.js + skill.json
    .history/<id>/v<N>/    旧版本快照 —— 必须在草稿目录**外**
```

> 🔴 `<id>/history/` 不能放：插件静态检查器拒绝嵌套目录，会导致本来合格的草稿
> 永远被判 `nested_directory`。

---

## 3. 模型调用链

```
generate_draft / revise_draft
  → _build_messages(kind, ...)                    组契约 + 模板参考
  → _call_model(api_cfg, system, user)
      → model_providers.get_provider(cfg)         与 Brain 聊天同一个工厂
      → provider.chat(payload, timeout=180)       OpenAI chat.completions 体
      → 取 ["choices"][0]["message"]["content"]
  → _extract_json(text) → _write_files → validate_draft
```

- `api_cfg` 由 `server.py` 传入，就是当前的 `API_CFG`（配置里的 models 段）。
- 请求体：`{model, messages:[system,user], temperature:0.2, max_tokens:4096}`。
- 失败分类原样上报，不吞：`ProviderUnavailable`（连不上/超时）、`ProviderHTTPError`
  （带 status）、`ProviderResponseError`（响应不可解析）、`ProviderError`、其他异常。
- 模型没按契约返回 JSON → `_persist` 直接失败**且不落地任何文件**。

静态验证复用 P4 检查器，**只读文件 + `ast.parse` / `json.load`，不 import、不执行**：

```
plugin_structure.check_plugin_directory(d)
skill_structure.check_skill_directory(d, package_name=<skill.json 的 id>)
```

`skill` 之所以显式传 `package_name`：草稿目录名（`cr_...`）不是最终技能名，
与 ZIP 安装器同款处理。

---

## 4. 测试

### 单元 / API 测试
`brain/tests/test_creator_mode.py` —— **20/20 通过**

- 单元 8 项：`_extract_json`（围栏/废话/括号在字符串内）、`_safe_relpath`、
  `_draft_dir` 拒绝非法 id、`_persist` 落盘验证、路径逃逸拒绝、坏代码 surfaced
  为 `python_syntax`、`_call_model` 未配置 / 真 HTTP fixture / 500 分类 / 错凭据。
- HTTP 12 项：真起 Brain 子进程 + 假模型 fixture。
  - `test_01` 生成插件端到端 + 断言草稿在独立工作区、不在 `plugins/`
  - `test_03` 模型失败 → 400 明说且不落地
  - `test_04` 坏代码 → 存盘但验证失败
  - `test_07` 修改升 v2 + `.history/<id>/v1` 存在 + 草稿目录内**无** history
  - `test_10` 路径逃逸不落地；`test_11` 无 token 401；`test_12` 删除

运行方式（**必须 `cd brain`**：`python -m pytest` 把 CWD 放进 `sys.path`，
从仓库根跑会 `ModuleNotFoundError: codex_adapter`）：

```bash
cd brain && python -m pytest tests/test_creator_mode.py -q     # 20 passed
cd brain && python -m pytest tests/ -q                          # 全量
```

### 全量回归基线
157 项 collected，creator 20 全过。剩余 **5 FAILED + 1 ERROR** 与 P4 记录的
Windows 环境基线**逐条同名**（保留名 ×3、symlink 权限 ×2、tempfile 清理），
与本次改动无关：

```
test_plugin_structure.py::test_windows_reserved_directory_names_are_rejected
test_plugin_transactions.py::test_reserved_zip_filename_rejected
test_plugin_transactions.py::test_target_symlink_rejected
test_skill_host_security.py::test_windows_reserved_skill_name_is_rejected
test_skill_structure.py::test_paths_symlinks_reserved_and_case_conflicts
```

### Electron 界面演示
`docs/creator-mode-mvp-1/harness-creator-demo.js` —— **20/20**，
`ev-45f8473/results.json`。

流程：Node 内起 OpenAI 兼容假模型（按请求里 `ZXFIX_*` 标记切模式、带请求计数）
→ 起隔离 Brain 子进程（临时 `VIGIL_HOME` + 随机端口 + 随机 token）
→ 起真 Electron（临时 profile、`unset ELECTRON_RUN_AS_NODE`、CDP 连接）
→ 走完整 UI 流程。截图 9 张按仓库惯例**不入库**（`*.png` 已全局忽略）。

---

## 5. 未完成 / 明确不做

- **没有安装**。草稿卡上没有安装/启用按钮。
- 静态验证**只查结构与 Manifest**，**通过 ≠ 沙箱安全**（卡片上原话写死）。
- 不自动执行生成的 Python / JavaScript，不自动安装第三方依赖。
- 不做运行时沙箱、资源配额、行为审计。
- 未接管 P3 审批策略；本次未改任何审批代码。
- 无能力市场 / 分享 / 版本对比 UI。

---

## 6. 安全限制（改动前务必看）

1. 草稿只写 `<DATA_DIR>/creator_drafts/`；模块内**没有** install/reload 入口。
2. 生成必走真实 provider 链路，**不得**加"关键词匹配直接出模板"的旁路。
3. 静态验证**不得**改为 import / exec 生成代码。
4. 模型给的文件名**先校验再落盘**（`_safe_relpath`），不得跳过。
5. 历史版本必须留草稿目录外。
6. 不自动装依赖；缺依赖应当作为**风险项**呈现给人看。
7. 客户端渲染层 CSP `connect-src 'none'`：所有网络走 `API.get/post`
   → preload → ipcMain（主进程加 Bearer）。**不要在渲染层直接 fetch。**
8. 模型输出**不可信**，一律 `esc()` 后再进 DOM。

---

## 7. 开发时踩过的坑（都写进代码注释了）

1. **`API.post/get` 返回 `{ok,status,json}`**，业务体在 `r.json`。必须用 `pick()` 拆包，
   直接读 `r.draft` 会永远拿到"生成失败：未知错误"。
2. **TDZ**：`applyCreatorMode()` 的立即调用必须晚于 `let creatorMode` 声明，
   否则整个聊天页画不出来（`Cannot access before initialization`）。
3. **消息流重绘冲掉草稿卡**：`loadMsgs` 按历史条数变化重绘，草稿卡不在历史里。
   重绘前摘出 `.crturn`，画完按原顺序贴回末尾。
4. **快照顺序**：先拷旧版再写新文件；反了会把新内容当"旧版本"存。
5. **`U.md()` 不包裹 `.md`**：调用处要自己包一层 `<div class="md">`，
   否则 `.cr-code .md pre` 规则不生效。
6. **假模型 fixture 只认最后一条 user 消息**：system 提示词里含"技能"字样，
   扫整个请求体会把插件请求误判成技能请求。
7. 这台机器 `spawnSync` 一律 EBUSY（连绝对路径也一样）→ harness 里必须异步 spawn。

---

## 8. 复现

```bash
# 测试
cd brain && python -m pytest tests/test_creator_mode.py -q

# 界面演示（截图落到 docs/creator-mode-mvp-1/ev-<commit>/）
node docs/creator-mode-mvp-1/harness-creator-demo.js
```

演示 harness 需要的先决条件：仓库内 `client/node_modules/electron` 与 `client/node_modules/ws`，
以及一个装了 `httpx` 的 Python。harness 会依次探测候选解释器；如果都不匹配本机布局，
用环境变量显式指定（推荐）：

```bash
VIGIL_PYTHON=/path/to/python-with-httpx node docs/creator-mode-mvp-1/harness-creator-demo.js
# 或给多个候选：VIGIL_PYTHON_CANDIDATES="D:/py/python.exe;E:/other/python.exe"
```

harness 里不写死任何机器的绝对路径。
