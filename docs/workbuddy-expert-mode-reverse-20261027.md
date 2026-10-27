# WorkBuddy 专家模式 · 逆向记录

- 版本：WorkBuddy 安装版（`D:\workbuddy`，`resources\app.asar` 解包）
- 时间：2026-10-27
- 方法：解包 app.asar（`@electron/asar`）、读用户目录缓存与 SQLite、静态检索主进程/main 与 CLI 打包产物、比对提示词模板
- 结论全部有落点文件，行号/偏移见文末「证据索引」

---

## 0. 五句话结论

1. **专家是「插件里的一个 agent 定义」**，不是独立进程、不是模型、也不是人设：形态是 `plugins/<plugin>/agents/<name>.md`（或 `experts/<key>/SKILL.md`），正文就是提示词。
2. **资产来自云端市场**：`expert_center.json` 托管在 COS，本地缓存 448 个专家 / 15 个分类，其中 394 个 `agent`、54 个 `team`（专家团，带 `members[]`）。
3. **绑定在会话级**：`sessions` 表有 `expert_id / expert_marketplace / expert_runtime_identity / expert_locale`，外加 `last_user_prompt_expert_selection`——专家跟着某一条会话走，不是全局开关。
4. **生效=换模板 + 塞提示 + 定工具**：模式选 `expert` 系列模板，`{{ PluginAgentPrompt }}` 占位符被替换成专家 md 正文（含 `${CODEBUDDY_PLUGIN_ROOT}` 路径展开与超长截断），工具集取「模式固定清单 ∪ agent 自己声明的 tools」。
5. **每轮还有一条守卫提醒**：`<current-expert>` 段告诉模型"遵循该专家角色"，并**禁止**再调用专家推荐（`recommend-experts` / `suggest_plugin_install` 的 expert 分组），避免专家态里再推销专家。

---

## 1. 资产层：专家从哪来

### 1.1 云端清单

```
https://acc-1258344699.cos.accelerate.myqcloud.com/workbuddy/expert-marketplace/expert_center.json
```

本地缓存 `~/.workbuddy/app/cache/experts/`：

| 文件 | 作用 |
|---|---|
| `manifest.json` | 全量清单（448 experts / 15 categories / avatars 路径前缀） |
| `metadata.json` | `sourceSignature`（baseUrl+manifestPath）、`manifestHash`、`cacheFormatVersion` |
| `version.txt` | 清单版本，用来判断要不要重下 |

条目字段（并集）：

```
id, categoryId, displayName{zh,en}, profession{zh,en}, description{zh,en},
promptFile, avatar, defaultInitPrompt{zh,en}, quickPrompts[{zh,en}...],
expertType: agent | team, agentName, plugin, tags[{zh,en}], skills[],
members[], author, isOPC, visibility, version, createdAt, updatedAt, displayPosition
```

两个值得注意的设计：

- **双语内联**：所有面向用户的文案都是 `{zh,en}`，渲染时按 locale 取，没有单独的 i18n 表。
- **`promptFile` 是插件内相对路径**：`/plugins/<plugin>/agents/<name>.md`；448 条里 447 条有值，说明绝大多数专家就是"一个 md 文件"。

### 1.2 本地包形态（两种）

内置插件 `resources/plugins/workbuddy-builtin/builtin-plugins/workbuddy-docx/` 同时给了两种样本：

```
agents/doc-formatter.md        # agent 型：YAML frontmatter(name/description/category/version/spec/tags) + 正文
experts/general-writer/
  ├── SKILL.md                 # skill 型：frontmatter(name/description) + <role>/<workflow>
  ├── references/              # 按需读取的资料
  ├── scripts/
  └── assets/
```

- agent 型的 frontmatter 可以声明 `tools:`（逗号分隔，如 `workbuddy-sheetagent` 的 sheet-agent 就把工具收窄到 Read/WebSearch/mcp__sheetagent__* 这几个）。
- skill 型更像 Claude Skill：正文是工作流，靠 `references/` 分文件按需读，避免一次性把大段资料塞进上下文。
- 专家还可以声明 `skills: ["./skills/ipo-workflow", ...]`（清单里有实例），即专家自带技能包。

### 1.3 专家团

`expertType: "team"` 的条目多一个 `members[]`（lead + 若干 specialist，各自有 displayName/profession/avatar），`promptFile` 指向 lead 的 md。也就是说团队不是"多个 agent 并行"，而是**一个 lead 提示词 + 成员清单**，由 lead 在对话里决定派活。

---

## 2. 安装层：专家怎么落到本地

- 专家被当成**插件**安装：落在 `~/.workbuddy/plugins/marketplaces/<marketplace>/<dirName>/`，自定义专家在 `my-experts`（目录名带 `ex_` 前缀）。
- 主进程侧有一层 "expert runtime preparer"：
  - `inspectLandedBundle()` 检查本地是否已有完整包；
  - 不完整就 `activateExpert()` 下载/落盘（官方源按 plugin 名查下载地址）；
  - 再 `getExpertManifestFromMarketplace()` 拿 `plugin name / agentName / expertType`。
- 自建专家是**云资产**：渲染层 `expert-picker` 里能看到 `AssetCloudUpload / AssetCloudInstall / AssetCloudSync / AssetCloudDelete` 四类埋点，同步策略按 `version` 或 `mtime` 决定方向，删除分 `local_uninstall` 与 `cloud_delete`。
- 本地还有两份"使用痕迹"：`~/.workbuddy/app/data/expert-history.json`（按 session 记）、`~/.workbuddy/storage/<user>/expert.json`（`recents`）。

---

## 3. 绑定层：专家挂在哪

SQLite `~/.workbuddy/workbuddy.db` → `sessions` 表：

```
mode                          -- craft / ask / plan / expert / quick
expert_id                     -- 当前会话绑定的专家
expert_marketplace            -- 来自哪个市场（experts / my-experts / ...）
expert_runtime_identity       -- 运行时身份快照（插件名+agent 名等）
expert_locale                 -- 选定时用的语言
last_user_prompt_expert_selection  -- 最近一次用户选择（迁移 0004 新增列）
agent_dirty / agent_dirty_at / agent_last_synced
```

关键点：**专家是会话属性，不是全局模式**。同一时刻可以一个会话在"代码专家"、另一个在"写作专家"；`last_user_prompt_expert_selection` 让新建会话能沿用上次选择。`agent_dirty/last_synced` 是给"专家包在云端更新了、本地要不要重同步"用的。

---

## 4. 运行时：一次请求里发生了什么

```
ExpertModeHandler.resolve()
  ├─ promptRenderer.renderSystemPrompt({ mode:'expert', expertId, expertMarketplace, cwd })
  ├─ tools = resolveModeTools('expert')            // 固定清单，见 §5
  ├─ permissionMode = max(请求值, expert 场景默认)  // expert 默认 bypassPermissions
  └─ MCP 有声明时追加 workbuddy_request_mcp_connection 的 Defer 包装
```

提示词侧：

1. `WorkbuddySystemPromptService.renderSystemPrompt()` → `PromptRendererImpl`，按 `mode + 任务类型` 选模板（见 §5.1）。
2. `resolveExpertPluginAgentPromptSource(expertId, marketplace, dirName)` 找到 md 的真实路径。
3. 读文件 → `materializeExpertPromptLoadTimeVariables()`：把 `${CODEBUDDY_PLUGIN_ROOT}` / `${CLAUDE_PLUGIN_ROOT}` 替换成插件根目录（所以专家 md 里可以直接写 `references/xxx.md` 这类相对资源）。
4. 超长截断：超过上限时只取前 N 字符并置 `promptTruncated: true`（列表页不读正文，`include_prompt=false` 省一次 IO）。
5. 填入模板的 `{{ PluginAgentPrompt }}`。

"prepared facts" 结构（主进程算好再交给运行时）：

```
{ fingerprint, canonical:{id, marketplace, runtimeIdentity},
  pluginName, agentName, expertType, displayName,
  promptText, promptFilePath, promptTruncated }
```

---

## 5. 提示词层：模板怎么换

### 5.1 模板映射

```
normal        → workbuddy-prompt.tpl
ask           → workbuddy-ask-prompt.tpl
expert        → workbuddy-expert-prompt.tpl
quick         → workbuddy-quick-prompt.tpl
normal-code   → workbuddy-craft-code-prompt.tpl
expert-code   → workbuddy-expert-code-prompt.tpl
expert-work   → workbuddy-expert-prompt.tpl
expert-design → workbuddy-craft-design-prompt.tpl
user-context-expert → user-context-expert-identity.tpl
```

即：**expert 是第五种模式**，并且会按任务类型（code / work / design）继续分叉；design 直接复用 craft 的设计模板。

### 5.2 专家模板和普通模板的差异（实测 40 行 diff）

| 普通模板 | 专家模板 |
|---|---|
| 开头的"You're good at research/writing/data/building…"能力介绍 + "Experts: 100+ 领域专家"导流 | **整段被 `{{ PluginAgentPrompt }}` 替换** |
| `<regional_conventions>`（涨红跌绿、¥ 等） | 去掉 |
| — | 新增 `<communication>`：语气要自然、可幽默、别只把自己当代码生成器 |
| — | 新增 `<agentic_mode_overview>`：强制把成果写成 `overview.md` 放进 artifact 目录 |
| `<content_policy>`、`<personal_files_safety>`、`<working_modes>`、`<tool_use>`、`<agent_skills>` 等 | 原样保留 |

结论：专家**不碰安全底线，只换"我是谁 + 怎么干"**，同时把"能力自述"让位给专家自己的角色设定。

### 5.3 每轮的守卫提醒

每轮（`stage: every_turn`）注入：

```
<current-expert>
当前会话已选择 Expert「X」专家。请遵循该 Expert 的角色与工作流。
禁止读取 recommend-experts，也不要调用 suggest_plugin_install 提交 expert 分组；
Connector 推荐可正常进行。
</current-expert>
```

`expert_team` / `team` 的标签是"专家团"，其余 `expert/skill/agent/plugin` 统一显示"专家"。

---

## 6. 工具层：专家能碰什么

- 模式级固定清单（`interactionmode-expert/fragments/interaction.md` + 代码里的 `Za.expert`）：等同 craft 全量工具集**再加** `EnterPlanMode / ExitPlanMode`（专家可以进计划态）。
- agent 级 `tools:` 声明会与模式清单 **union**（`unionTools(agentConfig.tools, fragment.tools, modeFragment.tools)`）。技巧在于：union 的语义是"只能在这个集合里增加声明"，所以专家能进一步收窄，但模式清单里没有的工具不会被加回来——真正放权仍在权限档位那一层。
- 权限档位：`expert` 场景默认 `bypassPermissions`，且**不允许比场景默认更弱**（请求成 `default/acceptEdits` 会被忽略，回落到默认）。这条是"专家=干活模式"的直接体现。
- MCP：检测到插件声明了 MCP，会注入 `Defer(workbuddy_request_mcp_connection)` 引导工具，让模型先申请连接再用。

---

## 7. 推荐层：怎么知道该召唤谁

- 内置 `resources/builtin-expert-recommendations/curated-experts.json`：60 条候选（50 expert + 10 team），每条 `{id, type, scenarios[], description, priority}`，优先级 300 → 5。纯静态、零成本，和输入做关键词命中即可排序。
- 界面上是「专家中心」：左侧栏入口 → 分类浏览（15 类，含腾讯专区、开学季这类运营位）→ 专家卡（头像/职业/简介/快捷提问）→ 召唤。
- 一旦会话里已经有专家，推荐链路被 §5.3 的提醒关掉。

---

## 8. 和 Vigil 现有实现的对照

| 维度 | WorkBuddy | Vigil（`codex/expert-release`） |
|---|---|---|
| 包形态 | 插件内 `agents/*.md` 或 `experts/*/SKILL.md` | `config/experts/<key>/expert.json + SKILL.md` |
| 来源 | 云端市场 448 个 + 自建云资产 | 内置 2 个，放目录即生效 |
| 安装 | 下载落盘、版本/mtime 同步、云删/本地卸 | 无安装链路 |
| 绑定 | 会话级（`sessions.expert_id`），可跨会话沿用 | 渠道级（`config.experts_by_src[src]`） |
| 提示注入 | 换模板 + 占位符替换 + 变量展开 + 截断 + 每轮提醒 | 在 system 段末尾追加 overlay |
| 工具 | 模式清单 ∪ agent `tools`，权限有下限 | `tools` 白名单只减不增 |
| 团队 | `expertType: team` + `members[]` | 无 |
| 推荐 | 静态 curated + 专家中心 | 输入关键词 `scenarios` 打分 |

---

## 9. 如果要往 Vigil 上搬，优先级建议

1. **P0 会话级绑定**：`experts_by_src` → `sessions.expert_id`，顺手加 `last_user_prompt_expert_selection`。这一步决定"一个窗口里能不能同时开两个专家"。
2. **P1 安装链路**：`/api/expert/install`（zip 或目录），复用 `skill_transaction` 的事务落盘 + 清单校验 + 大小上限；面板加「安装专家」。
3. **P2 团队与资源**：`members[]` + `references/` 目录，把大段资料从 SKILL.md 挪到按需读取的文件里——这是上下文成本的关键。
4. **P3 守卫提醒**：专家态下关掉"推荐专家"，避免自我推销。

---

## 10. 证据索引

| 结论 | 证据 |
|---|---|
| 448 专家 / 15 分类 / 394 agent + 54 team | `~/.workbuddy/app/cache/experts/manifest.json` |
| 云端清单地址与哈希 | 同目录 `metadata.json`（`sourceSignature` / `manifestHash`） |
| 会话级绑定字段 | `~/.workbuddy/workbuddy.db` → `sessions` 表；迁移 `0004_last_user_prompt_expert_selection.sql` |
| 模板映射表 | `main/tar.js`（`Wl` / `Gl` 常量，约 offset 826.8k） |
| 专家提示解析与变量展开 | `main/tar.js` `resolveExpertPluginAgentPromptSource` + `materializeExpertPromptLoadTimeVariables` |
| 截断与 prepared facts | `main/daemon-app-server-main.js` `assemblePreparedFacts` / `promptTruncated` |
| 模式工具/权限 | `main/tar.js` `Za` / `Xa` / `io`（expert: `bypassPermissions`） |
| 每轮提醒 | `main/tar.js` `CurrentExpertReminderSection` |
| 占位符替换点 | `cli/dist/codebuddy-lite-wb.mjs`（`PluginAgentPrompt` / `PE` 正则） |
| 工具 union | 同文件 `composePluginAgent` + `unionTools` |
| 专家模式工具清单 | `resources/plugins/workbuddy-builtin/interactionmode/expert/fragments/interaction.md` |
| 推荐候选 | `resources/builtin-expert-recommendations/curated-experts.json` |
| agent / skill 两种包形态 | `resources/plugins/workbuddy-builtin/builtin-plugins/workbuddy-docx/{agents,experts}` |

> 未完全确证的部分：`expert-code / expert-work / expert-design` 的具体判定来源（推测取自 welcomeMode 或任务类型探测），以及截断阈值的具体数值（常量在构建产物里被内联，未定位到明文）。
