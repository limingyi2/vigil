"""Core runtime context. Archived channel files keep their original path rules."""
DEFAULT_SRC = "pc"


def normalize_src(src=None):
    value = str(src or "").strip().lower()
    if value and value != DEFAULT_SRC:
        raise ValueError("unsupported_channel: Core only supports pc")
    return DEFAULT_SRC


def persona_key(config=None, src=None, default_key=""):
    normalize_src(src)
    from persona_store import key_from_config
    cfg = config or {}
    # Retain existing explicit PC bindings; persona_file remains the fallback.
    bindings = cfg.get("persona_by_src") or {}
    return str(bindings.get(DEFAULT_SRC) or "").strip() or (
        key_from_config(cfg) if cfg.get("persona_file") else
        str(default_key or "").strip() or key_from_config(cfg))


def runtime_persona_key():
    import runtime
    brain = runtime._LOCAL.get("brain")
    return persona_key(getattr(brain, "api", None) or runtime.api_config)
