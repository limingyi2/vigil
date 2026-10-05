# -*- coding: utf-8 -*-
"""临时验证：react 插件修复后，路由注册 / 思维链解析 / on_reply 剥离 / 落盘是否正常。

跑完会把自己写入的测试记录从当天 jsonl 里清掉，不污染真实留痕。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "plugins"))

# 1) 模块能导入
import react          # noqa: E402
import skill          # noqa: E402
import plugin_host    # noqa: E402

# 2) 插件宿主加载后，react 的两个路由要进路由表
plugin_host.load()
tbl = plugin_host.route_table()
paths = {p for (m, p, fn, owner) in tbl if owner == "react"}
print("react 注册到的路由：", sorted(paths))
assert "/api/react/stats" in paths, "stats 路由没注册上"
assert "/api/react/recent" in paths, "recent 路由没注册上"

# 3) 思维链解析：think + 工具标签 + 正文
raw = "<think>我要查实时温度，不能凭记忆</think>\n[tool:weather:北京]\n北京现在3度，冻得慌。"
think, tools, rest = react.parse_react(raw)
print("解析 -> think=%r tools=%r rest=%r" % (think, tools, rest))
assert think == "我要查实时温度，不能凭记忆"
assert tools == [("weather", "北京")]
assert "冻得慌" in rest

# 只有工具标签、没有 think 的老格式也得剥
think2, tools2, rest2 = react.parse_react("[tool:weather:杭州]\n杭州5度")
print("老格式 -> tools=%r rest=%r" % (tools2, rest2))
assert tools2 == [("weather", "杭州")]

# 4) skill 目录能算出来（不再走异常兜底）
print("skill 目录：", skill._skill_dir())

# 5) on_reply 完整链路：临时把开关打开，think 必须被剥掉、记录要落盘
react._cfg = lambda: {"enabled": True}


class Ctx(object):
    text = "北京天气【测试标记】"
    src = "app"


out = react.on_reply(Ctx(), raw)
print("on_reply 返回：%r" % out)
assert "<think>" not in out, "think 泄漏到回复里了"
assert "[tool:" not in out, "工具标签泄漏到回复里了"

d = react._data_dir()
day_file = os.path.join(d, time.strftime("%Y-%m-%d") + ".jsonl")
assert os.path.exists(day_file), "思维链没落盘"
print("落盘文件：", day_file, "大小：", os.path.getsize(day_file))

# 6) 两个 HTTP handler 直接调（宿主就是这样调的：fn(body, query)）
s = react._r_stats({}, {})
r = react._r_recent({"limit": 5}, {})
print("stats handler ok=%s, recent handler ok=%s, items=%d"
      % (s.get("ok"), r.get("ok"), len(r.get("items") or [])))
assert s.get("ok") and r.get("ok")

# 7) 清掉本脚本写入的测试记录
if os.path.exists(day_file):
    with open(day_file, encoding="utf-8") as f:
        kept = [ln for ln in f if "【测试标记】" not in ln]
    with open(day_file, "w", encoding="utf-8") as f:
        f.writelines(kept)
    print("已清理测试记录")

print("ALL PASS")
