# -*- coding: utf-8 -*-
"""模型中枢：服务商、模型分类、当前配置的读写。

模型调用原来散在三个地方、各读各的配置字段（brain.py 读 model、vision.py 读
vision.model、server.py 里把生图模型写死），App 想换个模型就得同时改三处，还容易漏。
抽到这里之后："现在用哪个模型"只有一份定义，分类规则也只有一份。

几类模型（App 里就是这几个下拉）：
chat 对话   vision 看图（把图翻译成一句中文给她看）   image 生图（自拍）
audio 语音（还没做，位置先留着）   embed 向量（记忆检索用，App 里不显示）

分类从哪来：先问服务商 /v1/models 要真实列表，按名字关键字归类；拉不到（没网 /
key 错了 / 平台改版）就用内置兜底清单，并在返回值里标 source=fallback ——
App 会提示"这是内置清单，可能不全"。
"""
import os
import json
import re
import time
import urllib.request

try:
    from paths import CONFIG_PATH, DATA_DIR
except Exception:                      # 单独跑这个文件时的兜底
    CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "config", "config.json")
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data")

CACHE_PATH = os.path.join(DATA_DIR, "run", "model_cache.json")
CACHE_TTL = 6 * 3600                   # 模型名单不会一天变三回，缓存 6 小时

# 服务商：后面要加别家（比如智谱、 moonshot），往这里加一条就行
PROVIDERS = {
    "siliconflow": {
        "name": "硅基流动",
        "base": "https://api.siliconflow.cn/v1",
        "models_doc": "https://docs.siliconflow.cn",
    },
    "xiaomi": {
        "name": "小米 MiMo",
        "base": "https://api.xiaomimimo.com/v1",
        "models_doc": "https://mimo.mi.com/docs",
    },
    "custom": {
        "name": "自定义（OpenAI 兼容）",
        "base": "",
        "models_doc": "",
    },
}

# 分类关键字（都对小写后的模型 id 做包含匹配）。
# 坑：别用 "turbo" 判生图 —— Qwen 一堆 -Turbo 是语言模型，会全错分。
_IMAGE_HINTS = ("kolors", "flux", "stable-diffusion", "sdxl", "sd3",
                "z-image", "zimage", "hidream", "playground", "wanx",
                "dall-e", "gpt-image", "recraft", "kandinsky", "lumina",
                "imagen", "majicflus", "sd-turbo", "sana",
                # 通义自己的文生图/编辑模型就叫 Qwen-Image —— 名字里没有
                # flux/kolors 那种关键字，不加这条会被当成对话模型列进对话栏
                "qwen-image")
_AUDIO_HINTS = ("whisper", "sensevoice", "funaudio", "fish-speech",
                "fishspeech", "indextts", "maskgct", "step-tts", "tts",
                "asr", "voice", "audio", "speech", "emotion2vec")
_VISION_HINTS = ("-vl", "vl-", "vision", "internvl", "minicpm-v", "llava",
                 "glm-4v", "cogvlm", "omni", "qwen-vl", "yi-vl")
# 智谱视觉版这两年改过命名：老的是 glm-4v，现在是 glm-4.5v / glm-4.1v 这种
# "版本号后面直接跟 v" 的写法 —— 子串关键字根本钉不住（要穷举到哪年？），
# 所以这里用一条正则兜住 glm-<版本号>v 这一种形状。
_GLM_VISION_RE = re.compile(r"glm-[\d.]+v\b")
_EMBED_HINTS = ("bge-", "bce-", "gte-", "jina-", "embedding", "rerank",
                "reranker", "text-embedding")

# 兜底清单：在线拉不到时给 App 用，至少能选。
#
# 2026-10-06 晚按真目录整表重刷：用硅基流动自己的 key 打 /v1/models 拉到
# **97 个真实 id**（全量存 C:\Vigil\_sf_catalog.txt），再逐类抽样实测 ——
# GLM-5.3 / DeepSeek-V4-Flash / Qwen3.6-35B-A3B 文字对话 OK，
# Qwen3-VL-30B/32B/8B、GLM-4.5V 带图 OK。
# 小米那几���是同天拿小米的 key 打它自己的 /v1/models 验证的（小米一共就 9 个 id：
# 5 个对话 + 1 个 asr + 3 个 tts）。文档里写的 mimo-v2-flash / mimo-7b-instruct
# 在真实列表里并不存在，别照文档抄。
# 2026-10-07 二次订正：mimo v2.6 全系 + v2.5 是**原生全模态**，能看图（官方
# 文档 + 实测，usage 里有 image_tokens）——之前写"小米没有任何 VL"是错的。
# 错因：mimo 思考默认开启，探测时 max_tokens 只给 40，全被推理吃掉、正文为空，
# 看着像"不支持图"。**拿 mimo 当看图模型必须把 max_tokens 给大**（>=1024）。
# 另：mimo-v2.5-pro 带图会 404「No endpoints found that support image input」，
# omni 系列不存在（400 Unsupported model）。
# 顺带清掉一批已下架、选了必报错的：meta-llama-3.1-8B、THUDM/glm-4-9b-chat、
# Qwen2.5-VL-32B/72B、InternVL3-14B、FLUX.1、SD3、fish-speech-1.5。
FALLBACK = {
    "chat": [
        "zai-org/GLM-5.3", "zai-org/GLM-5.2", "zai-org/GLM-4.5-Air",
        "deepseek-ai/DeepSeek-V4-Flash", "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V3.2", "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen3.6-35B-A3B", "Qwen/Qwen3.5-122B-A10B",
        "Qwen/Qwen3-30B-A3B-Instruct-2507", "Qwen/Qwen3-8B",
        "moonshotai/Kimi-K2.7-Code", "stepfun-ai/Step-3.5-Flash",
        "tencent/Hunyuan-A13B-Instruct", "meituan-longcat/LongCat-2.0",
        "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed", "mimo-v2.6-flash",
        "mimo-v2.5-pro", "mimo-v2.5",
    ],
    "vision": [
        # 只放"看得懂一张照片"的通用视觉模型。OCR 专用那几个（DeepSeek-OCR、
        # PaddleOCR）**不进这里**：它们只会把图里的字读出来，问它"照片里有什么"
        # 得到的是一串 OCR 文本，塞进她的上下文就成了胡话。
        "Qwen/Qwen3-VL-30B-A3B-Instruct", "Qwen/Qwen3-VL-32B-Instruct",
        "Qwen/Qwen3-VL-8B-Instruct", "zai-org/GLM-4.5V",
    ],
    "image": [
        "Tongyi-MAI/Z-Image-Turbo", "Tongyi-MAI/Z-Image",
        "Kwai-Kolors/Kolors", "Qwen/Qwen-Image",
        "Qwen/Qwen-Image-Edit-2509",
    ],
    "audio": [
        "FunAudioLLM/SenseVoiceSmall", "FunAudioLLM/CosyVoice2-0.5B",
        "Qwen/Qwen3-ASR-1.7B", "fnlp/MOSS-TTSD-v0.5",
        "mimo-v2.5-tts", "mimo-v2.5-tts-voicedesign",
        "mimo-v2.5-tts-voiceclone", "mimo-v2.5-asr",
    ],
    "embed": [
        "BAAI/bge-m3",
    ],
}


def classify(mid):
    """判断一个模型 id 属于哪一类。认不出来算 chat（语言模型最多）。"""
    s = str(mid or "").lower()
    if not s:
        return "chat"
    for h in _EMBED_HINTS:
        if h in s:
            return "embed"
    for h in _IMAGE_HINTS:
        if h in s:
            return "image"
    for h in _AUDIO_HINTS:
        if h in s:
            return "audio"
    for h in _VISION_HINTS:
        if h in s:
            return "vision"
    if _GLM_VISION_RE.search(s):
        return "vision"
    return "chat"


def group_models(ids):
    """把一串模型 id 按分类装进字典。embed 也返回（调用方自己决定显不显示）。"""
    out = {"chat": [], "vision": [], "image": [], "audio": [], "embed": []}
    for i in ids:
        if not i:
            continue
        out.setdefault(classify(i), []).append(str(i))
    for k in out:
        out[k].sort()
    return out


def _read_cache(base):
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("base") == base and time.time() - float(c.get("t") or 0) < CACHE_TTL:
            return c.get("ids") or []
    except Exception:
        pass
    return []


def _write_cache(base, ids):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({"base": base, "t": time.time(), "ids": ids},
                      f, ensure_ascii=False)
    except Exception:
        pass


def fetch_online(base, key, timeout=12):
    """问服务商要模型列表。返回 (ids, err)，失败时 ids 为空。

    走 urllib + ProxyHandler({})：不然会被系统代理劫持，返回一个假的 502。
    """
    if not base or not key:
        return [], "没填地址或密钥"
    url = base.rstrip("/") + "/models"
    req = urllib.request.Request(
        url, method="GET", headers={"Authorization": "Bearer " + key})
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        raw = op.open(req, timeout=timeout).read().decode("utf-8")
        data = json.loads(raw)
        ids = []
        for it in (data.get("data") or []):
            mid = it.get("id") if isinstance(it, dict) else None
            if mid:
                ids.append(str(mid))
        return ids, ""
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:120]}"


def probe_provider(base, key, timeout=6):
    """切换前先问一句：这个地址 + 这把密钥到底能不能用。返回 (state, detail)。

    state 三档，调用方自己决定怎么处理（这里不替上层做决定）：
      "ok"       —— 200，能用
      "auth"     —— 401/403，密钥不被接受。**必须拦下**：放过去的话
                    界面显示"已切换"，她下一句直接报错。
      "unknown"  —— 连不上/超时/路径不对，说不准。允许照切，但要在响应里
                    明说"没能验证"，不假装一切正常。

    只打一次 GET /models（和拉清单同一个路径），不加别的请求。
    同样走 ProxyHandler({})：不然会被系统代理劫持，拿到假的 502。
    """
    b = str(base or "").strip().rstrip("/")
    if not b:
        return "unknown", "没有接口地址"
    if not str(key or "").strip():
        return "unknown", "没有可用密钥"
    req = urllib.request.Request(
        b + "/models", method="GET",
        headers={"Authorization": "Bearer " + str(key)})
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        op.open(req, timeout=timeout).read()
        return "ok", ""
    except Exception as e:
        code = getattr(e, "code", None)
        if code in (401, 403):
            return "auth", "服务商拒绝了这把密钥（HTTP %s）" % code
        return "unknown", "%s: %s" % (type(e).__name__, str(e)[:80])


def list_models(base, key, force=False, provider="siliconflow"):
    """给 App 用的模型清单。返回 {ok, source, groups, count, err}；
    source = online（刚拉到）/ cache（用的缓存）/ fallback（拉不到，用内置清单）。
    """
    if not force:
        cached = _read_cache(base)
        if cached:
            return {"ok": True, "source": "cache", "groups": group_models(cached),
                    "count": len(cached), "err": ""}
    ids, err = fetch_online(base, key)
    if ids:
        _write_cache(base, ids)
        return {"ok": True, "source": "online", "groups": group_models(ids),
                "count": len(ids), "err": ""}
    if err:
        # 自定义服务商拉不到很正常（路径不一定叫 /v1/models），别报成故障
        print(f"[model_hub] 拉模型列表失败：{err}", flush=True)
    return {"ok": False, "source": "fallback", "groups": dict(FALLBACK),
            "count": sum(len(v) for v in FALLBACK.values()),
            "err": err or "拉不到模型列表"}


# --- 当前配置 ---
def current(cfg):
    """从配置读出"现在用哪家、哪个模型"。

    ⚠️ 返回的 `api_key` 是**完整明文**，因为服务端自己要用它去调服务商
    （`/api/models` 里 `list_models(cur["api_base"], cur["api_key"])`）。
    **不要把它直接放进 HTTP 响应** —— 回给客户端的用 `public_current()`。
    """
    cfg = cfg or {}
    vis = cfg.get("vision") if isinstance(cfg.get("vision"), dict) else {}
    provider = str(cfg.get("provider") or "")
    if provider not in PROVIDERS:
        # 老配置没有 provider 字段：按 base 反推，认不出来就算自定义。
        # ⚠️ 反推要按 base 的 host 判，别用 `in` 匹配整串 ——
        # "siliconflow" 那种字面匹配在多两个provider 之后就成了隐形坑：
        # 用户切到小米但 provider 字段没落盘时，会被算成"自定义"，
        # 界面显示的名字和实际用的服务对不上。
        b = str(cfg.get("api_base") or "").lower()
        provider = "custom"
        for pid, meta in PROVIDERS.items():
            if pid == "custom":
                continue
            host = str(meta.get("base") or "").lower()
            if host and host in b:
                provider = pid
                break
    ol = cfg.get("ollama") if isinstance(cfg.get("ollama"), dict) else {}
    dl = cfg.get("delegation") if isinstance(cfg.get("delegation"), dict) else {}
    return {
        "provider": provider,
        "provider_name": PROVIDERS.get(provider, {}).get("name", provider),
        "api_base": str(cfg.get("api_base") or ""),
        "api_key": str(cfg.get("api_key") or ""),
        "model_mode": str(cfg.get("model_mode") or "cloud"),
        "ollama": {
            "base": str(ol.get("base") or ""),
            "model": str(ol.get("model") or ""),
            "num_ctx": ol.get("num_ctx"),
            "num_predict": ol.get("num_predict"),
            "think": ol.get("think", True),
            "request_timeout": ol.get("request_timeout"),
        },
        "delegation": {
            "enabled": bool(dl.get("enabled", False)),
            "router_enabled": bool(dl.get("router_enabled", True)),
            "allowed_roots": [str(r) for r in dl.get("allowed_roots") or []
                              if str(r).strip()],
        },
        "chat": str(cfg.get("model") or ""),
        "vision": str(vis.get("model") or ""),
        "vision_enabled": bool(vis.get("enabled", True)),
        "image": str(cfg.get("image_model") or ""),
        "audio": str(cfg.get("audio_model") or ""),
        "audio_ready": False,          # 语音模块还没做，App 据此置灰
    }


def public_current(cfg):
    """给客户端看的版本：api_key 只留前后各几位，其余用省略号盖掉。

    为什么要脱敏：这个响应走明文 HTTP，而且 App 收到后会把它**填进一个
    普通 input**（chat.html 的 loadModelCfg）。密钥是账号级凭据，明文过公网
    等于任何人抓到一次包或看到一次屏幕就能拿去刷额度。

    顺带加一个 api_key_set：前端靠它知道"已经有一把了"，不用看见密钥本身
    就能显示状态。原来的做法是把打码串塞进 placeholder，那等于把脱敏串
    摆在输入框里 —— 谁手一抖复制了重贴回去，真 key 就被覆盖了（见 apply_cfg）。
    """
    d = current(cfg)
    k = d.get("api_key") or ""
    d["api_key_set"] = bool(k)
    d["api_key"] = mask_key(k)
    # 🔴 这里**不能**调 adopt_current_key：那会让"刚切过去、还没配key"的
    # 厂商把眼下这把（属于别家的）认领走 —— 于是它变成"已存密钥"，
    # 切换看着成功而她拿着别家的 key 去调，必然 401。
    # 迁移只在 run_server 启动时做一次（那时还没切过，key 就是它自己的）。
    # 哪���自己存过密钥 —— 前端据此在服务商名字上标"已存"，
    # 让"点一下就切换"这件事是看得见的：有 key 才切得动。
    d["keys_by_provider"] = {
        pid: has_provider_key(cfg, pid) for pid in PROVIDERS}
    # 🔴 当前服务商**自己没有**存过密钥 = 这家是"切过来但没配 key"。
    # 这时配置里的 api_key 是别家的那把，照它发请求必然被拒。
    # 前端要能识别这种情况并要 key，别让它显示成"已切换"。
    d["key_belongs_to"] = (d.get("provider")
                            if has_provider_key(cfg, d.get("provider"))
                            else "")
    return d


def mask_key(k):
    """给界面看的密钥样子。短于 12 位的一律全盖 —— 那个长度露头露尾没有意义。"""
    k = k or ""
    return ("%s…%s" % (k[:6], k[-4:])) if len(k) > 12 else ("*" * len(k))


def provider_key(cfg, prov):
    """取某个服务商自己的那把密钥；没有就回退到当前那把。

    为什么要按家存：各家 key 不通用。配置里只有一把 `api_key` 时，
    "切到硅基流动"换的只是地址和模型名，密钥还是小米那把 ——
    切换看着成功了，她下一句就调不动（401）。要真能一键切换，
    必须把每家的密钥分别记下来：`api_keys = {"siliconflow": "sk-…"}`。
    """
    bag = cfg.get("api_keys")
    if isinstance(bag, dict):
        k = str(bag.get(prov) or "").strip()
        if k:
            return k
    return str(cfg.get("api_key") or "")


def has_provider_key(cfg, prov):
    """这家是不是已经存过密钥（回退的那把不算数）。"""
    bag = cfg.get("api_keys")
    return bool(isinstance(bag, dict) and str(bag.get(prov) or "").strip())


def _any_provider_key(cfg):
    """api_keys 里有没有登记过任何一家的密钥。

    用来区分两种"当前这家没登记"：
      - 老配置（清单都不存在）→ 手上那把就是它的，该放行；
      - 新配置（清单里有别家）→ 手上那把是别家的，不能拿来当这家的用。
    """
    bag = (cfg or {}).get("api_keys")
    if not isinstance(bag, dict):
        return False
    return any(str(v or "").strip() for v in bag.values())


def adopt_current_key(cfg):
    """把当前那把密钥登记到当前服务商名下（原地改，只在缺这一次时写）。

    为什么需要：`api_keys` 是后来才有的，老配置里只有一把 `api_key`。
    不补这一步的话，界面会把每一家都显示成"还没存密钥"——
    而其中一家明明正拿着那把 key 在用。判据里要能区分"没配"和"配了没登记"。

    🔴 老配置连 `provider` 字段都没有（云上实锤：api_base 有、provider 是
    None）——不能因此直接放弃，要按 base 反推出是哪家（跟 current() 同一套
    判断）。不然迁移永远空转：正在用的这家显示"还没存密钥"，切走再切回来
    也得重填，"一键切换"对老用户等于没做。反推出来顺手把 provider 写回去，
    apply_cfg 的"换走的是哪家"也靠这个字段。
    """
    cfg = cfg or {}
    prov = str(cfg.get("provider") or "").strip()
    if not prov:
        b = str(cfg.get("api_base") or "").lower()
        for pid, meta in PROVIDERS.items():
            if pid == "custom":
                continue
            host = str(meta.get("base") or "").lower()
            if host and host in b:
                prov = pid
                break
        if prov:
            cfg["provider"] = prov
    k = str(cfg.get("api_key") or "").strip()
    if not prov or not k or has_provider_key(cfg, prov):
        return False
    bag = dict(cfg.get("api_keys") or {})
    bag[prov] = k
    cfg["api_keys"] = bag
    return True


def is_masked(k):
    """判断传进来的这一串是不是打码后的样子。

    存在的理由：`apply_cfg` 单看 `key != cfg["api_key"]` 判不出区别 ——
    打码串确实不等于真 key，于是它会被当成"用户换了新密钥"写进去，
    写完 AI 就再也调不动了。前端只要不小心回填一次（复制粘贴、浏览器 autofill、
    或者哪个版本压根就是预填的）就中招，所以这一层必须在服务端堵。
    """
    k = str(k or "")
    if not k:
        return False
    if "…" in k or "..." in k:
        return True
    # 全是星号也算：短密钥被打码后就是这个形状
    return bool(k) and set(k) == {"*"}


# ── 快速切换清单（主页下拉用的"我用过的模型"）──────────────────
#
# 🔴 这不是第二份模型配置。真正生效的永远只有 api_config 里那**一份**
# （provider / api_base / api_key / model）；这份清单只回答"下拉里该列
# 哪些条目"，点一条仍然走 apply_cfg 那条老路写回同一份配置。
#
# 为什么需要它：模型清单是 /v1/models 在线拉的，断网/换 key/平台改版就拉不到，
# 而且它只反映"服务商有什么"，不反映"我用过什么"。用户想一键切回上个月那个
# 模型，得有地方记着。
SAVED_MAX = 40


def _short_name(model):
    """`deepseek-ai/DeepSeek-V4-Flash` → `DeepSeek-V4-Flash`（只给界面显示用）。"""
    s = str(model or "").strip()
    return s.rsplit("/", 1)[-1] if "/" in s else s


def _norm_entry(it):
    """把清单里的一条规范化；缺模型名的一律丢弃（返回 None）。"""
    if not isinstance(it, dict):
        return None
    model = str(it.get("model") or "").strip()
    if not model:
        return None
    prov = str(it.get("provider") or "").strip() or "custom"
    base = str(it.get("base") or "").strip().rstrip("/")
    if not base:
        base = str(PROVIDERS.get(prov, {}).get("base") or "")
    return {
        "provider": prov,
        "base": base,
        "model": model,
        "name": str(it.get("name") or "").strip() or _short_name(model),
        "added_at": str(it.get("added_at") or ""),
    }


def _entry_key(e):
    """判重用的键：(服务商, 地址, 模型)。

    地址也进键 —— 同一个模型名在"自定义"下可能挂在两个不同的本机部署上
    （比如两台机器各自跑的 vllm），只按 (服务商, 模型) 判重会把它们合成一条，
    切过去打的是另一个地址。反过来，同名模型来自不同服务商天然就分得开，
    满足"同名模型跨服务商不混淆"。
    """
    return (str(e.get("provider") or "").lower(),
            str(e.get("base") or "").rstrip("/").lower(),
            str(e.get("model") or ""))


def saved_models(cfg):
    """读清单（只读，不写盘）。顺手把同一键的重复项并掉。"""
    out, seen = [], set()
    for it in (cfg or {}).get("saved_models") or []:
        e = _norm_entry(it)
        if not e:
            continue
        k = _entry_key(e)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def remember_model(cfg, provider, base, model, name=""):
    """把 (服务商, 地址, 模型) 记进清单。返回 (新配置, 是不是新加的)。

    已存在就只刷新显示名，不动顺序（用户看惯的次序不该因为一次保存就跳）。
    """
    e = _norm_entry({"provider": provider, "base": base, "model": model, "name": name})
    if not e:
        return cfg, False
    dst = dict(cfg or {})
    cur = saved_models(dst)
    k = _entry_key(e)
    for old in cur:
        if _entry_key(old) == k:
            if e["name"] and e["name"] != old.get("name"):
                old["name"] = e["name"]
                dst["saved_models"] = cur
                return dst, False
            return cfg, False
    if not e["added_at"]:
        e["added_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    cur.append(e)
    # 只留最近 SAVED_MAX 条：清单会跟着配置一起过同步，别让它无上限地长
    dst["saved_models"] = cur[-SAVED_MAX:]
    return dst, True


def remember_current(cfg):
    """把配置里**当前**那组 (provider, base, model) 记进清单。

    在 /api/models/apply 保存成功后调一次：用户只要在设置页选过一次模型，
    主页下拉里就有它了 —— 不需要再让用户"手动添加"第二遍。
    """
    cfg = cfg or {}
    prov = str(cfg.get("provider") or "").strip()
    model = str(cfg.get("model") or "").strip()
    if not prov or not model:
        return cfg, False
    return remember_model(cfg, prov, cfg.get("api_base"), model)


def public_saved(cfg):
    """给客户端的下拉数据：清单 + 生效状态。**不含任何密钥**（连打码的都不给）。

    连接状态只用布尔表达：
      has_key —— 这家自己有存过密钥（切过去能直接调）
      active  —— 就是眼下生效的这一条
      unsaved —— 当前生效的组合还不在清单里（老配置 / 手改过 config）
    """
    cur = current(cfg)
    prov, base, model = (cur["provider"], cur["api_base"],
                         str(cur.get("chat") or ""))
    out = []
    hit = False
    for e in saved_models(cfg):
        act = (e["provider"] == prov and e["model"] == model
               and (not e["base"] or not base or e["base"].rstrip("/") == base.rstrip("/")))
        if act:
            hit = True
        out.append(dict(e,
                        provider_name=PROVIDERS.get(e["provider"], {}).get("name")
                        or e["provider"],
                        has_key=has_provider_key(cfg, e["provider"]),
                        active=act,
                        unsaved=False))
    if model and not hit:
        # 当前这把还没进清单：也列出来标 active+unsaved，否则下拉里
        # "选中的那一条"不存在，用户会以为模型丢了。
        out.insert(0, {
            "provider": prov, "provider_name": PROVIDERS.get(prov, {}).get("name") or prov,
            "base": base, "model": model, "name": _short_name(model),
            "added_at": "", "has_key": has_provider_key(cfg, prov),
            "active": True, "unsaved": True,
        })
    return {
        "models": out,
        "current": {"provider": prov,
                    "provider_name": PROVIDERS.get(prov, {}).get("name") or prov,
                    "base": base, "model": model, "name": _short_name(model),
                    "has_key": has_provider_key(cfg, prov),
                    "key_belongs_to": (prov if has_provider_key(cfg, prov) else "")},
    }


def quick_switch(cfg, provider, model):
    """按清单切到某一条。返回 (新配置, 改动说明, 错误, 警告)。

    失败一律**明说**，绝不"悄悄换一个能用的"：静默兜底会让人以为在用 A，
    实际拿 B 的回复，比直接报错危险得多。

    切换前会探一次服务商（探不通不阻断，但要把"没验证过"如实带回去）。
    """
    prov = str(provider or "").strip()
    mid = str(model or "").strip()
    if not mid:
        return cfg, [], "没指定模型", ""
    # 🔴 切走之前先把"正在用的这一组"记进清单。
    # 不记的话：老配置里当前这个模型本来就不在清单里（下拉里显示成"未保存"），
    # 用户点别的模型切走之后，它就**从下拉里消失**了 —— 再也点不回来，
    # 只能去设置页重配。"当前选择应能正确恢复"说的就是这件事。
    cfg, _ = remember_current(cfg)
    if not prov:
        prov = str((cfg or {}).get("provider") or "").strip()
    ent = None
    for e in saved_models(cfg):
        if e["model"] == mid and (not prov or e["provider"] == prov):
            ent = e
            break
    if not ent:
        return cfg, [], "这个模型不在已保存的清单里（先在「模型与服务商」里选一次）", ""

    cur = current(cfg)
    has_key = has_provider_key(cfg, ent["provider"])
    key = provider_key(cfg, ent["provider"]) if has_key else ""
    if not has_key and not _any_provider_key(cfg) and cfg.get("api_key"):
        # 老配置（只有一把 api_key、api_keys 还没建立起来）里那把就是当前这家的，
        # 不能因为"没登记"就把老用户锁在门外。注意判据是**一把都没登记过**：
        # 一旦 api_keys 里有别家的 key，手上这把就属于别家，这家就是真没配。
        has_key = True
        key = str(cfg.get("api_key") or "")
    if not has_key and ent["provider"] != "custom":
        return cfg, [], ("「%s」还没存过密钥：先在「模型与服务商」里填一次它的 key，"
                         "再回来一键切换"
                         % (PROVIDERS.get(ent["provider"], {}).get("name")
                            or ent["provider"])), ""

    want_base = ent["base"] or PROVIDERS.get(ent["provider"], {}).get("base") or ""
    warn = ""
    if str((cfg or {}).get("model_mode") or "cloud") != "local":
        state, detail = probe_provider(want_base, key)
        if state == "auth":
            return cfg, [], ("「%s」拒绝了这把密钥：%s。先在设置里更新 key"
                             % (PROVIDERS.get(ent["provider"], {}).get("name")
                                or ent["provider"], detail)), ""
        if state == "unknown":
            # 不阻断（可能是本机部署、也可能是临时断网），但绝不装作验证过了
            warn = "没能连上服务商验证（%s）：已按你的选择切换，下一句若报错就是它" % detail

    new_cfg, changed = apply_cfg(cfg, {"provider": ent["provider"],
                                       "api_base": want_base,
                                       "chat": ent["model"]})
    after = current(new_cfg)
    if after["provider"] != ent["provider"] or str(after.get("chat")) != ent["model"]:
        # 理论上到不了这里；真到了也绝不装作切成功
        return cfg, [], ("切换没生效：服务端现在仍是「%s / %s」"
                         % (after.get("provider_name") or after["provider"],
                            after.get("chat") or "（空）")), ""
    # 地址也要核一遍：apply_cfg 在"换服务商但地址没变"的路径上会套用
    # PROVIDERS 里的标准地址，自定义/自建/代理地址那条就丢了 —— 少了这一句，
    # 界面显示切到了新家，实际还在打老地址。
    if want_base and str(after.get("api_base") or "").rstrip("/") != want_base.rstrip("/"):
        return cfg, [], ("切换没生效：接口地址还是 %s，没换成 %s"
                         % (after.get("api_base") or "（空）", want_base)), ""
    if str(new_cfg.get("model_mode") or "cloud") != "local" and not new_cfg.get("api_key"):
        return cfg, [], "这家没有可用的密钥，切换会立刻 401：先去设置里补一把 key", ""
    return new_cfg, changed, "", warn


def forget_model(cfg, provider, model):
    """从清单里去掉一条。返回 (新配置, 是不是真去掉了)。

    🔴 只动清单，**不动当前生效的配置**：用户可能正用着它，把 api_base/model
    一起清掉等于偷偷换模型。当前那条被移除后仍然生效，只是在下拉里显示成
    "（未保存）"—— 界面不会因此谎报"当前模型"。
    """
    prov = str(provider or "").strip()
    mid = str(model or "").strip()
    dst = dict(cfg or {})
    keep, hit = [], False
    for e in saved_models(dst):
        if e["model"] == mid and (not prov or e["provider"] == prov) and not hit:
            hit = True
            continue
        keep.append(e)
    if not hit:
        return cfg, False
    dst["saved_models"] = keep
    return dst, True


def apply_cfg(cfg, p):
    """把 App 传来的设置并进配置字典（只改字典，不落盘）。

    p 里的字段名跟 current() 一致：provider / api_base / api_key / chat / vision /
    image / audio / vision_enabled。返回 (新配置, 改了哪些项的中文说明)；
    空字符串＝不改那一项。
    """
    cfg = dict(cfg or {})
    changed = []

    prov = str(p.get("provider") or "").strip()
    # 🔴 原服务商和切换前那把密钥必须在改 provider **之前**抓住。
    # 下面第一行就把 cfg["provider"] 覆盖了，之后再想"换走的是哪家"
    # 拿到的已经是新家 —— 于是 `api_keys` 里永远登记不上，
    # 切回去时 `provider_key(原家)` 回退到 api_key，拿到的是别家的密钥。
    prev_prov = str(cfg.get("provider") or "").strip()
    prev_key0 = cfg.get("api_key")
    prev_base = str(cfg.get("api_base") or "").strip().rstrip("/")
    switched = bool(prov and prov in PROVIDERS and prov != prev_prov)
    if switched:
        cfg["provider"] = prov
        changed.append(f"服务商 → {PROVIDERS[prov]['name']}")

    # 接口地址：只认"用户真的改了地址"（填了个跟旧地址不同的值）。留空、或提交的
    # 还是**旧地址**都算没改 —— 前端下拉原先不联动，保存时会把旧 base 原样带上，
    # 于是老写法里"换服务商"先把 base 设成新家、紧接着又被下面那段用旧地址覆盖回去：
    # 服务商名字换了、地址没换，界面看着切了，实际还在打上一家（小米名字 + 硅基地址）。
    # 合并成一处判断，避免自我覆盖。
    p_base = str(p.get("api_base") or "").strip().rstrip("/")
    user_base = bool(p_base and p_base != prev_base)
    if user_base:
        if p_base != cfg.get("api_base"):
            cfg["api_base"] = p_base
            changed.append("接口地址 → " + p_base)
    elif switched and prov != "custom":
        new_base = PROVIDERS[prov]["base"]
        if new_base and new_base != cfg.get("api_base"):
            cfg["api_base"] = new_base
            changed.append("接口地址 → " + new_base)

    key = str(p.get("api_key") or "").strip()
    # 🔴 判据一律用**提前捕获**的 prev_prov/prev_key0。
    # 上面第一行已经把 cfg["provider"] 改成新的了，再用 cfg.get("provider")
    # 比恒为 False —— 于是这段代码整段不执行，小米的key 永远登记不上，
    # 切回去时 provider_key 只能回退到 api_key（那时已是别家的）。
    # 先把"换走的是哪家、它眼下用哪把"记进 api_keys，再谈换密钥 ——
    # 顺序反了的话 want_key 会取到还没登记时的回退值。
    if switched and prev_key0 and prev_prov in PROVIDERS \
            and not has_provider_key(cfg, prev_prov):
        bag = dict(cfg.get("api_keys") or {})
        bag[prev_prov] = prev_key0
        cfg["api_keys"] = bag
    want_key = provider_key(cfg, prov) if switched else ""

    # 打码串绝不能写进去：它 != 真 key，会通过下面的 != 判断，然后覆盖掉真的那把，
    # 之后 AI 一句也说不出来。空串（前端留空 = 沿用当前）和打码串在这里都要放过。
    if key and not is_masked(key) and key != cfg.get("api_key"):
        cfg["api_key"] = key
        changed.append("密钥已更新")
        # 同时记进按家那份，否则下次切走再切回来还得重填一遍
        if prov in PROVIDERS:
            bag = dict(cfg.get("api_keys") or {})
            bag[prov] = key
            cfg["api_keys"] = bag
    elif is_masked(key):
        changed.append("密钥没改（填的是打码后的样子，服务器上那把没动）")

    # 切了服务商就换上那把已存的密钥；那家一把都没存就不换 ——
    # 沿用别家的密钥等于换完她立刻调不动，而界面显示"已切换"。
    if switched and want_key and want_key != cfg.get("api_key") \
            and not (key and not is_masked(key) and key == cfg.get("api_key")):
        cfg["api_key"] = want_key
        changed.append("已换上%s的密钥" % PROVIDERS[prov]["name"])

    chat = str(p.get("chat") or "").strip()
    if chat and chat != cfg.get("model"):
        cfg["model"] = chat
        changed.append("对话模型 → " + chat)

    vis = str(p.get("vision") or "").strip()
    vcfg = cfg.get("vision") if isinstance(cfg.get("vision"), dict) else {}
    vcfg = dict(vcfg)
    if vis and vis != vcfg.get("model"):
        vcfg["model"] = vis
        changed.append("看图模型 → " + vis)
    if "vision_enabled" in p:
        ve = bool(p.get("vision_enabled"))
        if ve != vcfg.get("enabled", True):
            vcfg["enabled"] = ve
            changed.append("看图功能 → " + ("开" if ve else "关"))
    if vcfg:
        cfg["vision"] = vcfg

    img = str(p.get("image") or "").strip()
    if img and img != cfg.get("image_model"):
        cfg["image_model"] = img
        changed.append("生图模型 → " + img)

    aud = str(p.get("audio") or "").strip()
    if aud and aud != cfg.get("audio_model"):
        cfg["audio_model"] = aud
        changed.append("语音模型 → " + aud)

    # 运行方式：cloud / local / hybrid（混合模式的委派能力在 P2，首版保存了
    # 也只会让主模型走云端 —— 不报错，UI 那边负责把未开放项置灰）。
    mode = str(p.get("model_mode") or "").strip().lower()
    if mode:
        if mode not in ("cloud", "local", "hybrid"):
            return cfg, changed + ["运行方式没保存：只认 cloud/local/hybrid"]
        if mode != str(cfg.get("model_mode") or "cloud"):
            cfg["model_mode"] = mode
            changed.append("运行方式 → "
                           + {"cloud": "云端模型", "local": "本地 Ollama",
                              "hybrid": "混合（云端主脑）"}.get(mode, mode))

    # Ollama 连接段：逐键收口，只认白名单字段（防止 UI/请求塞任意键进 config）。
    ol = p.get("ollama")
    if isinstance(ol, dict):
        cur_ol = dict(cfg["ollama"]) if isinstance(cfg.get("ollama"), dict) else {}
        if str(ol.get("base") or "").strip():
            # 顺手剥尾斜杠和 /v1 后缀，和 OllamaProvider 的归一保持一致，
            # 免得"保存的值"和"实际请求的值"看着不一样。
            b = str(ol["base"]).strip().rstrip("/")
            if b.endswith("/v1"):
                b = b[:-3]
            cur_ol["base"] = b
        if "model" in ol:
            cur_ol["model"] = str(ol.get("model") or "").strip()
        for nk in ("num_ctx", "num_predict", "request_timeout"):
            if ol.get(nk) is not None:
                try:
                    cur_ol[nk] = int(float(ol[nk]))
                except (TypeError, ValueError):
                    pass
        if "think" in ol:
            cur_ol["think"] = bool(ol["think"]) if ol["think"] is not None else None
        if cur_ol != (cfg.get("ollama") or {}):
            cfg["ollama"] = cur_ol
            changed.append("本地模型设置已更新")

    # 委派段（P3 只读委派）：白名单三键 enabled / router_enabled / allowed_roots。
    # 只有请求显式带 delegation dict 才改，旧客户端保存不会冲掉已配段。
    # allowed_roots 空名单是 fail-closed（任何工作区都派不出去），不在服务端
    # 强制非空 —— 由 UI 提示用户。
    dl = p.get("delegation")
    if isinstance(dl, dict):
        cur_dl = dict(cfg["delegation"]) if isinstance(cfg.get("delegation"), dict) else {}
        if "enabled" in dl and dl.get("enabled") is not None:
            cur_dl["enabled"] = bool(dl.get("enabled"))
        if "router_enabled" in dl and dl.get("router_enabled") is not None:
            cur_dl["router_enabled"] = bool(dl.get("router_enabled"))
        if "allowed_roots" in dl:
            roots = dl.get("allowed_roots")
            if isinstance(roots, str):
                # 文本框：换行不行就认分号
                roots = roots.replace(";", "\n").replace("\r\n", "\n").splitlines()
            seen, clean = set(), []
            for r in roots or []:
                rs = str(r or "").strip().strip('"').strip("'")
                if rs and rs not in seen:
                    seen.add(rs)
                    clean.append(rs)
            cur_dl["allowed_roots"] = clean
        if cur_dl != (cfg.get("delegation") or {}):
            cfg["delegation"] = cur_dl
            changed.append("本地委派设置已更新")

    return cfg, changed


def save(cfg):
    """写回 config.json。先把旧文件备份一份 —— 改配置改崩过不止一次。"""
    try:
        bak = CONFIG_PATH + ".bak_modelhub"
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, encoding="utf-8") as f:
                old = f.read()
            with open(bak, "w", encoding="utf-8") as f:
                f.write(old)
    except Exception:
        pass
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return True


def load():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
