# -*- coding: utf-8 -*-
"""技能管理接口的真跑冒烟：起大脑 -> 打真 HTTP -> 收摊，一条命令跑完。

    python _test_skill_admin_http.py                 # 本机回环，不带假宿主
    python _test_skill_admin_http.py --with-host     # 顺带起假宿主，装卸也真跑
    python _test_skill_admin_http.py <base> <token>  # 打云端（token 别留在历史里）

## 为什么必须一个进程里跑完

跑测试的 shell 一退出，被它带着起的 `server.py` 一起没。表现极具误导性：
日志里明明打印了「远程大脑 API 就绪：127.0.0.1:8788」，紧接着打接口
就是 `WinError 10061 目标计算机积极拒绝`。会浪费很久去怀疑"是不是启动有
什么偶发问题"，而真相只是进程没活过父进程。所以这个脚本自己起、自己收。

## 为什么带一个假宿主

没装 Node 的机器上，"真装一个技能 / 真卸一个"这两组只能跳过 —— 而它们恰好
是最容易错的：包根判断、备份不覆盖、装完打 /reload、卸完留 .bak_ 全在
`_install_zip` / `_uninstall` 里。`--with-host` 起一个照 `server.js` 契约
实现的假宿主（`_fake_skill_host.py`），不下载任何东西。
它验的是**大脑跟宿主之间的协议**，不是 server.js 本身。

## 会不会改坏本机 config

`--with-host` 要把 `config.json` 的 `skill.host` 指到假宿主，随手改配置
是不可逆的那种事（下次大脑起来还指着那个死端口，技能静默失效）。所以：
先备份、只改这一个键、跑完无条件还原，还原后**逐字节比对**。
"""

import base64
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
# 🔴 单反斜杠。这里的常量是写进 Python 源码的，不经过 cmd，
# 但仍要跟仓库里其它脚本一致 —— 写成双反斜杠会在 Windows 上被当 UNC。
VENV_PY = os.path.normpath("F:/vigil/venv/Scripts/python.exe")

ARGS = sys.argv[1:]
WITH_HOST = "--with-host" in ARGS
ARGS = [a for a in ARGS if a != "--with-host"]
BASE = ARGS[0] if ARGS else "http://127.0.0.1:8788"
EXTERNAL = bool(ARGS)          # 打的是别处就不能起本地大脑、不能改配置

_OK = 0
_BAD = 0
_SKIP = 0


def check(label, cond, extra=""):
    global _OK, _BAD
    if cond:
        _OK += 1
        print("  PASS  %s" % label)
    else:
        _BAD += 1
        print("  FAIL  %s%s" % (label, ("\n        " + str(extra)) if extra else ""))


def skip(label, why):
    global _SKIP
    _SKIP += 1
    print("  SKIP  %s（%s）" % (label, why))


def group(t):
    print("\n[%s]" % t)


def default_token():
    p = os.path.join(HERE, "config", "config.json")
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            return str(json.load(f).get("brain_token") or "")
    except Exception:
        return ""


TOK = ARGS[1] if len(ARGS) > 1 else default_token()


def raw(method, path, body=None, timeout=25):
    url = BASE + path
    sep = "&" if "?" in path else "?"
    url += sep + "token=" + TOK
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        code = e.code
    except Exception as e:
        return -1, str(e)
    try:
        return code, json.loads(txt)
    except Exception:
        return code, txt


def mkzip_b64(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, v in entries.items():
            z.writestr(n, v if isinstance(v, bytes) else v.encode("utf-8"))
    return base64.b64encode(buf.getvalue()).decode("ascii")


SKILL_IDX = """export function setup(api) {
  api.registerTool({
    id: 'hello', name: 'hello', description: '打招呼',
    parameters: {}, async execute(ctx, args) { return 'hi'; }
  });
}
"""


def port_open(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def wait_brain(proc=None, upto=50):
    for _ in range(upto):
        if proc is not None and proc.poll() is not None:
            return False
        try:
            if raw("GET", "/api/plugins", timeout=4)[0] == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def wait_host(upto=14):
    for _ in range(upto):
        d = raw("POST", "/api/skill/status", {}, timeout=6)[1]
        if isinstance(d, dict) and d.get("alive"):
            return True
        time.sleep(0.5)
    return False


def patch_cfg(host_url):
    """把 config.json 的 skill.host 指到别处，返回还原函数。

    只动这一个键，其余键连顺序都不碰（json 读写保不住原格式，
    所以用**文本替换**而不是重新 dump —— 重新 dump 会把整个文件重排，
    diff 一片红，下次谁都不敢碰这个文件了）。
    """
    p = os.path.join(HERE, "config", "config.json")
    orig = io.open(p, encoding="utf-8").read()
    fd, bak = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    shutil.copyfile(p, bak)

    m = re.search(r'("skill"\s*:\s*\{)([^}]*)(\})', orig)
    if not m:
        # 本机 config 里还没有 skill 段 —— 插一个，别改其他任何键
        ins = ',\n  "skill": {"host": "%s"}' % host_url
        idx = orig.rindex("}")
        new = orig[:idx] + ins + orig[idx:]
    else:
        body = m.group(2)
        if re.search(r'"host"\s*:', body):
            newbody = re.sub(r'"host"\s*:\s*"[^"]*"',
                             '"host": "%s"' % host_url, body)
        else:
            newbody = (body.rstrip().rstrip(",")
                       + ', "host": "%s"' % host_url)
        new = orig[:m.start(2)] + newbody + orig[m.end(2):]
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(new)

    def restore():
        shutil.copyfile(bak, p)
        try:
            os.remove(bak)
        except Exception:
            pass
        # 逐字节比对：还原必须是真的还原，不是"看着像"
        same = io.open(p, "rb").read() == orig.encode("utf-8")
        print("    config 已还原：%s（逐字节%s）"
              % (p, "一致" if same else "不一致 ← 这是 bug"))
        return same

    return restore


def run_suite():
    print("=" * 52)
    print("技能管理接口真跑冒烟（打 %s）" % BASE)
    print("=" * 52)

    if not wait_brain(PROC[0]):
        print("起不来大脑。")
        return 1

    host_up = wait_host()
    print("\n宿主状态：%s" % ("活着" if host_up else "没起来（没装 Node 时属正常）"))

    group("1. 四条路由都注册了（404 = 没注册，不是'没装技能'）")
    st = None
    for r in ("/api/skill/status", "/api/skill/install",
              "/api/skill/uninstall", "/api/skill/reload"):
        code, d = raw("POST", r, {})
        check("%s 有响应" % r, code != 404, "code=%s body=%s" % (code, d))
        if r == "/api/skill/status":
            st = d
    check("不存在的路由确实回 404（面板据此显示'读不到状态'）",
          raw("POST", "/api/skill/nope", {})[0] == 404)

    group("2. /api/skill/status 的形状")
    check("返回 dict", isinstance(st, dict), st)
    if not isinstance(st, dict):
        return 1
    for k in ("ok", "host", "alive", "dir", "skills", "ext", "node"):
        check("有键 %s" % k, k in st, sorted(st.keys()))
    check("alive 是 bool", isinstance(st.get("alive"), bool), repr(st.get("alive")))
    check("skills 是列表", isinstance(st.get("skills"), list), repr(st.get("skills"))[:200])
    check("ext 是列表", isinstance(st.get("ext"), list), repr(st.get("ext")))
    check("host 只认回环（不是公网）",
          bool(re.match(r"^http://(127\.0\.0\.1|localhost)", str(st.get("host") or ""))),
          st.get("host"))
    check("ext 不含 .py（.py 归插件通道）", ".py" not in (st.get("ext") or []),
          st.get("ext"))
    check("ext 含 .js", ".js" in (st.get("ext") or []), st.get("ext"))

    # 3. 原来这里拿 admin/dashboard.html 当"消费方契约"，逐个 u0040st.X 核对后端有没
    #    有给。那个面板随 App/微信渠道一起在 P21.2 下线了；现在的消费方是 Electron
    #    客户端，契约由 tests/ 里的接口测试守。

    group("4. 参数校验（跟宿主活不活无关，任何时候都得对）")
    z = mkzip_b64({"smoke/smoke/index.js": SKILL_IDX,
                   "smoke/smoke/skill.json": '{"name":"冒烟"}'})
    for body, why in (({}, "空包名"),
                      ({"skill": "../../etc"}, "路径穿越"),
                      ({"skill": "a/b"}, "带斜杠"),
                      ({"skill": ""}, "空串"),
                      ({"skill": "CON"}, "Windows 保留名"),
                      ({"skill": ".hidden"}, "点开头")):
        d = raw("POST", "/api/skill/uninstall", body)[1]
        check("uninstall 拒 %s" % why,
              isinstance(d, dict) and d.get("ok") is False, d)
    d = raw("POST", "/api/skill/uninstall", {})[1]
    check("理由说清了要哪个字段",
          isinstance(d, dict) and "skill" in str(d.get("err") or ""), d)
    d = raw("POST", "/api/skill/install", {})[1]
    check("install 缺 zip_b64 被拒",
          isinstance(d, dict) and d.get("ok") is False, d)

    if not host_up:
        group("5. 宿主没起来时的 install")
        d = raw("POST", "/api/skill/install", {"zip_b64": z})[1]
        check("拒了（不许装上）", isinstance(d, dict) and d.get("ok") is False, d)
        check("理由提到宿主或 Node",
              isinstance(d, dict) and re.search(r"宿主|Node", str(d.get("err") or "")),
              d)
        skip("6. 真装一个技能", "宿主没起来")
        skip("7. 卸掉", "宿主没起来")
    else:
        group("5. 真装一个技能")
        before = raw("POST", "/api/skill/reload", {})[1]
        d = raw("POST", "/api/skill/install", {"zip_b64": z})[1]
        check("装上了", isinstance(d, dict) and d.get("ok") is True, d)
        pkg = (d or {}).get("skill")
        check("返回包名", bool(pkg), d)
        check("返回文件数", isinstance((d or {}).get("files"), int), d)
        check("没报重载失败", not (d or {}).get("warn"), d)

        st2 = raw("POST", "/api/skill/status", {})[1]
        ids = [s.get("id") for s in (st2 or {}).get("skills") or []]
        check("装完列表里看得见", pkg in ids, ids)
        one = next((s for s in (st2 or {}).get("skills") or []
                    if s.get("id") == pkg), None)
        check("技能名从 skill.json 取到了", (one or {}).get("name") == "冒烟", one)
        check("工具名是全名（带技能前缀）",
              any(t.get("name", "").startswith(pkg + ".")
                  for t in (one or {}).get("tools") or []),
              (one or {}).get("tools"))

        sk_dir = (st2 or {}).get("dir") or ""
        check("技能目录真落地了", os.path.isdir(os.path.join(sk_dir, pkg or "")),
              sk_dir)

        group("6. 同名重装：旧版留备份，不覆盖")
        z2 = mkzip_b64({"smoke/smoke/index.js": SKILL_IDX + "\n// v2\n",
                        "smoke/smoke/skill.json": '{"name":"冒烟2"}'})
        d = raw("POST", "/api/skill/install", {"zip_b64": z2})[1]
        check("重装成功", isinstance(d, dict) and d.get("ok") is True, d)
        baks = [n for n in (os.listdir(sk_dir) if os.path.isdir(sk_dir) else [])
                if n.startswith((pkg or "") + ".bak_")]
        check("旧版留成了 .bak_（没被覆盖）", len(baks) == 1, os.listdir(sk_dir))
        with io.open(os.path.join(sk_dir, pkg, "index.js"), encoding="utf-8") as f:
            check("新内容真换上去了", "// v2" in f.read(), f)

        group("7. 卸掉（只挪走不删）")
        d = raw("POST", "/api/skill/uninstall", {"skill": pkg})[1]
        check("卸掉了", isinstance(d, dict) and d.get("ok") is True, d)
        st3 = raw("POST", "/api/skill/status", {})[1]
        ids = [s.get("id") for s in (st3 or {}).get("skills") or []]
        check("列表里没了", pkg not in ids, ids)
        left = os.listdir(sk_dir) if os.path.isdir(sk_dir) else []
        check("目录还在（改名不是删）",
              any(n.startswith(pkg + ".bak_") for n in left), left)

        # 收摊：把冒烟装的东西全清掉，别在技能目录留垃圾
        if os.path.isdir(sk_dir):
            for n in os.listdir(sk_dir):
                if n.startswith("smoke"):
                    shutil.rmtree(os.path.join(sk_dir, n), ignore_errors=True)

    print("\n" + "=" * 52)
    print("PASS %d  FAIL %d  SKIP %d" % (_OK, _BAD, _SKIP))
    print("=" * 52)
    return 1 if _BAD else 0


PROC = [None]

if __name__ == "__main__":
    srv = host = None
    restore = None
    skill_dir = None
    try:
        if not EXTERNAL:
            if WITH_HOST:
                spec = importlib.util.spec_from_file_location(
                    "fhost", os.path.join(HERE, "_fake_skill_host.py"))
                fh = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(fh)
                skill_dir = tempfile.mkdtemp(prefix="zxskills_")
                host = fh.FakeHost(skill_dir).start()
                print("[0] 假宿主已起：127.0.0.1:%d  dir=%s"
                      % (host.port, skill_dir))
                # 🔴 宿主和大脑必须看**同一个**技能目录，只改 config 的 host
                # 是不够的。踩过：假宿主指着临时目录、大脑指着默认的
                # brain/skill_host/skills —— 文件照装成功（install 返回 ok:true），
                # 但宿主重扫自己的目录当然看不见新技能，于是
                # "装完列表里看得见" 判据失败，而真因在测试自己身上。
                # 只设 host 不设 ZX_SKILL_DIR 时，install 会 warn「重载失败」，
                # 那条 warn 就是这个错的自证信号。
                os.environ["ZX_SKILL_DIR"] = skill_dir
                restore = patch_cfg("http://127.0.0.1:%d" % host.port)
            print("[1] 起大脑", flush=True)
            logname = os.path.join(HERE, "_smoke_srv.log")
            log = open(logname, "wb")
            srv = subprocess.Popen([VENV_PY, "server.py"], cwd=HERE,
                                   stdout=log, stderr=log,
                                   stdin=subprocess.DEVNULL)
            PROC[0] = srv
        print("[2] 跑冒烟", flush=True)
        rc = run_suite()
    finally:
        if srv is not None:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except Exception:
                srv.kill()
            print("[3] 大脑已收摊")
        if host is not None:
            host.stop()
            os.environ.pop("ZX_SKILL_DIR", None)
            if skill_dir and os.path.isdir(skill_dir):
                shutil.rmtree(skill_dir, ignore_errors=True)
            print("[4] 假宿主已收摊")
        if restore is not None:
            restore()
    sys.exit(rc)
