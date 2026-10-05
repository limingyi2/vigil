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
const { pathToFileURL } = require('node:url')

// --- 配置：环境变量给默认值，配置文件可覆盖 ---------------------------------
const SKILL_DIR = process.env.ZX_SKILL_DIR || path.join(__dirname, 'skills')
const PORT = Number(process.env.ZX_SKILL_PORT || 8790)
const HOST = process.env.ZX_SKILL_HOST || '127.0.0.1'
// 调 skill 工具的硬超时。skill 可能去爬图（P站很慢），但知夏一轮对话
// 不能被它拖死 —— 超时后大脑那边照样能回话，只是这个工具算失败。
const CALL_TIMEOUT = Number(process.env.ZX_SKILL_TIMEOUT || 25000)

// 🔴 不用 console.log 直接转发：它**不认 %s 占位符**，
// 多参数转发过去会打成字面量 "装载 %s 失败：%s"（实测踩过）。
// 自己拼字符串，顺便统一加前缀。
const log = (...a) => console.log('[skill-host]', ...a.map(x => (typeof x === 'string' ? x : String(x))))

/** 说清一个字符串：把 %s 逐个替换掉。log 只收数组形式了。 */
const fmt = (tpl, ...vals) => {
  let i = 0
  return String(tpl).replace(/%s/g, () => (i < vals.length ? String(vals[i++]) : '%s'))
}

/** 已装载的技能：{ id -> {mod, meta, tools:{toolName->def}} } */
const skills = new Map()

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
  for (const name of fs.readdirSync(SKILL_DIR)) {
    const dir = path.join(SKILL_DIR, name)
    let st = null
    try { st = fs.statSync(dir) } catch (e) { continue }
    // 跳过非目录和下划线开头的（跟 python 插件同一套规矩，别搞两套标准）
    if (!st.isDirectory() || name.startsWith('_') || name.startsWith('.')) continue
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
  const realDir = resolveSkillDir(dir)
  const meta = readMeta(realDir)
  const entry = meta.entry || 'index.js'
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
  if (typeof mod.setup !== 'function') throw new Error('这个包没有 setup(api)，不是 skill')

  const tools = {}
  // 垫出来的 api：形状照 QQ Agent 那套，够 skill 认，但每个方法都另有用处。
  const api = {
    config: () => ({ ...(meta.settings || {}) }),
    // 不加 '[' ']'：log 已经带了 '[skill-host]' 前缀，技能自己又加一层
    // 的话日志里会出现 "[[daily-fortune] ...]" 这种双括号（实测出现过）。
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

  await mod.setup(api)
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
// 群里没被点名照样给签。原skill 的 `gate()` 实测过这个组合，判据 4 就是这么挂的。
// 传错格式的代价不是"拦错"，是"完全不拦"，所以宁可多写一个转换。
const TIER = { private: 0, mentioned: 1, group: 2, keyword: 2, random: 3 }

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
    kind: tierOf(src.tier) === 0 ? 'private' : 'group',
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
    return
  }

  send(res, 404, { ok: false, err: '没这个路由' })
})

server.listen(PORT, HOST, () =>
  log(fmt('监听 http://%s:%s，技能目录 %s', HOST, PORT, SKILL_DIR)))

loadAll()
