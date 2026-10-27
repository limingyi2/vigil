# 专家包格式

一个专家就是一个目录。目录即清单，不需要注册表 —— 建好目录、重启或重载即可在
「人格与语气 → 专家」里看到，点「召唤」会开一个新对话并换上这套工作方式。

```
config/experts/<key>/        ← 你手写的
data/installed_experts/<key>/ ← 从链接装的
├── expert.json          必填。元数据
├── SKILL.md             必填。专家提示词正文（角色 + 工作方式 + 纪律）
├── skills/              可选。**真的带工具**：skills/<id>/{index.js, skill.json}
├── references/          可选。资源包：参考资料，召唤时只列清单
├── assets/              可选。同上（目录名换成 assets/resources 也认）
└── <任意文件>            可选。用 expert.json 的 files 显式声明
```

两个根目录的区别只有一条：**`config/experts` 优先**。同名 key 两边都有时用你
手写的那份，远程装的包被升级也不会顶掉你改过的提示词；卸载只会删
`data/installed_experts` 里带安装记录的那些，从不碰你的配置。

`<key>` 就是目录名，也是 `/api/expert/summon` 的 `key`。只允许字母数字和
`-` `_`，不能带 `/ \ :`。

## expert.json

```jsonc
{
  "name": "写作专家",                    // 面板显示名
  "description": "一句话说明这个模式在干什么",
  "scenarios": ["写作", "文案", "润色"],  // 输入框实时推荐的关键词，包含匹配
  "icon": "book",                       // 可选。界面徽标里的线稿图标名，见下
  "default_init_prompt": "帮我写/改一段文字，用途是：",  // 召唤后的开场白
  "tools": ["local_file_read"],         // 可选。工具白名单，只能收窄不能放开
  "skills": ["data-clean"],             // 可选。要带的技能，见下
  "files": ["templates/weekly.md"]      // 可选。显式声明的资源文件
}
```

- `icon` 是界面右上角那枚方形徽标里的**线稿图标**，取值是前端 sprite 里的
  symbol 名（`term` / `book` / `brain` / `folder` / `chart` / `search` …）。
  不写或写了个不认识的，会自动退化成名字首字（「写作专家」→「写」），
  所以任何专家包都不会出现空白方块。
- **不要用 `avatar` 放 emoji**。那个字段还在（老包不炸），但界面已经不渲染了：
  彩色 emoji 和全站的单色线性图标是两种视觉语言，一排放着像贴了几张彩色贴纸，
  而且字号、基线都不受控，中文名字旁边忽大忽小。

- `tools` **不写或写空数组 = 不收窄**（保留当前全部工具）。写了列表就只剩列表里的，
  且永远放不出原本没有的工具 —— 专家是换装，不是提权。
- `skills` 支持两种写法：`"data-clean"` 或
  `{"id": "data-clean", "name": "数据清洗", "desc": "把 CSV 里的脏值统一掉"}`。
  对象写法更推荐：模型看到的是能读懂的名字，不是一串 slug。
- `files` 只在资源文件放在 `references/` `assets/` `resources/` 之外时才需要写。

## 资源包怎么生效

召唤后，专家的 `references/` `assets/` `resources/` 下所有文件会被**列进 system
段**，带相对路径、大小和绝对路径。模型看到的是清单，需要时自己用
`local_file_read` / `local_file_grep` 去读。

只列清单是有意的：一份 30KB 的样稿塞进 system 段，等于每轮对话都重烧一遍上下文，
而且模型并不会因此更准 —— 它真正需要知道的是「有这份文件、在哪」。

## 技能：声明 vs 真的带

这两件事长得很像，但一个是**依赖**，一个是**能力**，别混：

| 写法 | 效果 | 用在哪 |
|---|---|---|
| `"skills": ["data-clean"]` | 只在 system 段里写一行"我要用 data-clean，先 `skill_read` 看用法"。技能得别处先装好 | 依赖一个现成技能 |
| `skills/<id>/index.js` | 安装时把整包复制进技能宿主，reload 后模型**手上真的多出工具** | 这个专家专用的工具 |

### 带工具怎么写

```
skills/writing-tools/
├── index.js      必填。导出 setup(api)，用 api.registerTool 注册
└── skill.json    可选。{id, name, description, version}
```

`index.js` 就是普通的 JS 技能包（跟技能市场那套是同一个契约）：

```js
function setup(api) {
  api.registerTool({
    id: 'count',                       // 工具全名 = writing-tools__count
    name: '字数统计',
    description: '中文按字、英文按词，另给标点与段落数。',
    parameters: { type: 'object', properties: { text: { type: 'string' } },
                  required: ['text'] },
    async execute(ctx, args) { return { ok: true, total: 42 }; },
  });
}
module.exports = { setup };
```

两个细节：

- **工具全名是 `<技能 id>__<工具 id>`**，所以技能 id 要够特别（`writing-tools`
  好过 `tools`），否则容易跟用户自己装的撞名。撞名时专家不会抢占 ——
  已存在的同名技能会被跳过，并在安装结果里说明理由。
- **只有召唤了这个专家的会话看得见这些工具**。安装时会在技能目录写一个
  `.expert-owner`，工具表按它过滤；执行层还有第二道同样的判定，所以模型
  照着历史里的名字硬调也调不通。

## 怎么装进来

三种来源，走同一套校验（路径穿越、后缀白名单、体积上限、`expert.json` +
`SKILL.md` 必须齐）：

```python
POST /api/expert/install {"source": "owner/repo"}          # GitHub 仓库
POST /api/expert/install {"source": "https://…/x.zip"}     # 直链
POST /api/expert/install {"source": "D:/packs/writer.zip"} # 本地 zip（开发用）
POST /api/expert/uninstall {"key": "writer"}
```

GitHub 那种去 `codeload` 拉整棵树，外层那层 `repo-main/` 会自动剥掉，仓库
长这样就行：

```
vigil-experts/            ← 仓库根
├── writer/               ← 一个专家包
│   ├── expert.json
│   ├── SKILL.md
│   └── skills/writing-tools/{index.js, skill.json}
└── coder/
    └── …
```

一个仓库里有多个专家时用 `key` 指定装哪个：`{"source":"owner/repo","key":"writer"}`。
不指定就拿最浅的那个 `expert.json` 所在目录的目录名当 key。

包内**不允许** `.py` `.exe` `.dll` `.sh` `.bat` —— 专家带的是提示词、资料和
JS 技能，需要跑代码就写成技能让宿主去跑。这不是防用户，是让"装一个别人的
专家包"这件事的**最坏后果**止于"多了几段提示词"。

## 绑定是会话级的

召唤一次 = 新建一个对话 + 在这个对话上记下专家 key（`conversations.expert`）。
两个对话可以各用一个专家，互不影响；同一个对话换绑专家会保留对话历史
（专家是模式不是人，上一句说过的话下一句还能引用）。
删掉对话，绑定跟着消失。
