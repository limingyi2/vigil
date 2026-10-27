# -*- coding: utf-8 -*-
"""专家（Expert）的存储、扫描、匹配与绑定。

一个专家就是 config/experts/<key>/ 下的一个目录包：

    expert.json   元数据：name / description / scenarios / icon（线稿图标名）/
                  default_init_prompt（召唤后的开场白）/ tools（可选工具白名单）
                  skills（可选，要带的技能）/ files（可选，要带的资源文件）
    SKILL.md      专家提示词正文（角色 + 工作方式 + 纪律），纯文本
    references/   可选。资源包：给这个专家专用的参考材料，召唤时列进 system 段，
    assets/         模型按需自己去读（不是全塞进去 —— 塞进去等于把上下文烧光）
    skills/       可选。**真的带工具**：skills/<id>/{index.js, skill.json}，
                    安装时复制进 skill_host/skills，宿主 reload 后这些工具就能调。
                    与 expert.json 里 skills 字段的区别见下。

设计原则（WorkBuddy 逆向结论的轻量落地）：
- 专家不是模型、不是进程 —— 只是"换装"：提示词叠加 + 工具收窄 + 开场白。
- 目录即清单，不需要注册表：list_experts() 扫描即得，拖进去就生效。
- 绑定是**会话级**（P27）：experts_by_src 那种渠道绑定在 PC 上等于全局，
  一召唤所有对话都换装。现在绑定落在 conversations 行上，两个会话各用各的。
  召唤/卸下只改这一行，大脑 reload_persona() 后该会话的 system 段自然刷新。

绑定语义：conversations.expert = key。未绑定的会话保持纯人设。
同一会话换绑专家**保留**对话历史 —— 专家是模式不是人，上一句说过的话
下一句还能引用；system 段由 reload_persona() 重刷。

两种"带技能"的区别（都保留，别混）：

  expert.json 的 skills 字段  = **声明依赖**：技能得另外装好，专家只是说明
                                "我干活要用它"，并提醒模型先 skill_read。
  skills/ 子目录              = **真的带工具**：包解开后这些技能会被装进宿主，
                                模型直接拿到可调用的工具，不用用户再去找技能包。
                                带 .expert-owner 标记，只在召唤了本专家的会话里
                                可见（见 skill_visible）。
"""
import hashlib
import json
import os
import shutil
import sys

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
# 资源包只列清单，不塞正文：一份样稿 30KB，塞进 system 段等于每轮都烧一遍
# 上下文，而且模型并不会因此更准 —— 它需要的是"知道有这份文件、去哪读"。
MAX_RESOURCE_FILES = 40
RESOURCE_DIRS = ("references", "assets", "resources")
# 图标名只是 sprite 里的一个 symbol id（term / book / brain …），前端拿去
# <use href="#i-<name>">。这里只做形状校验，不认识的名字交给前端退化成首字，
# 免得大脑持有一份跟界面同步不上的图标白名单。
MAX_ICON = 24
# 专家包自带的技能目录名。只有这里的子目录会被当成技能包复制进宿主，
# references/ 里的东西永远不会被当成代码执行 —— 这条界线必须硬。
BUNDLED_SKILLS_DIR = "skills"
# 归属标记：技能目录里放这个文件，写专家 key。tools 侧据此判断
# "这个工具只属于某个专家"，别的会话连工具名都看不到。
SKILL_OWNER_FILE = ".expert-owner"
# 远程安装的来源记录，写在包目录里。有它才允许 uninstall —— 
# 没有它的包是用户自己放进 config/experts 的，程序不该替人删文件。
INSTALL_MARK_FILE = ".installed.json"

_CACHE = {}          # key -> (mtime, data)；磁盘改动靠 mtime 失效，热更新零成本
_KEY_BLACKLIST = {"", ".", ".."}

# 内置包种子指纹：记录"上一次是我们把哪个版本种进去的"。
# 有了它才能区分"用户改过"和"用户没改、只是包升级了" —— 只靠"文件已存在
# 就不覆盖"分不出来，代价是老用户的 expert.json 永远停在首次安装那一版：
# 新字段（icon、skills）拿不到，而面板上看不出任何异常，只是图标退化成首字。
SEED_FILE = ".seed.json"
_SEED_SKIP_DIRS = {"__pycache__", ".git", ".idea"}
_SEED_SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_SEED_DONE = set()   # 已种过的 (defaults, target) 组合：一次进程只种一遍


def _file_sha(path):
    try:
        digest = hashlib.sha256()
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(65536), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _load_seed(pkg_dir):
    try:
        with open(os.path.join(pkg_dir, SEED_FILE), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    files = data.get('files') if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def _save_seed(pkg_dir, files):
    try:
        with open(os.path.join(pkg_dir, SEED_FILE), 'w', encoding='utf-8') as handle:
            json.dump({'files': files}, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write('\n')
    except OSError:
        pass


def _json_merge_missing(target, source):
    """把内置 json 里用户没有的**顶层键**补进去，已有值一律不动。

    老用户升级上来时没有种子记录，没法判断这份 expert.json 是他改的还是我们
    种的。退回最保守的做法：只补键、不覆盖 —— 元数据文件因此总能拿到新字段
    （icon / default_init_prompt / skills），而 name、description 这些用户可能
    认真调过的东西原样保留。
    """
    try:
        with open(target, 'r', encoding='utf-8') as handle:
            current = json.load(handle)
        with open(source, 'r', encoding='utf-8') as handle:
            fresh = json.load(handle)
    except (OSError, ValueError):
        return False
    if not isinstance(current, dict) or not isinstance(fresh, dict):
        return False
    missing = [key for key in fresh if key not in current]
    if not missing:
        return False
    for key in missing:
        current[key] = fresh[key]
    try:
        with open(target, 'w', encoding='utf-8') as handle:
            json.dump(current, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    except OSError:
        return False
    return True


def _seed_package(source, target):
    """把一个内置专家包同步到用户目录，返回改动过的相对路径列表。

    规则（既要保护用户手改，又不能让包卡在旧版本）：
    - 目标没有这个文件 → 种下去（新版本新增的 references/ skills/ 也走这条）
    - 内容和内置完全一样 → 不动，只补种子记录
    - 有种子记录且当前内容 == 上次种的内容 → 用户没动过，跟着升级
    - 用户改过，或老版本没有种子记录 → json 只补缺失键，其他文件保留用户版本
    """
    seed = _load_seed(target)
    before = json.dumps(seed, sort_keys=True)
    touched = []
    for root, dirs, names in os.walk(source):
        dirs[:] = [item for item in dirs if item not in _SEED_SKIP_DIRS]
        rel_dir = os.path.relpath(root, source)
        for name in names:
            if name.startswith('.') or name in _SEED_SKIP_FILES:
                continue
            source_file = os.path.join(root, name)
            rel = name if rel_dir == '.' else os.path.join(rel_dir, name)
            rel = rel.replace('\\', '/')
            target_file = os.path.join(target, rel)
            fresh_sha = _file_sha(source_file)
            if not fresh_sha:
                continue
            current_sha = _file_sha(target_file)
            if not current_sha:
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                shutil.copy2(source_file, target_file)
                seed[rel] = fresh_sha
                touched.append(rel)
                continue
            if current_sha == fresh_sha:
                seed[rel] = fresh_sha
                continue
            if seed.get(rel) == current_sha:
                shutil.copy2(source_file, target_file)
                seed[rel] = fresh_sha
                touched.append(rel)
                continue
            if rel.endswith('.json') and _json_merge_missing(target_file, source_file):
                # 合并结果是"内置键 + 用户值"的混血，**不记进种子**：记了下次
                # 就会被当成"我们种的原样"整份覆盖，把用户的字段和格式冲掉。
                # 留空的话下次仍走合并分支，此时已无可补的键，自然幂等。
                touched.append(rel)
    if json.dumps(seed, sort_keys=True) != before:
        _save_seed(target, seed)
    return touched


def experts_root():
    # 打包内置包在不可变的 payload 里；用户目录是活的。两者不是一处时把它们
    # 对齐一次（见 _seed_package 的规则），后续同一进程不再重复扫。
    bundle = getattr(sys, '_MEIPASS', None) or os.path.dirname(__file__)
    defaults = os.path.join(bundle, 'config', 'experts')
    target_root = paths.EXPERTS_DIR
    if os.path.realpath(defaults) == os.path.realpath(target_root) or not os.path.isdir(defaults):
        return target_root
    once = (os.path.realpath(defaults), os.path.realpath(target_root))
    if once in _SEED_DONE:
        return target_root
    _SEED_DONE.add(once)
    os.makedirs(target_root, exist_ok=True)
    # 扫目录而不是写死两个 key：以后包里多一个内置专家，老用户升级就能拿到。
    for key in sorted(os.listdir(defaults)):
        source = os.path.join(defaults, key)
        if key.startswith('.') or not os.path.isdir(source):
            continue
        _seed_package(source, os.path.join(target_root, key))
    return target_root


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


def _normalize_icon(raw):
    """图标名：小写字母开头的 [a-z0-9-]，超限/非法一律丢空（前端退回首字）。"""
    icon = str(raw or "").strip().lower()
    if not icon or len(icon) > MAX_ICON:
        return ""
    if not (icon[0].isalpha() and icon[0].isascii()):
        return ""
    return icon if all(c.isalnum() or c == "-" for c in icon) else ""


def installed_root():
    """远程安装的专家包根目录（data/installed_experts）。"""
    return getattr(paths, "EXPERT_INSTALL_DIR",
                   os.path.join(paths.DATA_DIR, "installed_experts"))


def _pkg_dir(key):
    """专家包实际在哪个根下。

    **config/experts 优先**：同名 key 两边都存在时用用户手写的那份 ——
    远程装的包被覆盖升级，不该把用户改过的提示词顶掉。
    两边都没有就返回 config 下的路径（新建包默认落这儿）。
    """
    key = validate_key(key)
    if not key:
        return os.path.join(experts_root(), "_")
    local = os.path.join(experts_root(), key)
    if os.path.isdir(local):
        return local
    remote = os.path.join(installed_root(), key)
    if os.path.isdir(remote):
        return remote
    return local


def _meta_path(key):
    return os.path.join(_pkg_dir(key), "expert.json")


def _skill_path(key):
    return os.path.join(_pkg_dir(key), "SKILL.md")


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
        # icon 是界面上那枚方形徽标里的线稿图标（sprite 里的 symbol 名）。
        # avatar 是早期版本的 emoji 字段：留着只为不炸老包，但界面不再渲染它
        # —— 彩色 emoji 和全站的单色线性图标是两种视觉语言，混在一起就是
        # 一张面板里长出几个彩色贴纸。
        "icon": _normalize_icon(meta.get("icon")),
        "avatar": str(meta.get("avatar") or "")[:200],
        "default_init_prompt": str(meta.get("default_init_prompt") or "")[:MAX_PROMPT_CHARS],
        # 工具白名单：None = 不收窄（保留全部工具）；列表 = 只留列出的。
        # 专家只能收窄工具，永远放不出原本没有的工具。
        "tools": ([str(t).strip() for t in (meta.get("tools") or []) if str(t).strip()]
                  if isinstance(meta.get("tools"), list) else None),
        # 资源包与技能：让一个专家能自带参考资料、顺手带上几个现成技能，
        # 而不是只有一段提示词。两者都只"列出"，正文由模型按需去读。
        "files": [str(f).strip() for f in (meta.get("files") or []) if str(f).strip()]
                 if isinstance(meta.get("files"), list) else [],
        "skills": _normalize_skills(meta.get("skills")),
        "dir": os.path.abspath(_pkg_dir(key)),
        "prompt": prompt,
    }
    _CACHE[key] = ((mtime, smtime), data)
    if include_prompt:
        return dict(data)
    return {k: v for k, v in data.items() if k != "prompt"}


def list_experts():
    """扫描全部专家包，按目录名排序。坏的包跳过（不炸清单页）。

    两个根都扫：config/experts（用户手写）与 data/installed_experts（远程装）。
    同一个 key 两边都有时只用 config 那份 —— 与 _pkg_dir 的优先级保持一致，
    否则列表显示的是 A、召唤时生效的是 B，属于最难受的那种不一致。
    """
    out, seen = [], set()
    for root, source in ((experts_root(), "local"), (installed_root(), "remote")):
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            key = validate_key(name)
            if not key or key in seen:
                continue
            if not os.path.isdir(os.path.join(root, key)):
                continue
            meta, err = _read_meta(key)
            if meta is None:
                continue
            e = load_expert(key, include_prompt=False)
            if not e:
                continue
            seen.add(key)
            out.append({"key": e["key"], "name": e["name"],
                        "description": e["description"],
                        "scenarios": e["scenarios"],
                        "icon": e["icon"],
                        "avatar": e["avatar"],
                        # 面板上标一下"这个专家带了几份资料、几个技能"，
                        # 空口说"支持资源包"用户看不见。
                        "resources": len(_resource_files(key, e.get("files"))),
                        "skills": len(e.get("skills") or []),
                        # 真带工具的技能包有几个：这决定了召唤后模型手上
                        # 会不会真的多出可调用的工具。
                        "bundles": len(bundled_skills(key)),
                        "source": source})
    out.sort(key=lambda e: e["key"])
    return out


# --- 绑定（召唤状态）：会话级 ---
#
# P27 起绑定落在**会话**上，不再是渠道。渠道级在 PC 上等于全局：normalize_src
# 只认 'pc'，一召唤就把所有对话都换装了 —— 用户在 A 会话里叫了代码专家，
# 回到 B 会话写文案，人还是那个写代码的。会话级才是"这次对话用什么方式干活"。
#
# 读写都只经过 conversations 那一张表，这里不另存一份 —— 两份状态就是两个
# 真相，"召唤了但没生效"这类问题全从分叉处来。

def binding_for(conversation_id):
    """某个会话当前绑定的专家 key；未绑定或会话已删返回空串。"""
    try:
        import conversations
        return validate_key(conversations.expert_of_id(conversation_id))
    except Exception:
        return ""


def bind(conversation_id, key):
    """把专家绑到会话；key 传空即卸下。成功 True，会话不存在 None。"""
    import conversations
    return conversations.set_expert(conversation_id, key)


def session_expert(brain):
    """大脑此刻正在服务的会话绑的是哪个专家；拿不到就返回空串。

    工具表在拼规格和执行两处都要问这个 —— 一处决定"看不看得见"，
    另一处决定"能不能调"。两处必须问同一个来源，否则会出现
    "看不见但调得通"（提示词里出现过名字就能硬调）。
    """
    try:
        cid = getattr(brain, "_cur_conversation", None)
        if not cid:
            return ""
        return binding_for(cid)
    except Exception:
        return ""


def tool_allowlist(conversation_id):
    """该会话专家的工具白名单；未绑定或未声明返回 None（不收窄）。"""
    key = binding_for(conversation_id)
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


# --- 资源包 / 技能清单 ---

def _normalize_skills(raw):
    """skills 支持两种写法：纯字符串（技能 id）或 {id,name,desc} 对象。

    对象写法是为了让专家包自己写清"这个技能拿来干什么" —— 直接透传 id 的话，
    模型只能看到一个 slug，得先探一次才知道能不能用。
    """
    out = []
    for item in (raw or [])[:16]:
        if isinstance(item, str):
            key = item.strip()
            if key:
                out.append({"id": key, "name": key, "desc": ""})
        elif isinstance(item, dict):
            key = str(item.get("id") or item.get("key") or "").strip()
            if key:
                out.append({"id": key,
                            "name": str(item.get("name") or key)[:48],
                            "desc": str(item.get("desc") or item.get("description") or "")[:160]})
    return out


def _resource_files(key, declared=None):
    """专家目录下 references/ assets/ resources/ 的文件，加上显式声明的 files。

    返回 [(相对路径, 绝对路径, 字节数)]。相对路径是给模型看的（稳定、可读），
    绝对路径是它真去读时用的。目录不存在就跳过，不报错 —— 绝大多数专家包
    只有一份 SKILL.md，资源包是可选增强。
    """
    root = _pkg_dir(key)
    out, seen = [], set()

    def _size(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0

    for d in RESOURCE_DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                if rel in seen:
                    continue
                seen.add(rel)
                out.append((rel, os.path.abspath(p), _size(p)))
                if len(out) >= MAX_RESOURCE_FILES:
                    return out
    for raw in (declared or []):
        rel = str(raw or "").replace("\\", "/").strip().lstrip("./")
        if not rel or rel in seen:
            continue
        p = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(p):
            continue
        seen.add(rel)
        out.append((rel, os.path.abspath(p), _size(p)))
        if len(out) >= MAX_RESOURCE_FILES:
            break
    return out


def _human_size(n):
    n = int(n or 0)
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.1f MB" % (n / (1024.0 * 1024.0))


def _resource_section(key, expert):
    files = _resource_files(key, expert.get("files"))
    if not files:
        return ""
    lines = ["\n\n【专家资源包：%s】\n" % expert["name"],
             "以下材料只属于本专家。需要时用文件读取工具（local_file_read / "
             "local_file_grep）去读，**不要凭印象编造里面的内容**：\n"]
    for rel, abspath, size in files:
        lines.append("- %s（%s）→ %s\n" % (rel, _human_size(size), abspath))
    lines.append("动笔/下手之前先看一眼清单里相关的文件；没有相关文件就按通用规则做。\n")
    return "".join(lines)


def _skill_section(expert):
    skills = expert.get("skills") or []
    if not skills:
        return ""
    lines = ["\n\n【本专家带的技能】\n",
             "这些技能已经装好，**用法以技能说明为准**：先用 skill_read 读它的说明，"
             "再按说明调用，不要凭技能名猜参数。\n"]
    for s in skills:
        tail = ("：" + s["desc"]) if s.get("desc") else ""
        lines.append("- %s（id: %s）%s\n" % (s["name"], s["id"], tail))
    return "".join(lines)


# --- 包内自带技能：真的带工具 ---
#
# expert.json 的 skills 字段只是"声明"，包内 skills/ 目录才是"带"。两者都保留
# 是有意的分工：声明用于"这个技能我建议你先读一下用法"，带包用于"工具我给你
# 装好了，直接调"。合成一个字段的话，"到底装没装"就只能靠猜。

def skill_host_dir():
    """JS 技能宿主读的目录。

    必须和 core_features/skill._skill_dir() 算出**同一份**路径，否则装完了
    宿主看不见 —— 表现是「安装成功、技能列表里没有」，这种问题最难查。
    所以这里不自己拼，直接问那边要。
    """
    try:
        from core_features import skill as _skill_mod
        return _skill_mod._skill_dir()
    except Exception:
        pass
    override = os.environ.get("ZX_SKILL_DIR")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "skill_host", "skills")


def _skill_id_ok(value):
    """技能 id 同时是目录名和工具名前缀（<id>__<tool>），字符集收窄一点。"""
    sid = str(value or "").strip()
    if not sid or len(sid) > 48 or sid[0] in "._-":
        return ""
    ok = all(c.isalnum() or c in "-_" for c in sid) and sid.isascii()
    return sid if ok else ""


def bundled_skills(key):
    """专家包里的技能子包清单。判据是 index.js 在不在 —— 宿主认的就是它，
    skill.json 只是给人看的元数据（与 core_features/skill 的判定一致）。"""
    base = os.path.join(_pkg_dir(key), BUNDLED_SKILLS_DIR)
    out = []
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return out
    for name in entries:
        sid = _skill_id_ok(name)
        if not sid:
            continue
        path = os.path.join(base, sid)
        if not os.path.isfile(os.path.join(path, "index.js")):
            continue
        meta = {}
        try:
            with open(os.path.join(path, "skill.json"), encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception:
            meta = {}
        out.append({"id": sid,
                    "name": str(meta.get("name") or sid)[:48],
                    "desc": str(meta.get("description") or meta.get("desc") or "")[:160],
                    "dir": path})
    return out


_OWNER_CACHE = {}     # 技能目录 -> (mtime, owner)


def skill_owner(skill_id, host_dir=None):
    """这个技能是不是某个专家带进来的；不是（用户自己装的）返回空串。"""
    sid = _skill_id_ok(skill_id)
    if not sid:
        return ""
    path = os.path.join(host_dir or skill_host_dir(), sid, SKILL_OWNER_FILE)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _OWNER_CACHE.pop(path, None)
        return ""
    hit = _OWNER_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            owner = validate_key(f.read().strip())
    except Exception:
        owner = ""
    _OWNER_CACHE[path] = (mtime, owner)
    return owner


def skill_visible(skill_id, expert_key, host_dir=None):
    """当前会话能不能看见这个技能的工具。

    没人认领的技能（用户自己装的）对所有会话可见 —— 一条规则只多不少；
    带归属标记的只给召唤了那个专家的会话。这是"专家自带的工具不外溢"的
    唯一实现点，**不能只靠提示词劝**：工具名出现在表里，模型就会去调。
    """
    owner = skill_owner(skill_id, host_dir)
    if not owner:
        return True
    return owner == validate_key(expert_key or "")


def _copy_skill_tree(source, target):
    """拷一个技能目录。跳过版本库与字节码 —— 那些东西宿主不认，还会把
    包撑大；`.expert-owner` 由调用方最后写，不从源目录继承。"""
    def _ignore(_dir, names):
        drop = {"__pycache__", "node_modules", ".git"}
        return [n for n in names if n in drop or n.endswith(".pyc")]
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, ignore=_ignore,
                    ignore_dangling_symlinks=True)


def _same_or_newer(target, source):
    """目标里的 index.js 是否不比源包旧。只看入口文件：
    技能改动必然落在它身上，扫全目录反而把"只改了个 README"也算成更新。"""
    try:
        return (os.path.getmtime(os.path.join(target, "index.js"))
                >= os.path.getmtime(os.path.join(source, "index.js")))
    except OSError:
        return False


def install_bundled_skills(key, host_dir=None):
    """把专家包自带的技能装进宿主。返回 {installed, skipped, errors}。

    三种情况**不覆盖**，只跳过并说明理由：
      · 同名技能是用户自己装的（没有归属标记）—— 专家不该顶掉用户的东西；
      · 同名技能属于另一个专家 —— 后者优先，别互相偷；
      · 目标目录写不进去（权限/占用）。
    同一个专家重装/升级时会覆盖自己之前装的那份。
    """
    key = validate_key(key)
    host = host_dir or skill_host_dir()
    out = {"installed": [], "skipped": [], "errors": []}
    if not key:
        return out
    for s in bundled_skills(key):
        target = os.path.join(host, s["id"])
        current = skill_owner(s["id"], host)
        if os.path.isdir(target) and current != key:
            out["skipped"].append({
                "id": s["id"],
                "reason": ("技能名已被占用的用户技能" if not current
                           else "已属于专家 %s" % current)})
            continue
        # 已经装过且不比源包旧：跳过。召唤是热路径，每次全量拷一遍
        # 会让"点一下召唤"变成磁盘 I/O 抖动，而 99% 的情况什么都没变。
        if current == key and _same_or_newer(target, s["dir"]):
            continue
        try:
            os.makedirs(host, exist_ok=True)
            _copy_skill_tree(s["dir"], target)
            with open(os.path.join(target, SKILL_OWNER_FILE), "w",
                      encoding="utf-8") as f:
                f.write(key)
            _OWNER_CACHE.pop(os.path.join(target, SKILL_OWNER_FILE), None)
            out["installed"].append(s["id"])
        except Exception as e:
            out["errors"].append({"id": s["id"], "err": str(e)[:120]})
    return out


def uninstall_bundled_skills(key, host_dir=None):
    """卸掉某个专家带进来的技能（只删带它自己标记的那些）。"""
    key = validate_key(key)
    host = host_dir or skill_host_dir()
    removed = []
    if not key:
        return removed
    try:
        entries = os.listdir(host)
    except OSError:
        return removed
    for name in entries:
        sid = _skill_id_ok(name)
        if not sid or skill_owner(sid, host) != key:
            continue
        try:
            shutil.rmtree(os.path.join(host, sid))
            _OWNER_CACHE.pop(os.path.join(host, sid, SKILL_OWNER_FILE), None)
            removed.append(sid)
        except Exception:
            pass
    return removed


# --- 安装来源记录 ---

def installed_meta(key):
    """远程装的包才有：{source, at, sha256, files}。手放的包返回 {}。"""
    try:
        with open(os.path.join(_pkg_dir(key), INSTALL_MARK_FILE),
                  encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def mark_installed(key, meta):
    """给远程装的包写来源记录。写不进就算了 —— 它只是给面板和卸载用的，
    不该因为写不了这一行就把「装好了」变成「装失败」。"""
    try:
        with open(os.path.join(_pkg_dir(key), INSTALL_MARK_FILE), "w",
                  encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


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
    return head + (e.get("prompt") or "").strip() + _resource_section(key, e) + _skill_section(e)
