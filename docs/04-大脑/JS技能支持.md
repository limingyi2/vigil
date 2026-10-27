# JavaScript 技能

JS 技能由独立的 Node Skill Host 执行，Brain 通过核心 `skill` 适配器发现和调用工具。Python 插件、JS 技能与指令型 `SKILL.md` 使用不同的安装和执行路径。

## 目录与模板

最小模板见 [templates/skill-basic](../../templates/skill-basic/README.md)。入口导出 `setup(api)`，通过 `api.registerTool` 注册工具：

```javascript
export function setup(api) {
  api.registerTool({
    id: 'echo',
    name: 'Echo',
    description: 'Return the supplied text.',
    parameters: { type: 'object', properties: { text: { type: 'string' } } },
    async execute(ctx, args) {
      return { content: String((args && args.text) || '') };
    },
  });
}
```

打包前运行结构检查：

```powershell
python tools/check_skill_structure.py templates/skill-basic
```

`skill.json` 可省略以兼容旧 `index.js` 包。提供清单时，`id` 必须与包目录一致，`entry` 必须位于根目录内。单层入口支持 `index.js`、`index.mjs` 和 `index.cjs`；后两种需要明确指定入口。同名双层目录的自动下钻只适用于现有 `index.js` 兼容路径。

结构检查会拒绝路径越界、符号链接、特殊文件、大小写冲突和非法 Windows 名称，但不执行技能代码，也不证明其行为安全。

## 宿主

主要实现为 `brain/skill_host/server.js`，Brain 侧适配器为 `brain/core_features/skill.py`。托管运行时由启动流程提供宿主连接与认证信息，不应把端口或凭据写死到插件中。

| 路由 | 用途 |
| --- | --- |
| `GET /health` | 健康状态 |
| `GET /skills` | 技能与工具清单 |
| `POST /skills/call` | 调用工具，传入 `tool`、`args` 和 `ctx` |
| `POST /reload` | 请求重载 |

宿主只允许绑定回环地址，接口要求认证。不要将宿主端口暴露到公网。

## 安装与更新

安装流程依次执行静态检查、文件提交、Host reload 和运行状态核验。文件写入完成不表示技能已经可调用；宿主离线或装后核验失败时，应显示实际错误状态。

重载失败时保留当前宿主注册表，并由 Brain 尝试回滚文件和相关配置。若回滚失败，回执保留恢复目录供人工处理。

`setup()` 可能启动外部资源，宿主没有通用 teardown 契约。文件回滚不能撤销已经发出的网络请求或其他外部副作用。更新与卸载可能需要重启，状态以 `restart_needed` 等回执为准。

## 指令型 Skill

`SKILL.md` 指令包由外部应用接入模块导入，包含指南与配套资源，不会自动成为 Node 工具，也不会自动执行其中脚本。安装、读取和执行范围见 [外部应用接入](../local-integrations-and-patches.md)。
