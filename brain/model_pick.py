# -*- coding: utf-8 -*-
"""按"模型和 base 必须同家"的规则挑一组可用的接口配置。

原来是 gate 插件里的私有实现（发言闸门判定用它），后来 planner 也复用了同一份
（规划/检查用的便宜模型同样会踩这个坑）。闸门在 P21.2 下线后，这份实现只剩
planner 一个消费者，就搬到这里，别在 planner 里再抄一遍 —— 抄两遍必漂移。

典型事故（实测 2026-10-07）：主对话切到小米后，planner/gate 的 model 还留着
`deepseek-ai/...`，小米那边直接回 400 Unsupported model。
"""
import json


def _model(api_config, cfg):
    """用哪个模型。cfg 里显式配了就用它，否则跟主模型。"""
    return str(cfg.get("model") or api_config.get("model") or "")


# 模型 id → 它属于哪家。按 id 关键字判：`deepseek-*` 只可能在 DeepSeek 官方
# 或硅基流动（硅基是全集），`mimo-*` 只在小米，`zai-org/GLM-*` 只在智谱/硅基。
# 兜底策略：认不出来的就算"当前家也行"——宁可同家撞一次 400（会被 except 放行），
# 也别把能用的配置判成无家可归。
_MODEL_HOME_HINTS = {
    "xiaomi": ("mimo",),
    "siliconflow": ("deepseek-ai/", "zai-org/", "qwen/", "glm-", "moonshotai/",
                    "stepfun-ai/", "tencent/", "meituan-", "baichuan-", "fnlp/"),
}


def _model_fits(model, provider_id):
    """这个模型 id 看起来是不是 provider_id 家的。"""
    m = str(model or "").lower()
    hints = _MODEL_HOME_HINTS.get(provider_id)
    if not hints:
        return True                      # custom 没有清单，放行
    if any(h in m for h in hints):
        return True                      # 关键字命中某家（兜底清单里有才命中这里）
    # 命中了**别家**的独占关键字 → 不属于这家
    for other, hs in _MODEL_HOME_HINTS.items():
        if other != provider_id and any(h in m for h in hs):
            return False
    return True                          # 认不出来：按属于这家处理


def _judge_api(api_config, cfg):
    """判定/规划用的 (base, key, model)。**模型必须和 base 同家**，不同家就是 400。

    规则（都拿 config.json 判）：
      1. cfg.model 在当前 base 的家 → 原样用。
      2. 不在 → 找一家**存了 key 且模型属于它**的 provider，换那家的 base/key。
      3. 找不到就退化成"主模型 + 主 base"（同源，至少不会 400）。
    """
    model = _model(api_config, cfg)
    base = str(api_config.get("api_base") or "").rstrip("/")
    key = str(api_config.get("api_key") or "")
    if not model or not base:
        return base, key, model
    try:
        from model_hub import PROVIDERS, provider_key, has_provider_key
    except Exception:
        return base, key, model
    # 当前 base 属于哪家
    home = ""
    for pid, meta in PROVIDERS.items():
        if pid == "custom":
            continue
        if str(meta.get("base") or "") and base == str(meta["base"]).rstrip("/"):
            home = pid
            break
    if home and _model_fits(model, home):
        return base, key, model
    # 不同家：翻配置里存过 key 的 provider，找一个模型认得的
    try:
        from runtime import CFG_PATH
        cfg_all = json.load(open(CFG_PATH, encoding="utf-8"))
    except Exception:
        cfg_all = {}
    for pid in PROVIDERS:
        if pid == "custom" or not has_provider_key(cfg_all, pid):
            continue
        if _model_fits(model, pid):
            b = str(PROVIDERS[pid]["base"]).rstrip("/")
            return b, provider_key(cfg_all, pid), model
    # 找不到同家的：退回主模型（主模型和主 base 永远同源）
    return base, key, str(api_config.get("model") or "")
