# -*- coding: utf-8 -*-
"""专家（Expert）的存储、扫描、匹配与绑定。

一个专家就是 config/experts/<key>/ 下的一个目录包：

    expert.json   元数据：name / description / scenarios / avatar /
                  default_init_prompt（召唤后的开场白）/ tools（可选工具白名单）
    SKILL.md      专家提示词正文（角色 + 工作方式 + 纪律），纯文本

设计原则（WorkBuddy 逆向结论的轻量落地）：
- 专家不是模型、不是进程 —— 只是"换装"：提示词叠加 + 工具收窄 + 开场白。
- 目录即清单，不需要注册表：list_experts() 扫描即得，拖进去就生效。
- 渠道绑定沿用 persona_by_src 的既有模式（config.experts_by_src），
  召唤/卸下只改这份绑定，大脑 reload_persona() 后全链路（system 段、
  历史首条）自然刷新。

绑定语义：experts_by_src[src] = key。未绑定的渠道保持纯人设。
同一渠道换绑专家**保留**对话历史 —— 专家是模式不是人，上一句说过的话
下一句还能引用；system 段由 reload_persona() 重刷。
"""
import json
import os

import paths

# 专家 key 的形状约束：与 personas 一致（目录名即 key）。
# 大小限制防一个坏包把 system 段撑爆：超限的专家不可召唤，而不是截断 ——
# 截断会让"看起来在工作其实规则丢了"这种坑藏得更深。
MAX_NAME = 64
MAX_DESCRIPTION = 400
MAX_SCENARIOS = 24
MAX_SCENARIO_LEN = 24
MAX_SKILL_CHARS = 32000
MAX_PROMPT_CHARS = 500

_CACHE = {}          # key -> (mtime, data)；磁盘改动靠 mtime 失效，热更新零成本
_KEY_BLACKLIST = {"", ".", ".."}


def experts_root():
    return paths.EXPERTS_DIR


def validate_key(key):
    """合法则原样返回，否则返回空串。只做静态校验，不碰文件系统。"""
    key = str(key or "").strip()
    if key in _KEY_BLACKLIST or "/" in key or "\\" in key or ":" in key:
        return ""
    if any(ord(c) < 32 for c in key) or len(key) > 64:
        return ""
    if key.endswith(".") or key.endswith(" "):
        return ""
    return key


def _meta_path(key):
    return os.path.join(experts_root(), key, "expert.json")


def _skill_path(key):
    return os.path.join(experts_root(), key, "SKILL.md")


def _read_meta(key):
    """读一份 expert.json，返回 (meta_dict, err)。err 非空说明包坏了。"""
    try:
        with open(_meta_path(key), encoding="utf-8") as f:
            meta = json.load(f)
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:
        return None, str(e)[:120]
    if not isinstance(meta, dict):
        return None, "meta_not_object"
    return meta, ""


def load_expert(key, include_prompt=True):
    """加载一个专家包；不存在或损坏返回 None。

    include_prompt=False 时不算 SKILL.md（清单页用，省读大文件）。
    mtime 缓存：包文件改了立刻生效，与 persona 的热更新行为一致。
    """
    key = validate_key(key)
    if not key:
        return None
    try:
        mtime = os.path.getmtime(_meta_path(key))
        smtime = os.path.getmtime(_skill_path(key)) if include_prompt else 0.0
    except OSError:
        _CACHE.pop(key, None)
        return None
    hit = _CACHE.get(key)
    if hit and hit[0] == (mtime, smtime):
        data = hit[1]
        return dict(data) if include_prompt else {k: v for k, v in data.items()
                                                  if k != "prompt"}
    meta, err = _read_meta(key)
    if meta is None:
        _CACHE.pop(key, None)
        return None
    if include_prompt:
        try:
            with open(_skill_path(key), encoding="utf-8") as f:
                prompt = f.read()
        except OSError:
            _CACHE.pop(key, None)
            return None
        if len(prompt) > MAX_SKILL_CHARS:
            return None
    else:
        prompt = ""
    name = str(meta.get("name") or key)[:MAX_NAME] or key
    description = str(meta.get("description") or "")[:MAX_DESCRIPTION]
    scenarios = [str(s).strip()[:MAX_SCENARIO_LEN]
                 for s in (meta.get("scenarios") or [])[:MAX_SCENARIOS]
                 if str(s).strip()]
    data = {
        "key": key,
        "name": name,
        "description": description,
        "scenarios": scenarios,
        "avatar": str(meta.get("avatar") or "")[:200],
        "default_init_prompt": str(meta.get("default_init_prompt") or "")[:MAX_PROMPT_CHARS],
        # 工具白名单：None = 不收窄（保留全部工具）；列表 = 只留列出的。
        # 专家只能收窄工具，永远放不出原本没有的工具。
        "tools": ([str(t).strip() for t in (meta.get("tools") or []) if str(t).strip()]
                  if isinstance(meta.get("tools"), list) else None),
        "prompt": prompt,
    }
    _CACHE[key] = ((mtime, smtime), data)
    if include_prompt:
        return dict(data)
    return {k: v for k, v in data.items() if k != "prompt"}


def list_experts():
    """扫描全部专家包，按目录名排序。坏的包跳过（不炸清单页）。"""
    root = experts_root()
    out = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return out
    for name in entries:
        key = validate_key(name)
        if not key or not os.path.isdir(os.path.join(root, key)):
            continue
        meta, err = _read_meta(key)
        if meta is None:
            continue
        e = load_expert(key, include_prompt=False)
        if e:
            out.append({"key": e["key"], "name": e["name"],
                        "description": e["description"],
                        "scenarios": e["scenarios"],
                        "avatar": e["avatar"]})
    return out


# --- 绑定（召唤状态） ---

def binding(cfg, src):
    """某渠道当前绑定的专家 key，没绑返回空串。cfg 是内存里的活动配置。"""
    by = (cfg or {}).get("experts_by_src")
    if not isinstance(by, dict):
        return ""
    return validate_key(by.get(str(src or "")) or "")


def bindings(cfg):
    """全量绑定快照（设置页展示"哪个渠道在用哪个专家"）。"""
    by = (cfg or {}).get("experts_by_src")
    if not isinstance(by, dict):
        return {}
    out = {}
    for src, key in by.items():
        k = validate_key(key)
        if k:
            out[str(src)] = k
    return out


def tool_allowlist(cfg, src):
    """该渠道专家的工具白名单；未绑定专家或白名单未声明返回 None（不收窄）。"""
    key = binding(cfg, src)
    if not key:
        return None
    e = load_expert(key, include_prompt=False)
    if not e:
        return None
    allow = e.get("tools")
    if not allow:
        return None
    return frozenset(allow)


# --- 场景匹配（WorkBuddy curated-experts 的推荐机制） ---

def match_experts(text, limit=3):
    """按触发关键词给一段输入打分，返回最相关的专家。

    评分 = 命中的 scenario 数（每个 1 分）。平分时目录序稳定输出。
    关键词是"包含"匹配：scenario "代码" 能被 "这段代码有bug" 命中。
    不用模型、不联网 —— 输入框旁的实时推荐必须零成本。
    """
    text = str(text or "").strip()
    if not text:
        return []
    hits = []
    for e in list_experts():
        if not e["scenarios"]:
            continue
        matched = [s for s in e["scenarios"] if s in text]
        if matched:
            hits.append({"key": e["key"], "name": e["name"],
                         "description": e["description"],
                         "score": len(matched), "hits": matched})
    hits.sort(key=lambda x: (-x["score"], x["key"]))
    return hits[:max(1, int(limit or 3))]


# --- system 叠加段 ---

def system_overlay(key):
    """把专家 SKILL.md 拼成接在人设后面的 system 文本段；key 无效返回空串。

    夹在人设字段与渠道底线之间是不对的 —— 专家是"现在的工作方式"，
    权重应该高于人设的背景描述；但渠道底线（CORE_RULES / ASSISTANT_RULES）
    必须仍在最末尾压轴，所以调用方约定：overlay 接在 build_system_text()
    产物之后、底线之前做不到，就接在整个产物之后。当前取后者：
    专家内容里明写"底线规则仍然有效"，避免与 persona_store 的底部约束打架。
    """
    key = validate_key(key)
    if not key:
        return ""
    e = load_expert(key)
    if not e:
        return ""
    head = "\n\n【专家模式：%s】\n" % e["name"]
    head += ("现在进入专家工作模式。以下规则**优先于上面的人设背景**，"
             "但最末尾的渠道底线规则仍然有效。\n")
    return head + (e.get("prompt") or "").strip()
