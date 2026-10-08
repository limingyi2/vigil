# Vigil

一个常驻云端的对话 Agent。云端跑着它的大脑，桌面和手机是纯客户端。

**人格与项目无关。** 人格是 `brain/config/personas/*.json` 里各自定义的配置，
仓库不预置角色。项目叫 Vigil，人格叫什么是人格自己的事。

技术栈：Python 3.12 · PyQt6（桌面）· Android WebView（手机）·
LLM 走 OpenAI 兼容接口 · 语音走阿里云百炼

---

## 目录

| 章节 | 内容 |
|---|---|
| [装起来跑通](#装起来跑通) | 三个端各怎么启动 |
| [配 API 与模型](#配-api-与模型) | 接哪个服务商、怎么换 |
| [写插件](#写插件) | 加一个工具/一个功能 |
| [装技能（JS）](#装技能js) | 另一条通道 |
| [本地知识库](#本地知识库) | 让她翻你自己的文档 |
| [改人设](#改人设) | 人格文件怎么改 |
| [功能开关](#功能开关) | 按渠道开停 |
| [模块表](#模块表) | 每个文件负责什么 |
| [构建 APK](#构建-apk) | 打包手机端 |
| [测试](#测试) | 跑用例 |

---

## 装起来跑通

三个端：大脑在云上，桌宠和手机都是纯客户端，自己那份本地存储只存界面状态。

### 云端大脑

```bash
pip install -r brain/requirements.txt
cp brain/config/config.example.json brain/config/config.json   # 填 api_key / token
python brain/server.py                                          # 监听 :8788
```

`brain_token` 没配时接口只接受本机回环。手机要连就得配，配完用
`Authorization: Bearer <token>` 走请求头。

`fastembed` 第一次跑会下载 bge-m3 模型（记忆检索用），需要联网。

### 桌面端

桌宠走 SSH 隧道连云端，本地只监听 127.0.0.1:18787。

```bash
ssh -L 18787:127.0.0.1:8788 <user>@<server>       # 或双击 pet/launchers/run_pet_cloud.bat
pip install -r pet/requirements.txt
python pet/pet.py
```

### 手机端

```bash
python deploy/rebuild_apk.py
```

装好后「我 → 设置」里填服务地址与 token，只需填一次。

---

## 配 API 与模型

改的是 `brain/config/config.json`（从 `config.example.json` 复制）。也可以在
App「设置 → 模型」或 Web 面板「系统配置」里改，不用重启。

```json
{
  "provider": "xiaomi",
  "api_base": "https://api.xiaomimimo.com/v1",
  "api_key": "<你的 key>",
  "chat": "mimo-v2.6-pro",
  "vision": "",
  "image": "",
  "audio": ""
}
```

- `chat` 对话模型，**必填**
- `vision` / `image` / `audio` 留空就是不启用那项
- `api_base` 结尾一般是 `/v1`；自建服务按它实际的路径填

### 加一家服务商

只改 `brain/model_hub.py` 一处：

```python
PROVIDERS = {
    "siliconflow": {"name": "硅基流动", "base": "https://api.siliconflow.cn/v1",
                    "models_doc": "..."},
    "xiaomi":      {"name": "小米 MiMo",  "base": "https://api.xiaomimimo.com/v1",
                    "models_doc": "https://mimo.mi.com/docs"},
    "custom":      {"name": "自定义",     "base": "", "models_doc": ""},
}
```

加完重启，App 和面板上的服务商列表里自动多一家 —— 前端是按服务端返回的
`providers` 渲染的，不用改前端代码。

模型列表由服务端问服务商的 `/v1/models` 拉，按名字关键字自动分成
对话 / 看图 / 生图 / 语音四栏；拉不到就退回内置清单，界面会标明这份可能不全。
点「重新拉取」会跳过缓存重新问一次（换 key 之后必须点）。

> 兜底清单里的模型 id 要照实测写，照文档抄会给出选不上的项。

### 其他要配的

| 配置项 | 在哪 | 说明 |
|---|---|---|
| `brain_token` | `config.json` | 主人钥匙：配了它接口就要鉴权，客户端设置页填同一个 |
| 设备注册 | `data/devices.json` | 每个客户端用主人钥匙换一把**设备 token**，能单独吊销；表里只存哈希 |
| 主动搭话 | `config.json` | 什么时段、间隔多久 |
| 人格 | `brain/config/personas/*.json` | 见下面[改人设](#改人设) |
| 版本号 | `version.json` | 构建时注入到 APK |

---

## 写插件

加一个功能不用改核心代码，往 `brain/plugins/` 丢一个 `.py` 就行。

最小可用的插件：

```python
# brain/plugins/hello.py
NAME = "hello"
DESC = "打招呼"

def on_turn_in(ctx):
    """每轮聊天前跑一次。返回字符串会拼进系统提示。"""
    return "现在是新的一天，记得主动问好。"

def prompt(ctx, native):
    """往提示词里加内容。native=True 表示主链路在用原生 function calling。"""
    return ""

def on_reply(reply):
    """她回完话之后跑一次。"""
    pass
```

### 四个钩子

| 钩子 | 什么时候跑 | 能做什么 |
|---|---|---|
| `on_turn_in` | 每轮聊天前 | 返回文本拼进系统提示；`ctx.veto(why)` 可以**否决这轮**（不问她） |
| `prompt(ctx, native)` | 组装提示词时 | 追加内容。`native` 区分主链路是原生 FC 还是标签解析 |
| `on_reply` | 她回完话 | 后处理，比如记账、发通知 |
| `start(brain)` | 服务启动时 | 拿 brain 引用，起自己的线程 |

### 加一个工具（让她能调）

光有钩子只能改行为。要让她**主动调用**，得给工具声明 schema：

```python
NAME = "weather"
DESC = "查天气"

TOOL_SPECS = [{
    "name": "weather",
    "description": "查询某个城市的实时天气",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "城市名"}},
        "required": ["city"],
    },
}]

def invoke(name, args):
    """真正执行。args 是 dict。返回字符串，或 dict（会被转成 JSON）。"""
    if name != "weather":
        return None
    return fetch_weather(args.get("city"))      # 你的实现
```

🔴 **`TOOL_SPECS` 不能少**。只声明 `TOOLS` + `TOOL_HELP` 走的是标签解析那条
兼容路径；主链路读的是请求里的 `tools` 字段，没有 schema 的工具模型看不见。
症状是"装了、列表里有、开关能点、就是不用" —— 很难查。

### 声明功能开关

```python
FEATURE = "weather"       # 受功能开关和渠道开关管
```

不声明的话它在所有渠道都开着。

### 装 / 卸

面板「插件」页拖个 `.zip` 就能装。zip 里只允许 `.py` / `.json` / `.md` / `.txt`，
且只能有一个 `.py`。

### 哪些 import 会失败

```python
from config import X          # ❌ brain/ 下没有 config.py
from runtime import CFG_PATH  # ✅ 配置路径从这儿拿
from paths import PLUGIN_DATA_DIR   # ✅ 插件自己的持久化目录
```

🔴 这类错**模块能 import、加载器也验得过** —— 错在函数体里的 import 又被
`except Exception` 吞掉，所以插件静默不干活、零日志。写完直接调一次那个函数
把 import 逼出来。

详见 [`docs/插件系统.md`](docs/插件系统.md)。

---

## 装技能（JS）

Python 插件是 import 进大脑进程的。外面能下到的技能包（QQ Agent 等用的那套，
`skill.json` + `index.js`）是 JavaScript，塞进同一个上传口只会装上一个必然
import 失败的假条目，所以另开一条通道：

```
Python 插件  brain/plugins/*.py     → import 进大脑进程
JS 技能      brain/skill_host/      → Node 侧跑，大脑用 HTTP 调
```

模型用两个固定入口，不把每个技能名硬编进工具清单：

- `skill_list` —— 看有什么
- `skill_call` —— 调其中一个

宿主默认只监听 `127.0.0.1:8790`，先起它：

```bash
cd brain/skill_host && node server.js
```

技能包形状是 `包名/index.js` 或 `包名/包名/index.js`。
详见 [`docs/JS技能支持.md`](docs/JS技能支持.md)。

---

## 本地知识库

把文档丢进去，她就查得到。放在 `brain/data/kb/`，按「人设 × 渠道」分区，
**不需要装新依赖** —— 嵌入复用记忆库那条路（云端 bge-m3，失败降级本地 fastembed）。

```bash
# 导入一个文件或整个目录
curl -X POST http://127.0.0.1:8788/api/plugin/kb/ingest \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"path": "D:/文档/手册"}'

# 看她能查到什么（门槛设 0，看全貌）
curl "http://127.0.0.1:8788/api/plugin/kb/search?q=怎么配&k=3" \
  -H "Authorization: Bearer <token>"
```

她聊天时每轮自动检索一次并注入命中的段落（门槛 0.45，宁可不给也不塞无关内容），
也可以自己调 `kb_search` 主动去查。嫌每轮多一次 embedding 就把 `inject` 关掉，
只留工具。

接口都在 `/api/plugin/kb/*`，靠插件机制接线，**没改 `server.py`**。
零 API 成本的做法（用 D 盘那份 4.3G 的 bge-m3 本地跑）见
[`docs/本地知识库.md`](docs/本地知识库.md)。

---

## 改人设

人设是 `brain/config/personas/` 下的 json，**仓库不预置角色**，自己写。

```json
{
  "name": "麦麦",
  "nicknames": ["麦麦"],
  "personality": "说话简短，会开玩笑",
  "relation": "女朋友",
  "notes": "这里可以粘一整份人设，会整段拼进系统提示"
}
```

- `name` 她自称什么
- `nicknames` 群里 @ 她、或者直接叫名字时，靠这个判断是不是在叫她
- `notes` 整份人设正文，粘进去就行

改完热更新，不用重启。

### 换人格会换记忆分区

🔴 记忆、摘要、存档都按 `人设key.渠道` 算。换成另一个 key 就是**一片空白**
——旧记忆还在，只是不会再被检索到。所以换人格前想清楚。

配置里用 `persona_file` 指定当前用哪一份，App 和面板改人设会热切换。

---

## 功能开关

`brain/features.py`，按「人设 × 渠道」存。App 和面板都能点，改完即时生效。

- 全局 `features` 段 = 任何渠道都没覆盖时的初值
- `features_by_src.<渠道>` = 该渠道的覆盖

⚠️ 读写必须同一套规则。历史上读和写不一致过（界面显示已关、运行时其实全开），
最难排查的一类坑。测试见 `_test_features_src.py`。

---

## 模块表

```
   ┌────────────────┐   SSH 隧道 18787 → 8788    ┌──────────────┐
   │ 桌宠 pet（Win）│ ──────────────────────────► │              │
   └────────────────┘                            │  云端大脑     │
   ┌────────────────┐   HTTP :8788（token 鉴权） │ brain/server │
   │  Android App   │ ──────────────────────────► │              │
   └────────────────┘                            └──────┬───────┘
                                                        │
                         记忆 / 摘要 / 约定账本 / 日记 / 朋友圈 / 图片
```

大脑只有一份，在云端。`brain/server.py` 原来是个 2200 行的单文件，
按领域拆成 runtime / reply / randimg / selfie / imggen / proactive /
moment_flow / jobs 等模块，对外接口零变化。

| 模块 | 作用 |
|---|---|
| `brain/server.py` | HTTP 服务与全部路由：装配大脑、聊天管线 |
| `brain/brain.py` | 决策核心：上下文组装、人设注入、失败兜底 |
| `brain/persona_store.py` | 人设：白名单字段、关系档、身份底线 |
| `brain/memory_store_v2.py` | 长期记忆，bge-m3 向量检索 |
| `brain/recap_store.py` | 历史摘要，日→周→月三层压缩，按人设 × 渠道分区 |
| `brain/agenda.py` | 约定账本：到期提醒，过期标 missed |
| `brain/tools.py` | 工具调用：原生 function calling + 兼容标签解析 |
| `brain/model_hub.py` | 服务商表、模型列表拉取与分组 |
| `brain/planner.py` | 多步规划：判是否需要规划、逐步执行、结果核对 |
| `brain/chatty.py` | 话痨度（1~10），按渠道各一份 |
| `brain/paths.py` | 分区路径：记忆 / 摘要 / 存档按「人设 × 渠道」算 |
| `brain/life_engine.py` | 她的生活推演与日记 |
| `brain/moments.py` | 朋友圈 |
| `brain/voice.py` / `imggen.py` / `selfie.py` | 语音 / 生图 / 自拍 |
| `brain/features.py` | 功能开关，按渠道覆盖 |
| `brain/plugin_host.py` | 插件加载面：扫 `plugins/`、汇总工具 schema、拼提示词 |
| `brain/plugin_ops.py` | 插件管理面：装卸 .zip、元数据、渠道开关 |
| `brain/kb/` | 本地知识库（RAG）：切块、嵌入、检索；`plugins/kb.py` 负责接线 |
| `brain/skill_host/` | JS 技能的 Node 宿主 |
| `pet/` | Windows 桌宠（PyQt6） |
| `vigil-client/` | 桌面客户端（Electron）；排版定稿在 `docs/client-layout-v1.html` |
| `android-app/` | Android 客户端 |
| `admin/` | Web 管理面板（单文件 html） |
| `wechat_bridge/` | 微信接话，**独立仓库** |

### 微信接话

拆到 [`zhixia-wechat-bridge`](https://github.com/limingyi2/zhixia-wechat-bridge) 了。
两边没有代码级依赖，靠三个值对接：服务地址、token、微信登录态 ——
都只存在各自机器的本地配置里。

### 仓库关系

| 仓库 | 放什么 |
|---|---|
| 本仓库 | 大脑、桌宠、App、管理面板、插件与技能 |
| [`zhixia-wechat-bridge`](https://github.com/limingyi2/zhixia-wechat-bridge) | 微信接话脚本、虚拟机控制、安全闸 |

---

## 构建 APK

```bash
python deploy/rebuild_apk.py                     # gradle 全量构建
python deploy/rebuild_apk.py --version 29 --name 5.5   # 顺便升版本号
```

手机端 UI 唯一真源是 `android-app/app/src/main/assets/chat.html`。
构建脚本往里注入 `APP_CODE` 和服务地址，打完还原成空占位 ——
所以仓库里那份看到 `APP_CODE = 0` 是正常的。

---

## 测试

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -t tests
```

不联网、不读真实配置。CI 在每个 push 上跑这套用例 + 一次全仓 `compileall`。

新增用例当天就靠它抓出过多个真 bug（人设静默回退、插件静默失效、
密钥泄露、面板改了但不生效）。

---

## 文档

| 文档 | 解决什么问题 |
|---|---|
| [`插件系统.md`](docs/插件系统.md) | 加载面与管理面怎么分、渠道开关复用什么、.zip 的形状约定 |
| [`本地知识库.md`](docs/本地知识库.md) | 知识库和记忆为什么是两回事、嵌入三条后端怎么选、门槛为什么是 0.45 |
| [`JS技能支持.md`](docs/JS技能支持.md) | 两条通道为什么不能合并、Node 宿主的契约 |
| [`设计与取舍.md`](docs/设计与取舍.md) | 能力构成、踩过的坑、实测数据 |
| [`推理架构方案-ReAct-Plan-思维链.md`](docs/推理架构方案-ReAct-Plan-思维链.md) | 哪些能力能挪到本地、哪些必须留在云端 |
| [`时间感与主动性修复.md`](docs/时间感与主动性修复.md) | 她分不清"刚说的"和"六小时前说的"怎么修的 |
| [`人设瘦身对照.md`](docs/人设瘦身对照.md) | 提示词瘦身前后的逐条对照 |
| [`语音平台成本对比.md`](docs/语音平台成本对比.md) | 各家月成本换算 |
| [`游戏内录音取样指南.md`](docs/游戏内录音取样指南.md) | 想换音色时怎么录、哪些角色没有公开数据集 |

---

## 许可

MIT，见 [LICENSE](LICENSE)。
