# JS 技能（skill）支持

知夏除了自己的 Python 插件（`brain/plugins/*.py`），还能用外面下的 **JS 技能包**——
就是那种带 `skill.json` + `index.js` 的东西（QQ Agent / 某些机器人框架用的那套）。

## 为什么是两条通道，不是把 .js 塞进插件上传口

用户第一次试的是：把技能 zip 拖进面板的插件上传区，浏览器直接报「不支持」。
原因是 `plugin_ops.ALLOWED_EXT` 只准 `.py / .json / .md / .txt`，而技能里是 `index.js`。

**这个限制是对的，不该放宽。** 放宽的后果不是"能跑了"，而是装上一个必然
import 失败的假条目：列表里看着有、开关也能点、就是不生效。宁可明确拒绝。

所以另开一条通道：

```
Python 插件  brain/plugins/*.py     → import 进大脑进程
JS 技能      skill_host/skills/*/   → Node 侧跑，大脑用 HTTP 调
```

## 结构

| 文件 | 干什么 |
|---|---|
| `brain/skill_host/server.js` | Node 宿主。垫出 QQ Agent 那套 `api`/`ctx` 契约，暴露 HTTP 接口 |
| `brain/skill_host/skills/` | 装技能的地方。每个技能一个目录 |
| `brain/plugins/skill.py` | 知夏侧插件。把技能工具接进模型，给模型两个入口 |

### 创作前的静态检查

Skill 目录可以先用检查器验证，不会导入或执行 JavaScript：

```bash
python tools/check_skill_structure.py templates/skill-basic
```

`skill.json` 是可选的，缺失时保留历史 `index.js` Skill 的兼容性。存在时必须是
JSON 对象；`id` 要与包目录名一致，`entry` 必须指向根目录内的入口文件。单层目录
支持 `index.js`、`index.mjs` 和 `index.cjs`，后两者需要在 Manifest 中明确写出
`entry`。当前 Host 只会对内层 `index.js` 自动下钻同名双层目录，因此双层的
`index.mjs` / `index.cjs` 会被检查器拒绝。

检查器还会拒绝路径越界、反斜杠、控制字符、Windows 保留名称、符号链接、特殊文件
和大小写冲突路径。未知 Manifest 字段会保留，方便未来扩展。安装接口会在任何
持久化目录写入前检查暂存目录；静态检查不执行 Skill 代码。

### Host 接口（只监听回环且需要认证）

| 路由 | 用途 |
|---|---|
| `GET /health` | 活着没。面板判活、启动脚本都用它 |
| `GET /skills` | 列装了什么、每个有哪些工具 |
| `POST /skills/call` | 调工具：`{tool, args, ctx}` |
| `POST /reload` | 重载技能，**不用重启大脑** |

安装和更新分为静态检查、文件提交、Host reload、运行状态确认四步；文件写入成功
不代表 Skill 已可调用。显式 reload 如有 Skill 无法加载，会返回失败状态并保留当前
Host 注册表，Brain 会回滚文件和本次修改的渠道配置。回滚失败时返回恢复目录供人工处理。
更新和卸载会标记 `restart_needed`，因为 `setup()` / `register()` 可能启动线程、网络请求
或其他外部副作用，Host 没有通用 teardown 契约，文件回滚不能撤销这些行为。
这些事务处理的是受控错误，不提供进程崩溃后的自动恢复或跨进程锁。

🔴 **宿主只允许绑定 127.0.0.1 或 ::1，端口别开公网。** 所有路由都要求
`Authorization: Bearer <ZX_SKILL_AUTH_TOKEN>`；缺少或长度不足的运行时凭据时宿主拒绝启动。
认证只保护 Host HTTP 接口，不能替代 JavaScript 运行时隔离。要远程访问必须经过受控的
SSH 隧道或其他已审核的私有通道。

### 给模型的两个入口

```
skill_list()                → 现在有哪些技能工具
skill_call(name, args)      → 调其中一个，args 是 JSON 字符串
```

🔴 **刻意不把每个技能名注册成独立工具。** `TOOLS` 是 `plugin_host` **构造时快照**
（`plugin_host.py` 第 151 行），而 Node 宿主可能比大脑晚起几十秒——那时抓到的
技能表是空的，于是模型一整天都看不到技能，而面板上明明列得出来。固定入口 +
动态列举绕开了这个时序问题。

## 几个踩过的坑

### 1. 档位必须传**数字**，传字符串等于闸门全失效

技能侧判「群里有没有被点名」读的是 `ctx.session.contextTier`，**数字** 1=被@、
2=随便聊。而 `daily-fortune` 的作者为此写了条兜底：

```js
if (!Number.isFinite(tier)) return ''   // 宿主没给档位 → 不拦
```

我第一版传的是 `{tier: 'group'}` 这种字符串 → `Number('group')` = NaN →
走进那条兜底 → **群里没被点名照样给签**。实测判据直接挂了才发现。

传错格式的代价不是"拦错"，是"完全不拦"。宿主侧 `tierOf()` 认不出来时
按 2（群里随便聊）算，宁可严。

### 2. `console.log` 不认 `%s`

多参数转发给 `console.log` 会打成字面量 `装载 %s 失败：%s`。
自己拼字符串，见文件里的 `fmt()`。

### 3. 技能包常多一层同名目录

`daily-fortune/daily-fortune/index.js` —— 打包时 `zip -r daily-fortune daily-fortune/`
留下的，几乎人人都这么打。`resolveSkillDir()` 会往下钻一层，
但**只在恰好只有一个子目录时**钻：多个子目录说明是在放多个技能，钻进去会装错。

### 4. `import()` 会缓存模块

所有 Host 接口均要求 `Authorization: Bearer <ZX_SKILL_AUTH_TOKEN>`。
Brain 与 Node Host 必须由服务管理器或操作系统凭据环境注入同一个至少 32 字符的随机值；
不要将真实值写入 `config.json`、批处理文件或仓库。缺少凭据或配置公开监听地址时，
Host 会拒绝启动。Windows `start_host.bat` 也会在缺少凭据时退出。

`ctx` 中的 `src`、`tier` 等字段只是 Skill 兼容上下文，不是 Host 认证凭据。
渠道过滤仍由 Python Bridge 执行；Host Bearer 认证保护本机 HTTP 接口，不能隔离已加载
JavaScript。Skill 仍在当前 Node 进程用户权限下执行。

`/reload` 不加时间戳 query 的话，重载只是把同一个旧模块对象再拿出来一次——
表现为"重载了但改了代码没生效"。

### 5. 变量名别复用 `name`

踩过：`call(name, ...)` 里面为拿她的名字写了 `name = "知夏"`，
直接把传进来的技能工具名冲掉。症状是"调什么工具都报『没有这个技能工具：知夏』"。

### 6. 静默降级要留痕

`host_alive()` 原来 `except: return False`。结果一个 `NameError`（import 写漏了）
被它掩盖成"宿主没起来"，排查方向完全错。现在会把异常打出来，并区分
「代码有问题」和「连不上」。

## 现状与限制

**能在微信群里算，但不能自己发消息。** 知夏目前没有"往微信群发一条消息、
不问模型"的接口：`chat(proactive=True)` 会剥"说 "前缀、还把记忆摘要走一遍；
`Turn.say` 只往 App 推通知。所以技能在群里出的是**数据**，由模型自己用
她的话说出来——这也是 `TOOL_HELP` 里说"拿结果用你自己的话重写"的原因，
不是客套。

要让她自己发，需要给大脑加一个"原样投递"接口，那会牵动渠道开关和刷屏抑制，
不在这一层顺手做。

**配图功能没有。** `daily-fortune` 的配图靠调用隔壁 `pixiv-search` 技能的
`py/cli.py`，那个技能没装，所以走纯文字（技能自己的降级分支）。

## 已验证

`brain/_test_skill_host.py`，本机 9 项判据全过：

- 档位四种都对：群里没点名被拒 / 被@放行 / 私聊放行 / 拍一拍放行
- 换人设换昵称也认得（`@麦麦` 这种）
- 同一天同一人结果固定、换人不同、看明天不同
- 坏 JSON、未知工具、宿主没起来都给人话而不是崩

云端还需要装 Node.js（宿主依赖它），没装之前整套不生效。
