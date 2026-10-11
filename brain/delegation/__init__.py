# -*- coding: utf-8 -*-
"""混合模式（hybrid）：云端主模型把**有界只读子任务**委派给本机 Ollama。

设计依据：vigil-hybrid-model-v1-plan.md 第 4~6 节。第一版边界：

- 只做一个受控工具 `delegate_to_local`，挂在 hybrid 模式主模型的工具表里；
- 主模型可以**建议** kind/goal/expected_schema/input_refs，但权限、沙箱、
  限额全部由本包代码决定，模型建议不等于授权；
- 子任务只跑方案第 5 节的 5 个只读 kind，不开放写/Shell/删除/网络外发；
- Dispatcher 是进程内的有界循环（请求数/工具数/墙钟三重熔断 + 一次重试），
  不新起服务、不复制一套 Brain。

模块划分：
  contracts.py  合同校验、kind 白名单与限额、workspace 归属
  sandbox.py    只读沙箱工具（list/find/read/grep，全部 safe_path 收口）
  validator.py  结构化结果 + 证据文件真实性校验
  dispatcher.py 有界 Ollama 子循环、熔断、重试、状态归档
"""
from .contracts import (
    KINDS, KIND_NAMES, ABS_LIMITS, ContractError,
    validate_contract, load_delegation_config,
)
from .dispatcher import run_delegation, DelegationResult

__all__ = [
    "KINDS", "KIND_NAMES", "ABS_LIMITS", "ContractError",
    "validate_contract", "load_delegation_config",
    "run_delegation", "DelegationResult",
]
