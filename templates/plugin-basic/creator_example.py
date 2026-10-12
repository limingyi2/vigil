# -*- coding: utf-8 -*-
"""Vigil Creator Mode 的最小可运行插件模板。

这个模板只声明插件身份并提供一个无副作用的提示钩子；不联网、不写文件、
不声明权限，也不会自动安装或执行第三方代码。
"""

NAME = "creator_example"
DESC = "Creator Mode 最小示例插件"


def prompt(ctx):
    """返回静态提示，证明插件已经被加载。"""
    return "Creator example plugin is loaded."
