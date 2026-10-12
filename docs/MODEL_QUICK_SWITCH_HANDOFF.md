# 模型快速切换 · 交接文档

分支 `feature/chat-model-quick-switch`（从 `creator-mode-mvp-1` 切出）。

## 一句话

在主页聊天输入框旁边加了一颗「当前模型名 ▾」按钮：点开是**用过的模型清单**，
点一条就直接切过去，不用再跳设置页；切不了的一律明说，绝不静默换成别的模型。

## 用户要什么（需求 → 落点）

| 需求 | 落在哪 |
| --- | --- |
| 添加过的模型自动出现在下拉 | `/api/models/apply` 保存成功后自动记进清单（`remember_current`） |
| 删除/禁用后同步更新 | `POST /api/models/forget` + 下拉里的 `×`；删掉当前这条也不动生效配置 |
| 同名模型跨服务商分得开 | 判重键是 `(provider, base, model)` 三元组，地址也进键 |
| 切换不用重配 API Key | 每家密钥按家存在 `api_keys`，切换时自动换上那家的（复用 `apply_cfg`） |
| Electron 重启后清单还在 | 清单存在 `config.json` 的 `saved_models`，与配置同生共死 |
| 当前选择能正确恢复 | 清单里有 `active` 标记；当前那把不在清单里就作为「(未保存)」置顶显示 |
| 切换后下一条真的用新家新模型 | `quick_switch` 仍走 `apply_cfg` + `save` + 刷 `API_CFG`，与设置页同一条路 |
| Creator 草稿用当前选中的模型 | Creator 走同一个 `API_CFG`，传输层实测确认（见 M12） |
| 保留聊天记录、不清空会话 | 切换只改模型配置，不碰任何 `_hists` / 存档 |
| 生成中不许「意外改变正在跑的请求」 | 客户端生成中禁掉按钮；服务端每轮取配置快照（`Brain._api_snapshot`） |
| 不可用/缺凭据/连不上要明说 | 三道检查 + `warn` 透传，失败一律 `ok:false` 带人话 |
| 不静默切到别的模型 | 缺密钥、被服务商拒绝、切换未生效（provider/model/base 三重核对）全部拦下 |
| 密钥不进 localStorage / 日志 / 聊天记录 | `/api/models/saved` 只回布尔 `has_key`，连打码的都不给；切换请求体只有 `{provider, model}` |
| 不动工具权限 / P3 审批 / Skill Host 安全限制 | 未触碰；新增路由与既有 `/api/models/apply` 同级鉴权 |

## UI 入口

主页聊天页 composer 里，与「创作：关/插件/技能」开关并列的那颗 `#modelBtn`
（`client/src/js/pages.js:369` 起的 `#modelPop` 浮层）。

- 浮层复用现有 `.localpop` 样式（和 `#localPop` 同一个家族），位置贴 composer 右上角
- 每行：显示名 / `服务商 · 模型ID` / 状态（`当前` / `已存密钥` / `缺密钥`）/ `×` 移除
- 头部有搜索框（按名字、服务商、模型 ID 过滤），底部是「添加或管理模型」→ 跳设置页模型子页
- 模型名在**三处**同步刷新：composer 按钮、顶栏 `.chat-model`、下拉头部 `#mqCur`

## 数据流

读（`GET /api/models/saved`）：

```
config.json
  ├─ saved_models: [{provider, base, model, name, added_at}, …]   ← 下拉里列的就是它
  ├─ api_keys: {siliconflow: "sk-…", xiaomi: "…"}                 ← 判 has_key 用
  └─ provider / api_base / model                                  ← 判 active 用
        ↓ model_hub.public_saved(cfg)
  {models:[…], current:{…}}   ← 不含任何密钥，has_key 是布尔
```

写（`POST /api/models/quick-switch`）：

```
{provider, model}
  ↓ remember_current(cfg)              ← 切走前先记下当前这组，否则它从下拉里消失
  ↓ 找清单条目 (mod&provider 匹配)
  ↓ 密钥检查（has_provider_key / 老配置兜底 _any_provider_key）
  ↓ probe_provider(base, key)          ← GET {base}/models，401/403 直接拦
  ↓ apply_cfg(cfg, {provider, api_base, chat})
  ↓ 三重核对：provider/model 变了？api_base 真的换成 want_base 了？云端有 key？
  ↓ model_hub.save(nc) + 就地更新 api_config / API_CFG
  → {ok, changed:[人话], warn, current}
```

`apply_cfg` 仍是**唯一**的配置写入口 —— 快速切换是它的薄封装，没有第二套模型配置。

## API

| 方法 | 路径 | 作用 | 鉴权 |
| --- | --- | --- | --- |
| GET | `/api/models/saved` | 下拉数据（清单 + 当前生效） | 任意已认证身份（已加入 `DEVICE_GET_ALLOW`，只读无密钥） |
| POST | `/api/models/quick-switch` | 按清单切一条 | 同 `/api/models/apply` |
| POST | `/api/models/forget` | 从清单移除一条 | 同上 |
| POST | `/api/models/apply` | **既有**，保存设置；现在顺带把当前那组记进清单 | 同上 |

## 代码文件

| 文件 | 改动 | 关键内容 |
| --- | --- | --- |
| `brain/model_hub.py` | +290 | `probe_provider`(L205)、`_any_provider_key`(L376)、清单族 `SAVED_MAX`/`_short_name`/`_norm_entry`/`_entry_key`/`saved_models`/`remember_model`/`remember_current`/`public_saved`(L450-589)、`quick_switch`(L590)、`forget_model`(L664) |
| `brain/server.py` | +85 | `DEVICE_GET_ALLOW` 加 `/api/models/saved`(L177)、`/api/models/apply` 记清单(L2249)、`/api/models/quick-switch`(L2292)、`/api/models/forget`(L2332)、`GET /api/models/saved`(L2778) |
| `brain/brain.py` | +62 | `_api_snapshot`(L423)、`_provider(cfg=None)`(L439)、`_provider_ready(mp, cfg=None)`(L455)；三处取快照：`draft_native`(L888)、`stream_chat`(L1207)、`speak_on_scene`(L1668) |
| `client/src/js/pages.js` | +223 | `modelQuickBind(view, ctx, hooks)`(L69)、composer 浮层标记(L369)、接线(L430)、`updateComposer` 里生成中禁按钮 |
| `client/src/chat.css` | +15 | `.modelpop` 定位/行样式/移除按钮/底部操作条 |
| `brain/tests/test_model_quick_switch.py` | 新 682 行 | 24 条（`ModelHubUnitTests` 11 + `QuickSwitchHttpTests` 11 + `QuickSwitchEdgeHttpTests` 2） |
| `docs/model-quick-switch/harness-model-switch.js` | 新 663 行 | Electron 真实界面联动，22 条断言 |

## 关键实现细节（踩过的坑）

1. **生成中途切换会串台**：`CloudProvider` 在**构造时**快照 `api_base/api_key/model`，
   而 payload 里的 `model` 是**发送时**才读的 —— 切一下模型就变成「地址是老家的、
   模型名是新家的」。所以每轮开头 `_api_snapshot()` 取一份浅拷贝，provider 与
   payload 同源。**切换只影响下一轮。**
2. **地址必须进判重键**：`custom` 下两台机器各跑一个 vllm，模型名相同；
   只按 `(provider, model)` 判重会把两条合成一条，切过去打的是另一个地址。
3. **切走前先记住当前这组**（`quick_switch` 开头 + `/api/models/apply` 改动前各一次）：
   顺序反了，用户点别的模型之后，原来那个会从下拉里消失，再也点不回来。
4. **`apply_cfg` 会套用标准厂商地址**：`switched and prov != "custom"` 那条分支会用
   `PROVIDERS[prov]["base"]` 覆盖自定义/代理地址，所以切换后必须再核一遍 base。
5. **老配置不能被锁在门外**：`api_keys` 是后来才有的，老配置只有一把 `api_key`。
   判据是「一把都没登记过」才把手上这把当它的（`_any_provider_key`）；
   一旦清单里有别家，这家就是真没配。
6. **`list_drafts()` 的摘要里没有 `model` 字段**（MVP-1 的设计），所以验证
   「Creator 用对模型」不能查这个接口，要看草稿卡 + 传输层实际打给谁。
7. **配置更新不用 `clear()+update()`**：`clear()` 有一瞬整份配置是空的，
   另一个线程正在起步的那轮会看到「模型没配好」。改成先删多余键再 `update`。
8. **测试侧安全闸**：单测与 harness 都断言任何 base 必须 `http://127.0.0.1:…`，
   漏传 `api_base` 时会**断言炸掉**而不是真去打厂商（第 4 条的隐患就靠它钉住）。

## 测试

### 单元 / HTTP（隔离 HOME、随机 token、随机端口、本机假服务商）

```
cd brain && python -m pytest tests/test_model_quick_switch.py -q
→ 24 passed
```

覆盖：去重、同名跨家两行、`SAVED_MAX` 上限、`public_saved` 无密钥、unsaved 前置、
未知模型拒绝、缺密钥拒绝、服务商拒密钥拒绝、连不通有 `warn` 但仍切、切换真的搬
provider/base/key、forget 只动清单；以及 `M1–M11` HTTP 全链路 + 生成中途切换。

### 全量回归（改动前后逐条对比）

| | 收集 | 通过 | 跳过 | 失败 | 错误 |
| --- | --- | --- | --- | --- | --- |
| 基线 `42c6f1a` | 162 | 152 | 4 | 5 | 1 |
| 本分支 | 186 | 176 | 4 | 5 | 1 |

多出来的 24 条全是本分支新增且全绿；5 F + 1 E 与基线**逐条同名**，是本机 Windows
环境固有问题（保留文件名 ×3、symlink 权限 ×2），与本改动无关。

### Electron 真实界面联动（`docs/model-quick-switch/ev-<commit>/`）

```
node docs/model-quick-switch/harness-model-switch.js
→ 22/22（results.json 里有每条的证据）
```

真窗口、真 CDP 驱动、真 `pages.js` → preload/ipcMain → 真 `server.py`；
两家假服务商在本机随机端口，**不碰任何真实厂商**。逐条证据在 `results.json`：

| 断言 | 证的是什么 |
| --- | --- |
| D0/D1 | 隔离 Brain 就绪、Electron 连的是它 |
| M1 | composer 与顶栏都显示当前模型 |
| M2/M3/M4 | 下拉 3 条、两家服务商分开、唯一选中标记、缺密钥有标记 |
| M5/M6 | 管理入口在、搜索能过滤 |
| M7 | 缺密钥点击**明确报错**且当前模型不变 |
| M8/M8b | 设置页保存（带密钥）成功、能切回 |
| M9 | 点一下切换：服务端 + 按钮 + 顶栏**三处一致** |
| M10 | 下一句真的打在小米上、模型对、硅基没收到 |
| M11 | 切换没清空会话历史 |
| M12 | Creator 草稿用当前选中的小米模型（传输层 + 卡片两处一致） |
| M13 | 生成中按钮禁用、下拉打不开 |
| M14 | 生成中从 API 直接切走：在飞那一轮仍打在老家原模型，另一家零请求 |
| M15/M15b | 移除条目后下拉与接口同步；**移除当前这条**配置不变、标「未保存」、聊天照旧 |
| M16 | 页面文本与响应里都没有任何 API Key |
| M17 | Electron 重启后清单/选择/按钮都正确 |
| M18 | `electron.log` 无未捕获异常 |

截图按惯例不入库（`.gitignore` 挡 `*.png`），文本证据在 `results.json` 与 `brain.log.tail`。

## 明确不做 / 未做

- **不做会话级模型**：仓库里根本没有「每个会话一份模型设置」这一层（会话只分
  历史，按渠道 `_hists[src]`）。硬造会违反「不创建第二份模型配置系统」。
  当前是**全局单份**，新会话天然继承最近选择。
- 不做计费倍率、品牌图标、营销标签。
- 不做「连着生成中也能切」：客户端直接禁掉按钮，服务端快照兜底。
- 不做模型自动降级 / 静默兜底（这是**故意**的）。
- 未改 Agent 工具权限、P3 审批策略、Skill Host 安全限制。

## 已知风险与局限

1. **全局单份模型**：切换会影响该大脑上的**所有渠道**（微信私聊/群/桌面端），
   因为它们共用一份 `api_config`。这与改造前的行为一致（设置页换模型也是全局），
   但产品上要知道这一点。
2. `probe_provider` 探不通时**不阻断**（本机部署、临时断网都可能），
   只在响应里带 `warn`。界面会红字显示「没能连上服务商验证…」。
3. `has_key` 的语义是「这家存过密钥」，不是「密钥现在有效」——
   密钥失效要等切换时的探活才知道。
4. 清单**没有清理策略**（只保留最近 40 条），不会按"服务商已下线"自动淘汰。
5. 快速切换与 `/api/models/apply` 同级鉴权（任意已认证身份可调）——
   与改造前一致，未额外收紧，避免打断手机 App 的模型页。

## 复现

```bash
# 1. 单测
cd brain && python -m pytest tests/test_model_quick_switch.py -q

# 2. 真实界面联动（需要 client/node_modules 里的 electron + ws；
#    需要一个带 httpx 的 Python，可用 VIGIL_PYTHON 指定）
node docs/model-quick-switch/harness-model-switch.js
```
