# -*- coding: utf-8 -*-
"""JS 技能宿主的验收：Node 侧 + Python 插件侧，全链路实跑。

跑法（宿主要先起来）：
    node brain/skill_host/server.js
    ./venv/Scripts/python.exe brain/_test_skill_host.py http://127.0.0.1:8790

判据全是"实跑出来的"，不是看代码推的 —— 上一版就是靠看代码以为没问题，
实跑才发现档位传字符串会导致闸门全失效（NaN 走进作者那条"没给档位就不拦"
的兜底）。**改完宿主一定要真跑一遍。**
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 本机 HTTP_PROXY 有值，而宿主在 127.0.0.1：不关掉的话 urllib 会去问代理，
# 代理连不上本机端口，最后报"连不上宿主" —— 看着像宿主挂了，其实是被代理吃了。
for _k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ[_k] = ""

HOST = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790").rstrip("/")
TOOL = "daily-fortune__today"

_ok = 0
_bad = 0


def check(name, cond, extra=""):
    global _ok, _bad
    if cond:
        _ok += 1
        print("  PASS  %s" % name)
    else:
        _bad += 1
        print("  FAIL  %s" % name)
        if extra:
            print("        -> %s" % str(extra)[:200])


def group(t):
    print("\n%s" % t)


class Turn(object):
    """假 Turn。只提供插件真正读到的那几个字段。

    🔴 brain 给 None 是**故意的**：真人设昵称读不到（脑是 None），
    正好暴露"判据只依赖人设配置会漏"这个真问题 —— 上一版就是这么挂的。
    """
    src = "wechat"
    text = ""
    brain = None


def main():
    import plugins.skill as S
    S._cfg = lambda: {"host": HOST}          # 指到测试端口
    S._CACHE_TTL = 0                          # 别让缓存掩盖变化

    group("0. 宿主活着")
    alive = S.host_alive()
    check("宿主在 %s 上应答" % HOST, alive)
    if not alive:
        print("\n宿主没起来，先 node brain/skill_host/server.js 再跑。")
        return 1

    tools = S.tools()
    check("认出了技能工具", bool(tools), tools)
    if not tools:
        return 1
    check("工具清单里有 %s" % TOOL, TOOL in tools, list(tools))

    group("1. 档位：群里没点名必须被拒")
    t = Turn(); t.text = "今天运势怎么样"
    r = S.skill_call(t, name=TOOL, args=json.dumps({"who": "张三"}))
    check("群里没点名 -> 拒", r.startswith("这次在群里"), r)
    check("拒答文案带「当作没发生」这种交代", "当作没发生" in r, r)

    group("2. 档位：被点名 / 私聊要放行")
    t = Turn(); t.text = "@助手 今天运势怎么样"
    r = S.skill_call(t, name=TOOL, args=json.dumps({"who": "张三"}))
    check("群里@她 -> 放行", "签位" in r, r)

    t = Turn(); t.text = "拍了拍助手"
    r = S.skill_call(t, name=TOOL, args=json.dumps({"who": "张三"}))
    check("群里拍一拍 -> 放行", "签位" in r, r)

    t = Turn(); t.text = "@全体成员 今日运势"
    r = S.skill_call(t, name=TOOL, args=json.dumps({"who": "张三"}))
    check("@全体成员 -> 放行", "签位" in r, r)

    # 换人设后昵称变了，但正文里明确@了 —— 判据不能只认配置里的昵称
    t = Turn(); t.text = "@麦麦 运势呢"
    r = S.skill_call(t, name=TOOL, args=json.dumps({"who": "张三"}))
    check("换个昵称也认（换人设场景）", "签位" in r, r)

    t = Turn(); t.src = "app"; t.text = "今天运势怎么样"
    r = S.skill_call(t, name=TOOL, args=json.dumps({"who": "用户"}))
    check("App 私聊 -> 放行", "签位" in r, r)

    group("3. 抽签内容")
    t = Turn(); t.src = "app"
    r = S.skill_call(t, name=TOOL, args=json.dumps({"who": "用户"}))
    for field in ("签位", "宜", "忌", "幸运"):
        check("结果里有「%s」" % field, field in r, r[:150])

    group("4. 确定性（同一天同一人固定，换人/换天不同）")
    t = Turn(); t.src = "app"
    a1 = S.skill_call(t, name=TOOL, args=json.dumps({"who": "用户"}))
    a2 = S.skill_call(t, name=TOOL, args=json.dumps({"who": "用户"}))
    check("问两次完全一致", a1 == a2)
    b = S.skill_call(t, name=TOOL, args=json.dumps({"who": "张三"}))
    check("换人结果不同", a1 != b)
    c = S.skill_call(t, name=TOOL, args=json.dumps({"who": "用户", "day": "tomorrow"}))
    check("看明天不同", a1 != c)

    group("5. 出错时给人话，不能崩")
    t = Turn(); t.src = "app"
    r = S.skill_call(t, name=TOOL, args="{坏的}")
    check("坏 JSON -> 有提示", "不是合法 JSON" in r, r)
    r = S.skill_call(t, name=TOOL, args='"一个字符串"')
    check("参数不是对象 -> 有提示", "JSON 对象" in r, r)
    r = S.skill_call(t, name="daily-fortune__不存在", args="")
    check("未知工具 -> 有说明", "没有这个技能工具" in r, r)
    r = S.skill_call(t, name="", args="")
    check("没说调哪个 -> 有提示", "没说要调" in r, r)

    group("6. skill_list（模型先看有什么）")
    r = S.skill_list(Turn())
    check("列出了 %s" % TOOL, TOOL in r, r[:200])
    r = S.skill_list(Turn())
    check("说了怎么用", "skill_call" in r, r[:200])

    group("7. 宿主走了要能降级，不能把大脑带崩")
    S._cfg = lambda: {"host": "http://127.0.0.1:1"}   # 一定连不上的端口
    S._CACHE_TTL = 0
    check("host_alive 返回 False", S.host_alive() is False)
    r = S.skill_call(Turn(), name=TOOL, args="{}")
    check("调不动时返回人话而不是抛异常", isinstance(r, str) and len(r) > 0, r)

    print("\n通过 %d / %d" % (_ok, _ok + _bad))
    return 0 if _bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
