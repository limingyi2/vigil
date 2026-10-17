# -*- coding: utf-8 -*-
"""Vigil Creator 的最小聊天工具插件示例。

这个插件提供一个可被模型在聊天中调用的状态工具。
TOOLS 负责执行；TOOL_SPECS 负责让原生 function-calling 模型看见它。
"""

NAME = "creator_example"
DESC = "Creator 插件聊天调用示例"


def _creator_example_status(_arg):
    """验证插件已安装并可通过聊天调用。"""
    return "Creator 示例插件已加载，聊天工具可以正常调用。"


TOOLS = {
    "creator_example_status": _creator_example_status,
}

TOOL_SPECS = {
    "creator_example_status": {
        "desc": "检查 Creator 示例插件是否已安装并能通过聊天调用。用户询问示例插件是否生效时使用。",
        "params": [],
        "arg_param": None,
    },
}

TOOL_HELP_NATIVE = (
    "Creator 示例插件提供一个状态检查工具。用户询问示例插件是否安装或能否被聊天调用时使用。"
)


def prompt(ctx):
    """告诉模型这个插件提供什么能力；真正执行仍通过工具调用。"""
    return "你有一个 Creator 示例插件状态工具，可在用户询问示例插件是否生效时调用。"
