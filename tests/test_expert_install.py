# -*- coding: utf-8 -*-
"""专家包"带工具"与"能装"这两件事的测试。

两条主线：
  · 包内 skills/ 目录 → 复制进技能宿主 → 带归属标记 → 只有对应专家的会话可见；
  · zip / 链接安装 → 严格校验 → 落在 data/installed_experts → 可卸载。

**测试不碰真实目录**：experts_root / installed_root / skill_host_dir 三个
入口全部 monkeypatch 到 tmp_path —— 否则跑一次测试会把示例技能装进开发机
的技能目录，而且没有任何东西会把它清掉。
"""
import io
import json
import zipfile
from pathlib import Path

import pytest

import expert_install
import expert_store


GOOD_META = {
    "name": "带工具专家",
    "description": "测试用",
    "scenarios": ["测试"],
    "icon": "term",
    "default_init_prompt": "开始吧",
}

SKILL_JS = """module.exports = { setup(api) {
  api.registerTool({ id: 'count', name: '字数统计', parameters: {type:'object',properties:{}} ,
    async execute() { return { ok: true }; } });
} };
"""


def _write_pkg(root, key, meta=None, skill_md="# 规则\n\n干活。", bundled=()):
    d = Path(root) / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "expert.json").write_text(
        json.dumps(meta or GOOD_META, ensure_ascii=False), encoding="utf-8")
    (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for sid in bundled:
        s = d / "skills" / sid
        s.mkdir(parents=True, exist_ok=True)
        (s / "index.js").write_text(SKILL_JS, encoding="utf-8")
        (s / "skill.json").write_text(
            json.dumps({"id": sid, "name": sid}, ensure_ascii=False),
            encoding="utf-8")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    """三个根都搬到临时目录：手写包 / 远程包 / 技能宿主。"""
    local = tmp_path / "experts"
    remote = tmp_path / "installed_experts"
    host = tmp_path / "skills"
    for p in (local, remote, host):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(expert_store, "experts_root", lambda: str(local))
    monkeypatch.setattr(expert_store, "installed_root", lambda: str(remote))
    monkeypatch.setattr(expert_store, "skill_host_dir", lambda: str(host))
    expert_store._CACHE.clear()
    expert_store._OWNER_CACHE.clear()
    yield {"local": local, "remote": remote, "host": host, "tmp": tmp_path}
    expert_store._CACHE.clear()
    expert_store._OWNER_CACHE.clear()


def _zip_of(pkg_dir, prefix="repo-main/"):
    """把一个包目录打成 zip，外面套一层（模拟 GitHub 的 repo-main/）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in sorted(Path(pkg_dir).rglob("*")):
            if p.is_file():
                zf.write(p, prefix + str(p.relative_to(pkg_dir)).replace("\\", "/"))
    return buf.getvalue()


# --- 包内技能 -------------------------------------------------------------

def test_bundled_skills_found_and_installed(env):
    _write_pkg(env["local"], "writer", bundled=["writing-tools"])
    assert [s["id"] for s in expert_store.bundled_skills("writer")] == ["writing-tools"]

    got = expert_store.install_bundled_skills("writer")
    assert got["installed"] == ["writing-tools"]
    installed = env["host"] / "writing-tools"
    assert (installed / "index.js").is_file()
    # 归属标记是隔离的依据，必须落盘
    assert (installed / expert_store.SKILL_OWNER_FILE).read_text(
        encoding="utf-8") == "writer"
    assert expert_store.skill_owner("writing-tools") == "writer"
    # 清单里要能看出"带了几个真工具"
    listed = [e for e in expert_store.list_experts() if e["key"] == "writer"][0]
    assert listed["bundles"] == 1


def test_reinstall_is_noop_when_up_to_date(env):
    _write_pkg(env["local"], "writer", bundled=["writing-tools"])
    expert_store.install_bundled_skills("writer")
    again = expert_store.install_bundled_skills("writer")
    assert again["installed"] == []          # 没变化就不拷，召唤是热路径


def test_never_overwrites_user_skill(env):
    """用户自己装的同名技能：专家不许顶掉它，也不许打上自己的标记。"""
    _write_pkg(env["local"], "writer", bundled=["writing-tools"])
    user = env["host"] / "writing-tools"
    user.mkdir()
    (user / "index.js").write_text("// 用户自己的\n", encoding="utf-8")

    got = expert_store.install_bundled_skills("writer")
    assert got["installed"] == []
    assert got["skipped"] and "用户" in got["skipped"][0]["reason"]
    assert (user / "index.js").read_text(encoding="utf-8") == "// 用户自己的\n"
    assert expert_store.skill_owner("writing-tools") == ""


def test_skills_are_isolated_per_expert(env):
    _write_pkg(env["local"], "writer", bundled=["writing-tools"])
    expert_store.install_bundled_skills("writer")
    assert expert_store.skill_visible("writing-tools", "writer") is True
    assert expert_store.skill_visible("writing-tools", "code-expert") is False
    assert expert_store.skill_visible("writing-tools", "") is False
    # 没归属的技能对谁都可见（用户自己装的那批不能被规则误伤）
    (env["host"] / "plain").mkdir()
    assert expert_store.skill_visible("plain", "") is True


def test_tool_table_hides_owned_skill_from_other_sessions(env, monkeypatch):
    """工具表层面：普通会话看不到专家自带的工具，专家会话能看到。"""
    import sys
    import types
    import tools

    _write_pkg(env["local"], "writer", bundled=["writing-tools"])
    expert_store.install_bundled_skills("writer")
    # tools 里是 `import skill as _skill`（宿主适配器，靠 capability_registry
    # 把 core_features 挂上 sys.path 才 import 得到）。测试直接塞一个假的，
    # 不依赖启动顺序，也不碰真的 Node 宿主。
    fake = types.ModuleType("skill")
    fake.tools = lambda src=None: {
        "writing-tools__count": {"title": "字数统计", "desc": "",
                                 "skill": "writing-tools", "params": {}},
        "plain__ping": {"title": "ping", "desc": "",
                        "skill": "plain", "params": {}},
    }
    monkeypatch.setitem(sys.modules, "skill", fake)

    def names(specs):
        return {s["function"]["name"] for s in specs}

    monkeypatch.setattr(expert_store, "session_expert", lambda brain: "")
    assert names(tools._dynamic_tool_array(None, "pc")) == {"plain__ping"}

    monkeypatch.setattr(expert_store, "session_expert", lambda brain: "writer")
    assert names(tools._dynamic_tool_array(None, "pc")) == {
        "writing-tools__count", "plain__ping"}

    # 换一个专家：还是看不到别人的工具
    monkeypatch.setattr(expert_store, "session_expert", lambda brain: "code-expert")
    assert names(tools._dynamic_tool_array(None, "pc")) == {"plain__ping"}


def test_uninstall_bundled_skills_only_removes_own(env):
    _write_pkg(env["local"], "writer", bundled=["writing-tools"])
    expert_store.install_bundled_skills("writer")
    (env["host"] / "user-skill").mkdir()
    removed = expert_store.uninstall_bundled_skills("writer")
    assert removed == ["writing-tools"]
    assert not (env["host"] / "writing-tools").exists()
    assert (env["host"] / "user-skill").exists()


# --- 远程安装 -------------------------------------------------------------

def test_install_zip_lands_in_installed_root(env):
    pkg = _write_pkg(env["local"], "writer", bundled=["writing-tools"])
    item = expert_install.install_zip(_zip_of(pkg), source="test")
    assert item["key"] == "repo-main"          # 没给 key 就用包目录名
    # 上面那条其实说明：GitHub 的 repo-main/ 会被当成包名 —— 所以安装时
    # 必须允许显式指定 key（见下一个用例），否则名字会是仓库名。
    assert (env["remote"] / "repo-main" / "expert.json").is_file()
    assert (env["host"] / "writing-tools" / expert_store.SKILL_OWNER_FILE).is_file()


def test_install_zip_with_key_hint(env):
    # 源包放在"外面"，不能放在 config/experts —— 那里有同名包的话会被拒绝
    # (shadowed)，那是另一个用例。
    src = _write_pkg(env["tmp"] / "src", "writer")
    item = expert_install.install_zip(_zip_of(src), key_hint="writer", source="test")
    assert item["key"] == "writer"
    assert item["name"] == GOOD_META["name"]
    meta = expert_store.installed_meta("writer")
    assert meta["source"] == "test" and meta["sha256"]
    # config/experts 里没有它，但清单里能扫到，且来源标成 remote
    listed = [e for e in expert_store.list_experts() if e["key"] == "writer"][0]
    assert listed["source"] == "remote"


def test_install_rejects_path_traversal(env):
    pkg = _write_pkg(env["local"], "writer")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("writer/expert.json", json.dumps(GOOD_META))
        zf.writestr("../evil.md", "x")
    with pytest.raises(expert_install.InstallError) as err:
        expert_install.install_zip(buf.getvalue(), key_hint="writer")
    assert err.value.code == "bad_path"
    assert not (env["remote"] / "writer").exists()


def test_install_rejects_executable_ext(env):
    pkg = _write_pkg(env["local"], "writer")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("writer/expert.json", json.dumps(GOOD_META))
        zf.writestr("writer/SKILL.md", "# 规则")
        zf.writestr("writer/run.exe", b"MZ")
    with pytest.raises(expert_install.InstallError) as err:
        expert_install.install_zip(buf.getvalue())
    assert err.value.code == "bad_ext"


def test_install_rejects_missing_prompt(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("writer/expert.json", json.dumps(GOOD_META))
    with pytest.raises(expert_install.InstallError) as err:
        expert_install.install_zip(buf.getvalue())
    assert err.value.code == "no_prompt"


def test_install_refuses_shadowed_key(env):
    """config/experts 里已有同名包时不能装作装好了 —— 那份优先，装也白装。"""
    _write_pkg(env["local"], "writer")
    buf = _zip_of(_write_pkg(env["tmp"], "w2"))
    with pytest.raises(expert_install.InstallError) as err:
        expert_install.install_zip(buf, key_hint="writer")
    assert err.value.code == "shadowed"


def test_uninstall_only_remote_packages(env):
    _write_pkg(env["local"], "handmade")                 # 手写包
    expert_install.install_zip(_zip_of(_write_pkg(env["tmp"], "w2")),
                               key_hint="remote-one")
    with pytest.raises(expert_install.InstallError) as err:
        expert_install.uninstall("handmade")
    assert err.value.code == "not_installed"
    assert (env["local"] / "handmade" / "expert.json").is_file()

    out = expert_install.uninstall("remote-one")
    assert out["key"] == "remote-one"
    assert not (env["remote"] / "remote-one").exists()


def test_github_url_shape():
    url = expert_install.github_zip_url("owner/repo")
    assert url == "https://codeload.github.com/owner/repo/zip/refs/heads/main"
    assert expert_install.github_zip_url("owner/repo@dev").endswith("/refs/heads/dev")
    for bad in ("owner", "owner/repo/extra", "../etc/passwd", ""):
        with pytest.raises(expert_install.InstallError):
            expert_install.github_zip_url(bad)


def test_key_derivation_from_sources():
    """GitHub 的 zip 里外层的 `repo-main/` 不能变成专家名字。"""
    assert expert_install.repo_name("owner/writer") == "writer"
    assert expert_install.repo_name("owner/writer@dev") == "writer"
    # codeload 没有文件名，靠调用方给的 fallback（install() 会传仓库名）
    assert expert_install._key_from_url(
        "https://codeload.github.com/o/r/zip/refs/heads/main",
        fallback="r") == "r"
    # 「Download ZIP」：不能推出个 key=main
    assert expert_install._key_from_url(
        "https://github.com/o/writer/archive/refs/heads/main.zip") == "writer"
    assert expert_install._key_from_url(
        "https://x.example/packs/writer.zip") == "writer"
    assert expert_install._key_from_url("D:/packs/demo.zip") == "demo"


def test_install_from_local_zip_uses_filename_as_key(env):
    src = _write_pkg(env["tmp"] / "src", "whatever")
    zpath = env["tmp"] / "writer-pack.zip"
    zpath.write_bytes(_zip_of(src))
    item = expert_install.install(str(zpath))
    assert item["key"] == "writer-pack"


def test_rejects_non_zip_source(env):
    with pytest.raises(expert_install.InstallError) as err:
        expert_install.install("https://example.com/expert/")
    assert err.value.code == "bad_source"
