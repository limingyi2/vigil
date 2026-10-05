# -*- coding: utf-8 -*-
"""技能按渠道开关：判据 + 端到端。

## 为什么单元测试不够

技能渠道开关落在三处，任何一处不对都会表现成「界面能点、背后没改」：
  ① `channels_of/set_channels` 读写 config.json 的 skill.channels
  ② `_r_channels` 路由（包名校验、渠道名校验、写失败要说失败）
  ③ `_tools_now` 按渠道过滤 + `call` 把「渠道关着」和「没这个技能」分开回话

本文件把三处都真调一遍。**不装 Node 也能跑** —— 宿主调用那一层用假宿主顶掉，
但配置读写和过滤逻辑是真的。

用法：python _test_skill_channels.py            # 纯逻辑
      python _test_skill_channels.py --http     # 起真大脑打真 HTTP
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = [0]
FAIL = []


def check(name, ok, extra=None):
    if ok:
        PASS[0] += 1
        print("  PASS  " + name)
    else:
        FAIL.append(name)
        print("  FAIL  " + name + ("  <<< " + json.dumps(extra, ensure_ascii=False)[:300]
                                   if extra is not None else ""))


class _TmpCfg(object):
    """把 runtime.CFG_PATH 指到临时目录，不动真配置。"""

    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="zxskch_")
        self.p = os.path.join(self.d, "config.json")
        with io.open(self.p, "w", encoding="utf-8") as f:
            json.dump({"skill": {"host": "http://127.0.0.1:8790"}}, f)
        self.saved = {}
        for mod in ("runtime", "skill"):
            m = sys.modules.get(mod)
            if m is not None:
                self.saved[mod] = getattr(m, "CFG_PATH", None)
                if hasattr(m, "CFG_PATH"):
                    setattr(m, "CFG_PATH", self.p)
        import runtime
        runtime.CFG_PATH = self.p
        return self.p

    def __exit__(self, *a):
        shutil.rmtree(self.d, ignore_errors=True)
        import runtime
        if "runtime" in self.saved and self.saved["runtime"]:
            runtime.CFG_PATH = self.saved["runtime"]
        return False


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def main_logic():
    sys.path.insert(0, os.path.join(HERE, "plugins"))
    import skill
    CH = ["app", "wechat"]

    print("[1] 没配过的包 = 所有渠道都开")
    check("全新装的技能默认全开",
          skill.channels_of("brand_new", CH) == {"app": True, "wechat": True},
          skill.channels_of("brand_new", CH))

    print("[2] set_channels 只写被点的渠道")
    with _TmpCfg() as p:
        ok = skill.set_channels(["pkg_a"], CH, {"app": True, "wechat": False})
        cfg = _read(p)
        check("写成功", ok is True)
        check("config 里落的是 skill.channels",
              cfg.get("skill", {}).get("channels") == {"pkg_a": ["app"]},
              cfg.get("skill"))
        check("读回来一致",
              skill.channels_of("pkg_a", CH) == {"app": True, "wechat": False},
              skill.channels_of("pkg_a", CH))
        check("别的包不受影响",
              skill.channels_of("pkg_b", CH) == {"app": True, "wechat": True})

        print("[3] 再改一次能改回来（不是只能开不能关）")
        skill.set_channels(["pkg_a"], CH, {"app": True, "wechat": True})
        check("改成全开", skill.channels_of("pkg_a", CH) == {"app": True, "wechat": True},
              skill.channels_of("pkg_a", CH))
        skill.set_channels(["pkg_a"], CH, {"app": False, "wechat": False})
        check("两个都关",
              skill.channels_of("pkg_a", CH) == {"app": False, "wechat": False},
              skill.channels_of("pkg_a", CH))

    print("[4] 配置被写坏时不能整个崩")
    with _TmpCfg() as p:
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        check("读坏配置时按全开算（不抛）",
              skill.channels_of("x", CH) == {"app": True, "wechat": True})
    with _TmpCfg() as p:
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump({"skill": {"channels": "这不该是字符串"}}, f)
        check("channels 不是 dict 时按全开算",
              skill.channels_of("x", CH) == {"app": True, "wechat": True})
    with _TmpCfg() as p:
        with io.open(p, "w", encoding="utf-8") as f:
            json.dump({"skill": {"channels": {"x": "也不是列表"}}}, f)
        check("值不是列表时按全开算",
              skill.channels_of("x", CH) == {"app": True, "wechat": True})

    print("[5] _skill_on 认渠道，且认的是运行时那个渠道")
    with _TmpCfg() as p:
        skill.set_channels(["z"], CH, {"app": True, "wechat": False})
        import features
        features.set_current_src("app")
        check("app 里开着", skill._skill_on("z", "app") is True)
        check("wechat 里关着", skill._skill_on("z", "wechat") is False)
        features.set_current_src("wechat")
        check("省略 src 时按当前渠道算（wechat）", skill._skill_on("z") is False)
        features.set_current_src("app")
        check("省略 src 时按当前渠道算（app）", skill._skill_on("z") is True)

    print("[6] 路由 _r_channels 的三种拒绝")
    with _TmpCfg():
        r = skill._r_channels({}, {})
        check("空包名被拒且说清原因",
              r.get("ok") is False and "skill" in str(r.get("err")), r)
        r = skill._r_channels({"skill": "ok_pkg", "channels": ["qq"]}, {})
        check("不认识的渠道被拒",
              r.get("ok") is False and "qq" in str(r.get("err")), r)
        r = skill._r_channels({"skill": "ok_pkg", "channels": ["app"]}, {})
        check("正常改返回 ok 和回读状态",
              r.get("ok") is True and r.get("channels", {}).get("app") is True
              and r.get("channels", {}).get("wechat") is False, r)

    print("[7] 卸载要摘掉渠道配置（否则重装会继承旧的开关）")
    with _TmpCfg() as p:
        skill.set_channels(["gone"], CH, {"app": True, "wechat": False})
        check("摘之前是关的", skill.channels_of("gone", CH)["wechat"] is False)
        skill._drop_channels("gone")
        cfg = _read(p)
        check("config 里没这个包了了",
              "gone" not in (cfg.get("skill", {}).get("channels") or {}),
              cfg.get("skill"))
        check("摘掉之后按全开算",
              skill.channels_of("gone", CH) == {"app": True, "wechat": True})
        check("摘一个不存在的包不报错", skill._drop_channels("never_existed") is False)

    print("[8] _tools_now 按渠道过滤，_all_tools_now 不过滤")
    fake_skills = [
        {"id": "keep_pkg", "tools": [{"name": "keep__t", "title": "留"}]},
        {"id": "drop_pkg", "tools": [{"name": "drop__t", "title": "关"}]},
    ]
    with _TmpCfg() as p:
        skill.set_channels(["drop_pkg"], CH, {"app": True, "wechat": False})
        skill.list_skills = lambda timeout=8: list(fake_skills)
        import features
        features.set_current_src("app")
        t_on = skill._tools_now()
        features.set_current_src("wechat")
        t_off = skill._tools_now()
        allt = skill._all_tools_now()
        check("开着的渠道看得见两个",
              set(t_on) == {"keep__t", "drop__t"}, list(t_on))
        check("关掉的渠道只看得见留着的那个",
              set(t_off) == {"keep__t"}, list(t_off))
        check("不过滤的那份两个都在（用来分辨是哪种错）",
              set(allt) == {"keep__t", "drop__t"}, list(allt))

        print("[9] call 要把「渠道关着」和「没这个技能」分开回话")
        features.set_current_src("wechat")
        # tools() 有 60 秒缓存，这里手动清掉再走真实路径
        with skill._cache_lock:
            skill._cache["at"] = 0.0
            skill._cache["tools"] = []
        r = skill.call("drop__t", {}, None)
        check("渠道关着 → 说渠道关着，不说没这个工具",
              "不开放" in str(r.get("content")), r)
        r = skill.call("根本没有这个", {}, None)
        check("真没有 → 说没有这个技能工具",
              "没有这个技能工具" in str(r.get("content")), r)
        features.set_current_src("app")

    print("\n== %d pass / %d fail ==" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("   FAILED: " + f)
    return 1 if FAIL else 0


def main_http():
    """起真大脑打真 HTTP。地址和 token 走环境变量。"""
    host = os.environ.get("ZX_API", "http://203.0.113.10:8788")
    tok = os.environ.get("ZX_TOK", "")
    if not tok:
        print("需要 ZX_TOK")
        return 2
    import urllib.request as u
    op = u.build_opener(u.ProxyHandler({}))

    def post(path, body):
        r = u.Request(host + path, method="POST",
                      data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json",
                               "Authorization": "Bearer " + tok})
        with op.open(r, timeout=90) as f:
            return json.loads(f.read().decode("utf-8"))

    print("[H1] status 带 channels 字段")
    st = post("/api/skill/status", {})
    if not st.get("alive"):
        print("  SKIP 宿主没起来，后面几条没意义")
        return 2
    check("status.ok", st.get("ok") is True)
    check("status 给出渠道清单", bool(st.get("channels")), st.get("channels"))
    sk = st.get("skills") or []
    check("每个技能都带 channels 和 channelable",
          all("channels" in s and "channelable" in s for s in sk),
          [list(s) for s in sk])
    if not sk:
        print("  SKIP 一个技能都没装")
        return 0
    pkg = sk[0].get("id") or ""
    print("[H2] 改 %s 的渠道开关，再读回来" % pkg)
    was = dict(sk[0].get("channels") or {})
    try:
        r = post("/api/skill/channels", {"skill": pkg, "channels": ["wechat"]})
        check("只留微信 → ok", r.get("ok") is True, r)
        check("回读状态：微信开、App 关",
              (r.get("channels") or {}).get("wechat") is True
              and (r.get("channels") or {}).get("app") is False, r)
        st2 = post("/api/skill/status", {})
        s2 = [x for x in (st2.get("skills") or []) if x.get("id") == pkg]
        check("再拉一次也是微信开 App 关",
              bool(s2) and s2[0]["channels"].get("wechat") is True
              and s2[0]["channels"].get("app") is False,
              s2[0] if s2 else None)
    finally:
        want = [k for k, v in was.items() if v]
        post("/api/skill/channels", {"skill": pkg, "channels": want})
    print("[H3] 恢复原状读回")
    st3 = post("/api/skill/status", {})
    s3 = [x for x in (st3.get("skills") or []) if x.get("id") == pkg]
    check("恢复成功", bool(s3) and s3[0]["channels"] == was,
          (s3[0]["channels"] if s3 else None, was))

    print("\n== %d pass / %d fail ==" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("   FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_http() if "--http" in sys.argv else main_logic())