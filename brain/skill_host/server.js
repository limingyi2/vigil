// Skill 宿主：把 QQ Agent 那套 skill 跑起来，让知夏也能用现成的 JS 技能。
//
// ## 为什么需要这一层
//
// 知夏自己的插件（brain/plugins/*.py）是纯 Python、直接 import 进大脑进程的。
// 而外面能下到的 skill 大多是 JS + skill.json，契约长这样：
//
//     export function setup(api) {
//       api.registerTool({ id, name, description, parameters, async execute(ctx,args){} })
//     }
//
// 这套契约依赖 QQ Agent 特有的一堆东西（ctx.sender.sendMedia、contextTier、
// 配置面板…），知夏一个都没有。所以本文件做的就是**垫一层**：给它一个
// api 和 ctx，让 skill 以为是自己在 QQ Agent 里跑。
//
// ## 为什么只用 node:http，零依赖
//
// 云端是 2C2G，知夏本体 + 模型 API 已经占掉大半。这里再装 express 之类纯属
// 给自己找OOM。一个只 serve 三个路由的 http server 常驻内存约 50MB，
// 裸 node 进程，不装任何 npm 包 —— 不用 npm install，也就没有依赖地狱。
//
// ## 三个路由
//
//   GET  /skills            列出装了什么、每个有哪些工具
//   POST /skills/call       调某个工具：{tool, args, ctx}
//   GET  /health            活着没（面板和部署脚本判活靠它）
//
// ## 调不到工具时的规矩
//
// 宁可回一句人话，也不要静默失败。skill 侧拿到 {content, isError:true}
// 才知道该跟模型解释什么；什么都不回的话，模型会以为调用成功了，
// 然后自己编一个运势出来 —— 那是最坏的结果。
'use strict'

const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')
const { pathToFileURL } = require('node:url')

// --- 配置：环境变量给默认值，配置文件可覆盖 ---------------------------------
const SKILL_DIR = process.env.ZX_SKILL_DIR || path.join(__dirname, 'skills')
const PORT = Number(process.env.ZX_SKILL_PORT || 8790)
const HOST = process.env.ZX_SKILL_HOST || '127.0.0.1'
const AUTH_TOKEN = String(process.env.ZX_SKILL_AUTH_TOKEN || '')
// Do not expose the bridge secret through process.env to imported Skill code.
delete process.env.ZX_SKILL_AUTH_TOKEN
// 调 skill 工具的硬超时。skill 可能去爬图（P站很慢），但知夏一轮对话
// 不能被它拖死 —— 超时后大脑那边照样能回话，只是这个工具算失败。
const CALL_TIMEOUT = Number(process.env.ZX_SKILL_TIMEOUT || 25000)

// 🔴 不用 console.log 直接转发：它**不认 %s 占位符**，
// 多参数转发过去会打成字面量 "装载 %s 失败：%s"（已知问题）。
// 自己拼字符串，顺便统一加前缀。
const log = (...a) => console.log('[skill-host]', ...a.map(x => (typeof x === 'string' ? x : String(x))))

/** 说清一个字符串：把 %s 逐个替换掉。log 只收数组形式了。 */
const fmt = (tpl, ...vals) => {
  let i = 0
  return String(tpl).replace(/%s/g, () => (i < vals.length ? String(vals[i++]) : '%s'))
}

/** 已装载的技能：{ id -> {mod, meta, tools:{toolName->def}} } */
const skills = new Map()

const WINDOWS_RESERVED = new Set([
  'CON', 'PRN', 'AUX', 'NUL',
  ...Array.from({ length: 9 }, (_, i) => `COM${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `LPT${i + 1}`),
])

function validateRuntimeConfig() {
  if (AUTH_TOKEN.length < 32) {
    throw new Error('ZX_SKILL_AUTH_TOKEN 缺失或长度不足（至少 32 个字符）')
  }
  if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) {
    throw new Error('ZX_SKILL_PORT 必须是有效端口')
  }
  // Bind only to numeric loopback addresses. Avoid localhost name resolution
  // and accidental public listeners from an inherited environment variable.
  if (HOST !== '127.0.0.1' && HOST !== '::1') {
    throw new Error('ZX_SKILL_HOST 只允许 127.0.0.1 或 ::1')
  }
}

function authorized(req) {
  const value = String(req.headers.authorization || '')
  const match = /^Bearer ([^\s]+)$/i.exec(value)
  if (!match) return false
  const got = Buffer.from(match[1], 'utf8')
  const expected = Buffer.from(AUTH_TOKEN, 'utf8')
  return got.length === expected.length && crypto.timingSafeEqual(got, expected)
}

function safeName(name) {
  if (!name || name === '.' || name === '..' || /[\x00-\x1f\x7f<>:"/\\|?*]/.test(name)) return false
  if (/[. ]$/.test(name)) return false
  const stem = name.split('.', 1)[0].replace(/[ .]+$/, '').toUpperCase()
  return !WINDOWS_RESERVED.has(stem)
}

function lstatSafe(target) {
  const st = fs.lstatSync(target)
  // On Windows Node reports directory junctions as symbolic links as well.
  if (st.isSymbolicLink()) throw new Error(`不允许符号链接或 junction：${target}`)
  return st
}

function walkSkillTree(root) {
  const rootReal = fs.realpathSync(root)
  const seen = new Map()
  const files = []
  function walk(dir) {
    for (const name of fs.readdirSync(dir).sort()) {
      if (!safeName(name)) throw new Error(`非法 Skill 文件名：${name.slice(0, 80)}`)
      const target = path.join(dir, name)
      const st = lstatSafe(target)
      const real = fs.realpathSync(target)
      const rel = path.relative(rootReal, real)
      if (rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) {
        throw new Error(`Skill 路径越界：${name}`)
      }
      const relativeName = path.relative(rootReal, target).split(path.sep).join('/')
      const folded = relativeName.toLocaleLowerCase('en-US')
      const prior = seen.get(folded)
      if (prior && prior !== relativeName) {
        throw new Error(`Skill 路径大小写冲突：${prior} / ${relativeName}`)
      }
      seen.set(folded, relativeName)
      if (st.isDirectory()) walk(target)
      else if (st.isFile()) files.push(relativeName)
      else throw new Error(`Skill 目录中存在特殊文件：${relativeName}`)
    }
  }
  walk(rootReal)
  return { rootReal, files }
}

function validateSkillDirectory(name, dir) {
  if (!safeName(name)) throw new Error(`非法 Skill 目录名：${String(name).slice(0, 80)}`)
  const topStat = lstatSafe(dir)
  if (!topStat.isDirectory()) throw new Error('Skill 根必须是普通目录')
  const outerReal = fs.realpathSync(dir)
  walkSkillTree(outerReal)
  const realDir = resolveSkillDir(outerReal)
  const real = fs.realpathSync(realDir)
  const relRoot = path.relative(outerReal, real)
  if (relRoot === '..' || relRoot.startsWith(`..${path.sep}`) || path.isAbsolute(relRoot)) {
    throw new Error('Skill 实际目录越出安装根目录')
  }
  const { files } = walkSkillTree(real)
  const metaPath = path.join(real, 'skill.json')
  let meta = {}
  if (files.includes('skill.json')) {
    try {
      meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'))
    } catch (e) {
      throw new Error(`skill.json 无效：${String(e.message || e).slice(0, 100)}`)
    }
    if (!meta || typeof meta !== 'object' || Array.isArray(meta)) {
      throw new Error('skill.json 顶层必须是对象')
    }
  }
  const entry = meta.entry === undefined ? 'index.js' : meta.entry
  if (typeof entry !== 'string' || !entry || entry.includes('\\') || entry.startsWith('/') ||
      /^[A-Za-z]:/.test(entry) || entry.split('/').some(p => !p || p === '.' || p === '..')) {
    throw new Error('Manifest entry 必须是 Skill 根目录内的相对路径')
  }
  const entryPath = path.resolve(real, ...entry.split('/'))
  const relative = path.relative(real, entryPath)
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error('Manifest entry 越出 Skill 根目录')
  }
  const entryStat = lstatSafe(entryPath)
  if (!entryStat.isFile() || !files.includes(entry.split('/').join('/'))) {
    throw new Error('Manifest entry 必须指向 Skill 根目录内的普通文件')
  }
  const entryReal = fs.realpathSync(entryPath)
  const realRelative = path.relative(real, entryReal)
  if (realRelative === '..' || realRelative.startsWith(`..${path.sep}`) || path.isAbsolute(realRelative)) {
    throw new Error('Manifest entry 实际路径越出 Skill 根目录')
  }
  // Older installed packages may intentionally expose an id different from
  // their directory name; keep that Host behavior while rejecting unsafe ids.
  if (meta.id !== undefined && (typeof meta.id !== 'string' || !safeName(meta.id))) {
    throw new Error('Manifest id 不是安全名称')
  }
  return { realDir: real, meta, entry }
}

// --- skill.json 读取 ---------------------------------------------------------

function readMeta(dir) {
  // skill.json 不在就在 index.js 旁边找 .json；都没有就当没有元数据，
  // skill 仍能跑（很多包只靠代码里的 name/description）。
  const p = path.join(dir, 'skill.json')
  if (!fs.existsSync(p)) return {}
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'))
  } catch (e) {
    log(fmt('skill.json 解析失败：%s（%s）', p, String(e.message || e).slice(0, 80)))
    return {}
  }
}

// --- 装载 -------------------------------------------------------------------

async function loadAll() {
  skills.clear()
  if (!fs.existsSync(SKILL_DIR)) {
    log(fmt('技能目录不存在：%s（还没有装技能）', SKILL_DIR))
    return
  }
  const rootStat = lstatSafe(SKILL_DIR)
  if (!rootStat.isDirectory()) throw new Error('Skill 根目录必须是普通目录')
  for (const name of fs.readdirSync(SKILL_DIR)) {
    const dir = path.join(SKILL_DIR, name)
    let st = null
    try { st = fs.statSync(dir) } catch (e) { continue }
    // 跳过非目录和下划线开头的（跟 python 插件同一套规矩，别搞两套标准）
    //
    // 🔴 .bak_ 结尾的也必须跳过，而且**后果比"多一个幽灵技能"更隐蔽**：
    // python 侧的 _uninstall 把目录改名成 `<包名>.bak_<时间戳>` 而不是真删
    // （不覆盖、不销毁，是有意的）。skills 这个 Map 是拿 manifest 里的 id
    // 做 key 的，于是备份目录和真技能**同名** → 互相覆盖 →
    // 日志里三个都"装载成功"，最后 `装好 1 个技能`。
    // 也就是说：不跳的话，日志完全正常、/skills 数量看着正常，
    // 但清单里到底是哪一份取决于目录顺序 —— 已卸载的技能可能顶掉在用的。
    if (!st.isDirectory() || name.startsWith('_') || name.startsWith('.')) continue
    if (name.includes('.bak_')) continue
    await loadOne(name, dir).catch(e =>
      log(fmt('装载 %s 失败：%s', name, String(e.message || e).slice(0, 120))))
  }
  log(fmt('装好 %s 个技能：%s', skills.size, [...skills.keys()].join(', ') || '（无）'))
}

/** 技能的真正代码目录。

为什么要有这个函数：下到的包**经常多一层同名目录** ——
`daily-fortune/daily-fortune/index.js`（外层是包名，内层才是内容）。
这是打包时`zip -r daily-fortune daily-fortune/` 留下的，几乎人人都这么打。
所以先把当前目录看一眼：没有入口文件、但**恰好只有一个子目录**时，
往下钻一层。子目录多于一个就不钻 —— 那说明这人是在放多个技能，
钻进去只会装错一个，剩下的静默丢掉。

为什么不递归到底：技能之间互相引用很常见（运势配图去调pixiv-search），
递归会无限往下钻，而且把别的技能的文件当成自己的一部分。
只钻一层，且只钻"唯一子目录"这一种情况。
 */
function resolveSkillDir(dir) {
  if (fs.existsSync(path.join(dir, 'index.js'))) return dir
  let kids = []
  try {
    kids = fs.readdirSync(dir).filter(n => {
      if (n.startsWith('.') || n.startsWith('_')) return false
      try { return fs.statSync(path.join(dir, n)).isDirectory() } catch (e) { return false }
    })
  } catch (e) { return dir }
  if (kids.length !== 1) return dir
  const inner = path.join(dir, kids[0])
  return fs.existsSync(path.join(inner, 'index.js')) ? inner : dir
}

async function loadOne(name, dir) {
  const checked = validateSkillDirectory(name, dir)
  const realDir = checked.realDir
  const meta = checked.meta
  const entry = checked.entry
  const main = path.join(realDir, entry)
  if (!fs.existsSync(main)) {
    throw new Error(fmt('找不到入口文件 %s（在 %s）', entry, realDir))
  }
  if (realDir !== dir) {
    log(fmt('%s 外面多包了一层，已进到 %s', name, path.basename(realDir)))
  }

  // 🔴 必须用 pathToFileURL 转一下再 import：Windows 上直接传 'C:\a\b.js'
  // 会被当成 URL 协议头，报 "Only file URLs are supported"。
  //
  // 🔴 还要带个时间戳当 query：**Node 的 import() 会缓存模块**。
  // 不加的话 /reload 只是把同一个旧模块对象再拿出来一次，
  // 表现为"重载了但改了代码没生效"，查起来很怀疑人生。
  // 源文件一改时间戳就变，于是拿到的是新模块。
  const bust = Date.now()
  const mod = await import(pathToFileURL(main).href + '?v=' + bust)
  // 🔴 **入口函数认两个名字：setup（本宿主契约）和 register（QQ Agent 契约）。**
  //
  // 这不是"顺手兼容一下"。QQ Agent 的 skill 一律是
  // `export function register(api)`，而这层 api 本来就是**照着 QQ Agent 垫出来的**
  // （见文件头那段）—— 也就是说这类包除了入口函数叫什么以外，全都能跑。
  // 只认 setup 的后果很不显眼：包被判"不是 skill"，而**安装接口已经报了成功**
  // （文件确实写进去了），用户看到的是"提示安装成功、列表里没有"。
  // agent-reach 遇到的就是这个：宿主日志
  // `装载 agent-reach 失败：这个包没有 setup(api)，不是 skill`。
  const boot = (typeof mod.setup === 'function') ? mod.setup
    : (typeof mod.register === 'function') ? mod.register
      : null
  if (!boot) throw new Error('这个包没有 setup(api) 也没有 register(api)，不是 skill')
  if (typeof mod.setup !== 'function') {
    log(fmt('%s 用的是 register(api)（QQ Agent 契约），按 register 装载', name))
  }

  const tools = {}
  // 垫出来的 api：形状照 QQ Agent 那套，够 skill 认，但每个方法都另有用处。
  const api = {
    config: () => ({ ...(meta.settings || {}) }),
    // 不加 '[' ']'：log 已经带了 '[skill-host]' 前缀，技能自己又加一层
    // 的话日志里会出现 "[[daily-fortune] ...]" 这种双括号。
    log: (...a) => log(a.map(x => (typeof x === 'string' ? x : String(x))).join(' ')),
    registerTool: (def) => {
      // 🔴 registerTool 强制要求 name：QQ Agent 的 src/tool-registry.js 会直接抛
      // 「工具 X 必须提供 name」，少了它整个 setup 中断、工具一个都注册不上。
      // 这里自己也要挡一道 —— 报错要说清是哪个包缺，否则只看到"技能装载失败"。
      if (!def || !def.name) {
        throw new Error('技能 ' + name + ' 注册了一个没有 name 的工具 id=' + (def && def.id))
      }
      // 全名 = <技能id>__<工具id>，跟 QQ Agent 保持一致，模型提示词里
      // 引用工具名时不用记两套命名规则。
      const full = `${meta.id || name}__${def.id || def.name}`
      tools[full] = { ...def, full, skill: meta.id || name }
    },
  }

  await boot(api)
  // 技能自称不可用（缺依赖等）就别收 —— 收进来等于给模型一个必然失败的工具。
  if (typeof mod.available === 'function') {
    let ok = true
    try { ok = await mod.available() } catch (e) { ok = false }
    if (!ok) { log(fmt('%s 自称不可用，跳过', name)); return }
  }
  // 🔴 记 realDir 而不是外层 dir：面板和卸装要靠它定位文件，
  // 记外层的话会指到那个空壳目录上。
  skills.set(meta.id || name, { id: meta.id || name, dir: realDir, mod, meta, tools })
  log(fmt('技能 %s 就绪，工具：%s', meta.id || name, Object.keys(tools).join(', ') || '（无）'))
}

// --- 调用 -------------------------------------------------------------------

// skill 侧要的是 QQ Agent 的 ctx.sender / ctx.session。这里把它们接到
// 知夏的大脑上：发消息就是回调大脑的 /api/chat/say，档位由 python 那边算好传进来。
// 档位：QQ Agent 用数字（1 = 被@ / 拍一拍，2+ = 群里随便聊，缺省是不给）。
// 🔴 **必须传数字，不能传 'group' / 'mentioned' 这种字符串。**
// skill 侧是 `Number(ctx.session.contextTier)`，字符串会变成 NaN，
// 而作者为此写了条兜底「宿主没给档位就不拦」——于是传字符串等于闸门全程失效：
// 群里没被点名照样给签。原 skill 的 `gate()` 就是这个组合，判据 4 由此而来。
// 传错格式的代价不是"拦错"，是"完全不拦"，所以宁可多写一个转换。
// 🔴 档位数字必须跟技能作者看到的一致，也就是 QQ Agent 的 resolveContextTier：
//   1 = 被 @ / 拍了拍我   2/3 = 群里随便聊   4 = 私聊消息
//
// 🔴 私聊必须映射成 **4**，不能是 0。技能那边只认 `tier === 1`（被点名放行）
// 和 `ctx.kind === 'private'`（私聊放行）；映射成 0 时两边都不成立，
// 私聊里调用会被当成"群里没被点名"拒掉，回复一句
// "这次在群里没被点名（档位 0）"。tier=4 传进来也照样拒。
const TIER = { private: 4, mentioned: 1, group: 2, keyword: 2, random: 3 }

/** python 传tier（字符串），转成 skill 认的数字。认不出来按 2（群里随便聊）算 ——
//  宁可当成"没被点名"拦下来，也不要当成被点名放出去。 */
function tierOf(v) {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  const s = String(v || '').trim().toLowerCase()
  if (s in TIER) return TIER[s]
  if (s === '' ) return 2
  const n = Number(s)
  return Number.isFinite(n) ? n : 2
}

function makeCtx(src) {
  const sender = {
    // 纯文字：走大脑一个专门的接口，带 token。它最终会作为一条消息发出去。
    sendTextBatch: async (chatKey, list) => {
      const text = (list || []).join('\n')
      if (!text) return { ok: false }
      const r = await fetch(src.sayUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + (src.token || '') },
        body: JSON.stringify({ text, src: src.src || 'app', persona: src.persona || '' }),
      })
      if (!r.ok) throw new Error('大脑拒收了：HTTP ' + r.status)
      return { ok: true }
    },
    // 🔴 媒体发送**故意不实现**。QQ Agent 的 sendMedia 能把图文合并成一条气泡，
    // 知夏这边没有等价通道 —— 硬凑会发成两条，气泡就裂了。
    // 不实现的话 skill 会走它的纯文字兜底分支（那段写得很清楚），一条也不裂。
  }

  return {
    // 这几个字段 skill 会读，缺了会走 undefined 分支。
    // kind 和 session.contextTier 是两套判据，QQ Agent 上两个都给，
    // 这里也两个都给 —— 只给一个的话，换个 skill 换了写法就漏。
    // 🔴 这里的 4 必须跟上面 TIER 表一致。写错的话 kind 永远判成 'group'，
    // 连 kind 这条更宽松的判据也一起失效 —— 技能里
    // `if (ctx?.kind === 'private') return ''` 就再也走不到。
    kind: tierOf(src.tier) === 4 ? 'private' : 'group',
    chatKey: src.chatKey || '',
    sender,
    botName: src.botName || '知夏',
    selfNickname: src.botName || '知夏',
    session: {
      contextTier: tierOf(src.tier),
      // 知夏这边没有 QQ Agent 那套"为什么是这个档位"的解释，填了空串。
      // 它只用在拒答文案里拼接，不影响判断。
      contextReason: '',
    },
    // 🔴 消息原文必须带上，而且**必须放在最外层**。
    //
    // 起因：daily-fortune 要判「群里被点名时，他到底有没有在问运势」——
    // 「@麦麦 在吗」不该抽签，「@麦麦 今日运势」才该。这个判断只能看原文。
    // 而 makeCtx 只给了 kind / sender / session / _zx，**一个字原文都没有**，
    // 于是那个判断永远读到空串 → 等于「没问运势」→ 群里被点名也一律不给。
    //
    // 放最外层而不是塞进 sender：QQ Agent 的契约里 sender 是"人"（id、名字、
    // 发消息的方法），原文属于"这一条消息"，混进去会让换 skill 的人找不着。
    // 常见的几个键名都补上，因为不同 skill 挑的名字不一样。
    text: src.text || src.message || src.rawText || src.query || '',
    _zx: { src: src.src || 'app', persona: src.persona || '' },
  }
}

async function callTool(tool, args, src) {
  let hit = null
  for (const s of skills.values()) {
    if (s.tools[tool]) { hit = s.tools[tool]; break }
  }
  if (!hit) {
    return { ok: false, content: '没有这个技能工具：' + tool, isError: true }
  }
  try {
    const out = await Promise.race([
      Promise.resolve(hit.execute(makeCtx(src || {}), args || {})),
      new Promise((_, rej) => setTimeout(() => rej(new Error('技能超时 ' + CALL_TIMEOUT + 'ms')), CALL_TIMEOUT)),
    ])
    // 契约：返回 {content, isError?}。content 必须是字符串 —— 技能作者
    // 偶尔返回对象，而模型只吃文本，所以这里统一转字符串。
    const content = out && typeof out.content === 'string' ? out.content
      : (out === undefined || out === null ? '' : JSON.stringify(out))
    return { ok: !(out && out.isError === true), content, tool: hit.full }
  } catch (e) {
    // 失败也要有内容返回：模型看到空字符串会以为成功，然后自己编一个结果。
    const why = String((e && e.message) || e).slice(0, 200)
    log(fmt('工具 %s 报错：%s', tool, why))
    return { ok: false, content: '技能执行失败：' + why, isError: true, tool: hit.full }
  }
}

// --- HTTP -------------------------------------------------------------------

function send(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  })
  res.end(body)
}

const server = http.createServer((req, res) => {
  if (!authorized(req)) return send(res, 401, { ok: false, err: '未授权' })
  const url = new URL(req.url, 'http://localhost')
  const p = url.pathname

  if (req.method === 'GET' && p === '/health') {
    return send(res, 200, { ok: true, skills: skills.size, dir: SKILL_DIR })
  }

  if (req.method === 'GET' && p === '/skills') {
    const out = []
    for (const s of skills.values()) {
      out.push({
        id: s.id,
        name: s.meta.name || s.id,
        version: s.meta.version || '',
        description: String(s.meta.description || '').slice(0, 300),
        tools: Object.values(s.tools).map(t => ({
          name: t.full, title: t.name, desc: String(t.description || '').slice(0, 400),
          parameters: t.parameters || {},
        })),
      })
    }
    return send(res, 200, { ok: true, skills: out })
  }

  if (req.method === 'POST' && p === '/skills/call') {
    let raw = ''
    // 限长：body 超 1MB 直接掐掉，别让一个坏客户端把内存吃光
    req.on('data', (c) => {
      raw += c
      if (raw.length > 1024 * 1024) { req.destroy(); send(res, 413, { ok: false, err: 'body 太大' }) }
    })
    req.on('end', async () => {
      let body = {}
      try { body = JSON.parse(raw || '{}') } catch (e) {
        return send(res, 400, { ok: false, err: 'JSON 坏了：' + String(e.message || e).slice(0, 80) })
      }
      const out = await callTool(String(body.tool || ''), body.args, body.ctx)
      return send(res, 200, out)
    })
    return
  }

  // 🔴 装/卸技能不用重启大脑：python 侧把文件放好就打这个路由重载一次。
  // 走 HTTP 而不是"装完自己重启"，是因为大脑在云端一跑就是一周，
  // 为了加个技能把微信断一下不划算。
  if (req.method === 'POST' && p === '/reload') {
    loadAll().then(() => send(res, 200, { ok: true, reloaded: skills.size }))
      .catch((e) => send(res, 500, { ok: false, err: String(e.message || e).slice(0, 160) }))
    return
  }

  send(res, 404, { ok: false, err: '没这个路由' })
})

try {
  validateRuntimeConfig()
} catch (e) {
  console.error('[skill-host] 配置拒绝启动：' + String(e.message || e))
  process.exit(1)
}

loadAll().then(() => {
  server.listen(PORT, HOST, () =>
    log(fmt('监听 http://%s:%s，技能目录 %s', HOST, PORT, SKILL_DIR)))
}).catch((e) => {
  console.error('[skill-host] 安全检查失败，拒绝启动：' + String(e.message || e))
  process.exit(1)
})
