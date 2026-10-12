# Creator Mode MVP-1 报告：自然语言创作草稿与静态验证

- 分支：`creator-mode-mvp-1`（独立开发分支，**未合并 master、未推送 GitHub**）
- 基线：`45f847353ddc38f429457f02eedc5f06ab7809a3`（P4 收官点，28/28 + 35/35）
- 提交：`a176ab1eac841a42b58cd265eb8ba23f2a5dd8ee`
- 时间：2026-10-13

---

## 0. 先说产品定位的修正

初版把「创作模式」做进了「能力与扩展」组里当第 035 个页面。这不是用户要的东西。
用户要的是：**创作在主页对话里，是对话的一个选项 —— 直接跟 Agent 说话就能做插件**。

所以整个入口被重做成：主页 composer 上一个「创作」开关（关 / 插件 / 技能），
打开后**同一个输入框**里描述需求、回车，回来的是一张**草稿卡气泡**，
就长在聊天流里，和她的回复在同一个位置。没有独立页面，没有二级导航。

---

## 1. 交付物①：一条真实可演示的创作流程

在 Electron 里（隔离 HOME + 假模型 fixture + 随机 token + 非生产端口）实测通过：

1. 打开主页对话 → composer 右下角有「创作：关」chip
2. 点一下 → 变「创作：插件」，输入框上方出现创作模式条（插件 / 技能 两个类型 chip），
   placeholder 变成「描述你想要的插件。例如：一个每小时整点提醒我喝水的插件…」
3. 在输入框写需求，回车
4. 立刻在消息流里插入一条「正在调用模型生成插件草稿…」的占位气泡
5. 模型返回后，占位气泡原地替换成**草稿卡**：
   - 类型标签、用途摘要、草稿 id、版本号、使用的模型名
   - **风险与限制**
   - **静态验证**徽章（通过 / 未通过 N）+ 具体错误码与消息
   - 每个生成文件的完整代码（带语法高亮 + 复制按钮）
   - 「修改草稿」输入框 → 提交后生成 v2，旧版本进 `.history/`
   - 按钮：重新验证 / 删除草稿。**没有安装按钮**
6. 右栏出现「我的草稿 N」卡片，点任一条会在聊天流末尾再追加一张草稿卡，可继续看/改

演示证据：`docs/creator-mode-mvp-1/ev-45f8473/results.json`（20/20 ok），
截图 9 张（01 主页 → 09 草稿面板重开）按惯例**不入库**（`*.png` 已全局忽略）。

### 真实性的两条硬证据
- **不是关键词假装生成**：草稿内容由 `model_providers.get_provider(cfg).chat()`
  真实产生。演示 harness 用假模型 fixture 计数，每生成/修改一次请求数 +1
  （C2b: 0→1，C4: 第 2 次请求且修订标记出现在代码里）。
- **草稿不进正式目录**：C2d 断言草稿落在 `data/creator_drafts/<id>/`，
  不在 `plugins/` 下，也不被装载。

---

## 2. 交付物②：修改文件与 commit SHA

| 文件 | 变化 | 说明 |
|---|---|---|
| `brain/creator.py` | 新增 548 | 核心模块：提示词契约、模型调用、文件名安全闸、落盘、版本历史、静态验证 |
| `brain/server.py` | +90 | `/api/creator/{drafts,draft,revise,validate,delete}` 五个端点 + 管理员闸 |
| `brain/tests/test_creator_mode.py` | 新增 525 | 20 项测试（单元 8 + HTTP 12） |
| `client/src/js/pages.js` | +262 | composer 创作开关、草稿卡、右栏草稿列表、消息流草稿卡保活 |
| `client/src/chat.css` | +6 | `.creatorbar` 与 `#creatorBtn` 样式 |
| `client/src/styles.css` | +12 | `.cr-code` 代码展示（复用聊天页 `.codeblock`） |
| `docs/creator-mode-mvp-1/harness-creator-demo.js` | 新增 606 | Electron 界面演示 harness |
| `docs/creator-mode-mvp-1/ev-45f8473/results.json` | 新增 123 | 演示结果（20/20） |

**commit：`a176ab1eac841a42b58cd265eb8ba23f2a5dd8ee`**（8 files changed, 2172 insertions）

### 关键设计（踩过的坑都写进注释了）
- **文件名安全闸在写盘之前**：`..`、绝对路径、反斜杠、控制字符、Windows 保留名、
  契约外扩展名、超过三层的路径，全部拒绝。模型输出不可信，先校验再落盘。
- **历史版本放草稿目录外**：`.history/<id>/v<N>/`。放在 `<id>/history/` 里会被
  P4 插件检查器判 `nested_directory`，草稿就永远验证不过。
- **快照顺序**：先拷旧版再写新文件。反了会把新内容当"旧版本"存起来。
- **TDZ**：`applyCreatorMode()` 的立即调用必须在 `let creatorMode` 声明之后，
  否则整个聊天页画不出来（"Cannot access before initialization"）。
- **`pick()` 拆包**：`API.post/get` 返回 `{ok,status,json}`，业务体在 `r.json` 里。
  直接读 `r.draft` 会永远拿到"生成失败：未知错误"——这个坑让第一版演示全线飘红。
- **草稿卡保活**：消息流按历史条数变化重绘，会把不在历史里的草稿卡冲掉。
  重绘前摘出 `.crturn`，画完按原顺序贴回末尾。

---

## 3. 交付物③：Electron 界面演示结果

`docs/creator-mode-mvp-1/harness-creator-demo.js` —— 起假模型 → 起隔离 Brain 子进程
→ 起真 Electron（临时 profile、unset `ELECTRON_RUN_AS_NODE`、CDP 连接）→ 走完整 UI。

**20/20 全绿**（`results.json`）：

| 组 | 用例 |
|---|---|
| 环境 | D0 Brain 就绪且草稿接口可鉴权 / D1 Electron 连上隔离 Brain / D3 全流程无渲染层报错 |
| 入口 | C1 创作开关默认关 / C1b 切插件：提示条出现、placeholder 变、类型 chip 高亮 / C2a 需求填进同一输入框 |
| 插件创作 | C2 草稿卡（通过徽章 + 2 个文件真实代码）/ C2b 真的走了模型（fixture 计数 +1）/ C2c 带安全声明 / C2d 落独立工作区未进 plugins/ |
| 迭代 | C3 重新验证 / C4 修改升 v2 且模型再被调用 / C4b 旧版在 `.history/<id>/v1`、草稿目录内无 history |
| 技能 | C5 技能草稿卡 index.js + skill.json，验证通过，类型标「技能」 |
| 失败面 | C6 坏代码 → 验证未通过且错误码具体（`python_syntax`）/ C7 模型失败 → 消息流明说且草稿数不变 |
| 列表 | C8 右栏草稿列表有条目 / C8b 点条目 → 对话流追加草稿卡 / C9 删除生效（卡移除 + 列表 -1） |
| 回归 | C10 新历史消息到达后草稿卡仍在消息流里 |

**单元测试**：`brain/tests/test_creator_mode.py` 20/20。
**全量回归**：157 项 collected，creator 20 全过；剩余 5 FAILED + 1 ERROR
（Windows 保留名 ×3、symlink 权限 ×2、tempfile 清理）与 P4 记录的基线**逐条同名**，
与本次改动无关。

---

## 4. 交付物④：已实现 / 未实现边界

### 已实现
- 自然语言需求 → **真实模型生成**代码（插件 Python / 技能 Node 两种）
- 草稿落独立工作区 `<DATA_DIR>/creator_drafts/<id>/`，不碰正式插件/技能目录、不装载
- 展示：生成文件、完整代码、用途、风险与限制、静态验证结果（含具体错误码）
- 迭代：提交修改意见 → 重新生成 → 版本号 +1，旧版本进 `.history/`
- 重新验证 / 删除草稿
- 模型失败、非法代码、非 JSON 输出、路径逃逸：都明说，且**不落地**
- 服务端权限：POST 只管理员，GET 对已认证身份只读
- 右栏「我的草稿」回看入口

### 未实现（MVP-1 明确不做）
- **没有安装**。草稿卡上没有任何安装/启用按钮 —— 这是刻意的。
- 静态验证**只查结构与 Manifest**（复用 P4 的 `plugin_structure` /
  `skill_structure`：只读文件 + `ast.parse` / `json.load`，**不 import、不执行**）。
  **通过 ≠ 沙箱安全**，卡片上原话写死，不对外宣称等价。
- 不自动执行生成的 Python/JavaScript，不自动安装第三方依赖。
- 不做运行时沙箱、不做资源配额、不做行为审计。
- 不接管 P3 审批策略（本次没碰任何审批代码）。
- 没有能力市场/分享/版本对比 UI。

---

## 5. 交付物⑤：MVP-2「人工审核安装」所需的最小改造

目标：让一张审核过的草稿，走**已有的**安装事务进正式目录，全程留痕、不留旁门。

### 服务端（`brain/`）
1. **草稿指纹**：`creator.py` 增加 `hash_draft(id)` —— 对每个文件算 sha256，
   汇总成一个 `snapshot_hash` 存进 `meta.json`。审核批的是这个 hash，
   不是"当前磁盘内容"。
2. **新端点 `POST /api/creator/install`**（唯一新增写口）：
   - 入参必须带 `{id, snapshot_hash, confirm:true, reason}`；
   - 先重算 hash，**与请求里的 hash 不一致就拒**（草稿在审核期间被改过）；
   - 再跑一次静态验证（防止"改回坏代码再装"）；
   - 通过后调 **P4 已有的事务入口**（`plugin_transactions` / `skill_static_install`），
     不新写第二套装逻辑；
   - 落一条审核记录：谁批的、批的时间、批准的代码 hash、reason。
3. **权限挂到已有的 M4 clearance**：把 `creator.install` 归到 **risky 档**，
   走 P3 审批策略。不改审批策略本身，只加一个被管对象。
4. **回滚复用**：安装事务已有 savepoint/回滚，草稿安装直接吃，不另做。

### 客户端（`client/src/js/pages.js`）
5. 草稿卡加一个「提交安装审核」按钮 —— **且只在静态验证通过时可用**。
6. 点它弹确认气泡（复用已有三档里的 **dangerous**）：列出将要写入的文件清单、
   每个文件的 sha256、**逐字写清楚"安装后会被装载并执行"**，
   要求用户手输确认（不是点一下就过）。
7. 提交后展示审核状态（待审批 / 已批准 / 已拒绝 + 审批人和时间），不做轮询风暴。

### 明确不做的
- 不因为"用户点了确认"就跳过 clearance；
- 不做"信任这个作者以后都自动装"；
- 不在安装前自动 `pip install` / `npm install`；缺依赖要作为风险项说出来，让人自己决定。

---

## 附：怎么复现

```bash
# 单元测试
cd brain && python -m pytest tests/test_creator_mode.py -q     # 20 passed

# 全量（5F/1E 为 Windows 基线，与本次无关）
cd brain && python -m pytest tests/ -q

# 界面演示（截图到 docs/creator-mode-mvp-1/ev-<commit>/）
node docs/creator-mode-mvp-1/harness-creator-demo.js
```
