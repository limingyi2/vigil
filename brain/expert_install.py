# -*- coding: utf-8 -*-
"""专家包的远程安装：从 zip / 直链 / GitHub 仓库拉一个专家包下来。

**为什么要单独一个模块。** expert_store 管的是"本机已有哪些专家"，它假设
文件已经在盘上了；这里管的是"怎么把一个包从外面搬进来"，两者的失败模式
完全不同 —— 这边几乎所有代码都在防"外面"。混在一起会让 store 的每个函数
都要考虑网络。

**校验纪律（跟插件市场同一套，不另搞一套）：**

1. 先验完再落盘。解压到临时目录，验不过就一个字节都不写进正式目录 ——
   半个包留在那儿，下次扫描会读到它、报个看不懂的错。
2. 包里不许有绝对路径、反斜杠、`..`、空段、重复路径、符号链接。
   任何一条命中就整包拒收，不做"跳过那个文件"的宽容处理：一个包能塞进
   一条越界路径，说明它的来源就不该被信任。
3. 后缀白名单。专家包是数据 + 提示词 + JS 技能，不需要可执行文件。
4. 有上限：zip 8 MB、解开 32 MB、200 个文件、单文件 4 MB。
5. 装到 `data/installed_experts`，不碰 `config/experts`。那边是你手写的，
   升级和卸载都不该动它 —— 同名时也以那边为准。

**支持三种来源**，因为它们对应三种真实用法：

    owner/repo              发在 GitHub 上的专家包（最常见）
    https://.../x.zip       自己服务器 / Release / 网盘直链
    zip 字节流              本地拖进来的包（dev 与测试用）
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import time
import zipfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import expert_store

MAX_ZIP = 8 * 1024 * 1024
MAX_UNPACKED = 32 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024
MAX_FILES = 200
# 专家包里可能出现的后缀。**没有** .py/.exe/.dll/.sh/.bat —— 专家带的是
# 提示词、资料和 JS 技能，不是可执行文件；需要跑代码就写成技能，让宿主去跑。
ALLOWED_EXT = (".json", ".md", ".txt", ".js", ".mjs", ".cjs", ".css",
               ".html", ".csv", ".yaml", ".yml",
               ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")
# `owner/repo` 的字符集：GitHub 的命名规则，比这宽松的输入一律当非法路径。
_REPO_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class InstallError(ValueError):
    """安装失败。code 给前端分类，msg 是能直接给人看的一句话。"""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = str(code or "install_error")
        self.msg = str(msg or "")


def _norm_entry(name):
    """归一化 zip 里的一个条目名；非法返回 None。"""
    n = str(name or "").replace("\\", "/").strip()
    if not n or n in (".", "./"):
        return None
    if n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        raise InstallError("bad_path", "包里不许有绝对路径或盘符：%s" % n[:60])
    if n.endswith("/"):
        return None                     # 目录条目，内容会由文件带出来
    parts = n.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise InstallError("bad_path", "包内路径不能含空段、. 或 ..：%s" % n[:60])
    return "/".join(parts)


def inspect_zip(raw, label=""):
    """验一个专家 zip：形状、大小、路径、后缀。返回 (zipfile, [条目名])。

    **不解压**。这一层只回答"要不要碰它"，落盘是后面的事。
    """
    if not raw:
        raise InstallError("empty", "没收到内容")
    if len(raw) > MAX_ZIP:
        raise InstallError("too_large", "包 %.1f MB，超过上限 %d MB"
                           % (len(raw) / 1048576.0, MAX_ZIP // 1048576))
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as e:
        raise InstallError("not_zip", "不是有效的 zip：%s" % str(e)[:80])
    infos = zf.infolist()
    if len(infos) > MAX_FILES:
        raise InstallError("too_many_files", "包里有 %d 个文件，超过上限 %d"
                           % (len(infos), MAX_FILES))
    total, names, seen = 0, [], set()
    for inf in infos:
        if inf.flag_bits & 0x1:
            raise InstallError("encrypted", "不支持加密的包")
        mode = (int(inf.external_attr or 0) >> 16) & 0o170000
        if mode not in (0, 0o100000, 0o040000):
            raise InstallError("unsafe_entry",
                               "包里不允许符号链接或特殊文件：%s" % inf.filename[:80])
        n = _norm_entry(inf.filename)
        if n is None:
            continue
        total += int(inf.file_size or 0)
        if total > MAX_UNPACKED:
            raise InstallError("too_large", "解开后超过 %d MB" % (MAX_UNPACKED // 1048576))
        if int(inf.file_size or 0) > MAX_FILE:
            raise InstallError("too_large", "%s 单文件超过 %d MB"
                               % (n[:40], MAX_FILE // 1048576))
        folded = n.casefold()
        if folded in seen:
            raise InstallError("dup_path", "包里存在重复或大小写冲突的路径：%s" % n[:80])
        seen.add(folded)
        ext = os.path.splitext(n)[1].lower()
        if ext not in ALLOWED_EXT:
            raise InstallError("bad_ext", "专家包里不许有 %s（只准数据与 JS 技能）"
                               % (ext or "(无扩展名)"))
        names.append(n)
    if not names:
        raise InstallError("empty", "包里是空的")
    return zf, names


def _find_pkg_root(root):
    """找出真正的包根：**最浅**的那个含着 expert.json 的目录。

    GitHub 的 zip 外面永远套一层 `repo-main/`，用户自己打包又可能套两层；
    而 references/ 里如果恰好有一份 `expert.json` 示例，深层的那个是假的。
    所以按深度排序取最浅 —— 顺序比"第一个找到的"更可靠。
    """
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "expert.json" in filenames:
            depth = os.path.relpath(dirpath, root).count(os.sep)
            hits.append((depth, dirpath))
    if not hits:
        raise InstallError("no_manifest",
                           "包里找不到 expert.json —— 这不像一个专家包")
    hits.sort()
    return hits[0][1]


def _validate_pkg(pkg_dir):
    """验元数据与提示词。复用 store 的解析，保证"装得进去"等于"扫得出来"。"""
    try:
        with open(os.path.join(pkg_dir, "expert.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        raise InstallError("bad_manifest", "expert.json 读不动：%s" % str(e)[:80])
    if not isinstance(meta, dict):
        raise InstallError("bad_manifest", "expert.json 必须是一个对象")
    name = str(meta.get("name") or "").strip()
    if not name:
        raise InstallError("bad_manifest", "expert.json 缺少 name")
    skill_md = os.path.join(pkg_dir, "SKILL.md")
    try:
        with open(skill_md, encoding="utf-8") as f:
            body = f.read()
    except Exception:
        raise InstallError("no_prompt", "包里缺少 SKILL.md（专家提示词正文）")
    if not body.strip():
        raise InstallError("no_prompt", "SKILL.md 是空的")
    return meta


def install_zip(raw, key_hint="", source=""):
    """验 → 解 → 落盘 → 装自带技能。返回专家条目。

    任何一步失败都清干净临时目录，**不留半个包**。
    """
    zf, _names = inspect_zip(raw, source)
    tmpdir = tempfile.mkdtemp(prefix="vigil_expert_")
    try:
        try:
            zf.extractall(tmpdir)
        except Exception as e:
            raise InstallError("extract_failed", "解压失败：%s" % str(e)[:80])
        pkg = _find_pkg_root(tmpdir)
        meta = _validate_pkg(pkg)
        key = expert_store.validate_key(key_hint) or expert_store.validate_key(
            os.path.basename(pkg))
        if not key:
            raise InstallError("bad_key",
                               "专家包的目录名不合法：%s" % os.path.basename(pkg)[:60])
        # config 里已有同名包：远程这份永远不生效（_pkg_dir 优先读 config）。
        # 与其"装成功但没变化"，不如直接说清楚。
        if os.path.isdir(os.path.join(expert_store.experts_root(), key)):
            raise InstallError(
                "shadowed",
                "config/experts 里已有同名专家「%s」。那份优先于远程安装，"
                "要换成就先把它移走或改名。" % key)
        target = os.path.join(expert_store.installed_root(), key)
        os.makedirs(expert_store.installed_root(), exist_ok=True)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(pkg, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        digest = hashlib.sha256(raw).hexdigest()
        expert_store.mark_installed(key, {
            "source": str(source or "zip")[:300],
            "sha256": digest,
            "bytes": len(raw),
            "at": int(time.time()),
            "package": str(meta.get("name") or key)[:80],
        })
        bundled = expert_store.install_bundled_skills(key)
        _reload_host(bundled)
        item = expert_store.load_expert(key, include_prompt=False)
        if not item:
            raise InstallError("verify_failed", "装完了但读不出来，请检查包内容")
        item["bundled"] = expert_store.bundled_skills(key)
        item["skill_sync"] = bundled
        return item
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _reload_host(bundled):
    """装完技能后让 JS 宿主重新扫一遍。失败不算安装失败 ——
    技能下次重启宿主照样会被读到，而"包已经装好了"是既成事实。"""
    if not (bundled or {}).get("installed"):
        return {"ok": None, "skipped": True}
    try:
        from core_features import skill as _skill_mod
        _skill_mod._host_reload_checked()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "err": str(e)[:120]}


def fetch(url, timeout=20):
    """下载一个包。只认 http/https，边读边算大小，不信 Content-Length。

    不信长度是因为它由对方给：声明 1 KB 实际灌 500 MB 是最省事的打法，
    而"读满了再判断"意味着内存已经被占满了。
    """
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise InstallError("bad_url", "只支持 http/https 链接")
    req = Request(parsed.geturl(), headers={"User-Agent": "Vigil/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            chunks, total = [], 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ZIP:
                    raise InstallError("too_large", "下载超过 %d MB，已中断"
                                       % (MAX_ZIP // 1048576))
                chunks.append(chunk)
    except InstallError:
        raise
    except Exception as e:
        raise InstallError("network", "下载失败：%s" % str(e)[:120])
    return b"".join(chunks)


def github_zip_url(repo, ref=""):
    """把 `owner/repo[@ref]` 变成 codeload 的 zip 地址。

    走 codeload 而不是 `raw.githubusercontent.com`：一个专家包是多文件目录，
    raw 得一文件一请求，而 codeload 一次给整棵树，天然带一层 `repo-ref/` 前缀，
    正好由 _find_pkg_root 剥掉。
    """
    import re
    raw = str(repo or "").strip().strip("/")
    ref = str(ref or "").strip()
    if "@" in raw and not ref:
        raw, ref = raw.split("@", 1)
    parts = raw.split("/")
    if len(parts) != 2 or not all(re.match(_REPO_RE, p) for p in parts):
        raise InstallError("bad_repo", "仓库要写成 owner/repo")
    ref = ref or "main"
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$", ref):
        raise InstallError("bad_repo", "分支名不合法")
    return "https://codeload.github.com/%s/%s/zip/refs/heads/%s" % (
        parts[0], parts[1], ref)


def repo_name(repo):
    """`owner/repo[@ref]` → `repo`。用来给装出来的专家起名字。"""
    raw = str(repo or "").strip().strip("/")
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    parts = raw.split("/")
    return parts[1] if len(parts) == 2 else ""


def _key_from_url(url, fallback=""):
    """从下载地址猜一个 key。三段规则，覆盖实际会遇到的三种链接：

    · GitHub「Download ZIP」`/<owner>/<repo>/archive/refs/heads/<branch>.zip`
      → 取 `archive` 前面那段（repo 名）。不特判的话会跑出 key=`main`。
    · codeload `.../zip/refs/heads/main` → 没有文件名，交给调用方给的 hint。
    · 普通直链 `packs/writer.zip` → 文件名去扩展名。

    猜错了不要紧 —— 调用方传了 key 就以 key 为准。
    """
    raw = str(url or "").replace("\\", "/").strip()
    path = urlparse(raw).path or raw
    # codeload 的路径末段是分支名，不是包名 —— 直接交回调用方给的 hint，
    # 否则会装出一个叫 `main` 的专家。
    if "codeload.github.com" in raw or "/zip/refs/" in path:
        return fallback
    parts = [p for p in path.split("/") if p]
    if "archive" in parts:
        i = parts.index("archive")
        if i >= 1:
            return expert_store.validate_key(parts[i - 1]) or fallback
    name = parts[-1] if parts else ""
    stem = os.path.splitext(name)[0]
    for tail in ("-main", "-master"):
        if stem.endswith(tail) and len(stem) > len(tail):
            stem = stem[: -len(tail)]
            break
    return expert_store.validate_key(stem) or fallback


def install(source, key_hint="", ref=""):
    """统一入口：认 `owner/repo`、`https://...zip`、本地 zip 路径三种写法。

    本地路径只在源码运行时有意义（安装版没有"用户能点到的本机路径"），
    保留它是为了开发时能直接 `install('_tmp/my-expert.zip')` 试包。
    """
    src = str(source or "").strip()
    if not src:
        raise InstallError("empty", "没给来源")
    hint = expert_store.validate_key(key_hint)
    if src.startswith("http://") or src.startswith("https://"):
        url = src
    elif src.lower().endswith(".zip") and os.path.isfile(src):
        with open(src, "rb") as f:
            return install_zip(f.read(),
                               key_hint=hint or _key_from_url(src),
                               source=src)
    else:
        url = github_zip_url(src, ref)
        # GitHub 的包名是 `repo-ref`，用仓库名当 key 才符合直觉
        if not hint:
            hint = expert_store.validate_key(
                str(repo_name(src) or ""))
    if not url.lower().split("?")[0].endswith(".zip") and "codeload" not in url:
        # 非 zip 直链：多半是仓库网页或 raw 目录，说清楚该给什么
        raise InstallError("bad_source",
                           "这个链接不是 zip 包。可以用 owner/repo，或直接给 zip 下载地址。")
    return install_zip(fetch(url), key_hint=hint or _key_from_url(url), source=url)


def uninstall(key):
    """卸掉一个**远程安装的**专家包。

    只认 data/installed_experts 下、且带安装记录的包。config/experts 里
    用户手写的专家一律不动 —— 那个目录是"你的"，程序替人删配置文件是最
    不该发生的一类事故（而且删错了没有任何东西能还原）。
    """
    key = expert_store.validate_key(key)
    if not key:
        raise InstallError("bad_key", "专家 key 不合法")
    target = os.path.join(expert_store.installed_root(), key)
    if not os.path.isdir(target):
        raise InstallError("not_installed", "这个专家不是远程安装的（在 config/experts 里）")
    if not expert_store.installed_meta(key):
        raise InstallError("not_installed", "缺少安装记录，不替你删")
    removed_skills = expert_store.uninstall_bundled_skills(key)
    shutil.rmtree(target, ignore_errors=True)
    _reload_host({"installed": removed_skills})
    return {"key": key, "skills_removed": removed_skills}
