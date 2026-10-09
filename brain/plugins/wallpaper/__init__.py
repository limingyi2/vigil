# -*- coding: utf-8 -*-
"""示例插件：桌面壁纸背景（验证前端注入链路）

目录型插件：同目录下的 frontend.js / frontend.css 会被客户端自动注入。
装上后：导航栏自动多出「壁纸」页，界面背景出现淡紫色渐变。
"""
NAME = "wallpaper"
DESC = "桌面壁纸背景（前端注入示例）"
FEATURE = "wallpaper"

ROUTES = [
    ("GET", "/api/plugin/wallpaper/hello",
     lambda req: {"ok": True, "msg": "wallpaper 插件后端正常"}),
]
