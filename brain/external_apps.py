"""Persistent external applications; all execution enters through registry WRITE grants.

Configuration is data, never imported as Python. Credentials are environment variable
references. MCP sessions reconnect for each operation and never replay failed calls.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

_LOCK = threading.RLock()
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_LIMIT = 128 * 1024


def _path():
    import paths
    return Path(paths.CONFIG_DIR) / "external-apps.json"


def _read():
    path = _path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("apps"), dict):
        raise ValueError("invalid external-apps.json")
    return data["apps"]


def _save(apps):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="external-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"schema": 1, "apps": apps}, stream, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _argv(value):
    if not isinstance(value, list) or len(value) > 128 or any(
            not isinstance(x, str) or "\0" in x or len(x) > 8192 for x in value):
        raise ValueError("arguments must be an array of strings")
    return value


def _name(value):
    name = str(value or "")
    if not _ID.fullmatch(name):
        raise ValueError("id must contain only letters, digits, _ and - (1..64)")
    return name


def _revision(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("config must be an object")
    kind = raw.get("kind")
    if kind not in ("mcp", "cli", "skill"):
        raise ValueError("kind must be mcp, cli or skill")
    cfg = {"kind": kind, "description": str(raw.get("description") or "")[:1000]}
    timeout = float(raw.get("timeout", 30))
    if not 1 <= timeout <= 120:
        raise ValueError("timeout must be 1..120 seconds")
    cfg["timeout"] = timeout
    env = raw.get("env") or {}
    if not isinstance(env, dict) or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                                         or not isinstance(v, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
                                         for k, v in env.items()):
        raise ValueError("env must map child variable names to existing environment variable names")
    cfg["env"] = env
    if kind == "skill":
        text = raw.get("instructions")
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > _LIMIT:
            raise ValueError("skill instructions must be nonempty UTF-8 text, at most 128KB")
        cfg["instructions"] = text
        return cfg
    transport = raw.get("transport", "stdio") if kind == "mcp" else "stdio"
    if transport not in ("stdio", "http", "sse"):
        raise ValueError("transport must be stdio, http or sse")
    cfg["transport"] = transport
    if transport == "stdio":
        cmd = raw.get("command")
        if not isinstance(cmd, str) or not cmd.strip() or "\0" in cmd:
            raise ValueError("command is required (executable, not shell text)")
        cfg.update(command=cmd, args=_argv(raw.get("args", [])))
        cwd = raw.get("cwd")
        if cwd:
            if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
                raise ValueError("cwd must be an existing absolute directory")
            cfg["cwd"] = cwd
        if kind == "cli":
            cfg["probe_args"] = _argv(raw.get("probe_args", ["--version"]))
            if Path(cmd).suffix.lower() in (".cmd", ".bat"):
                raise ValueError("CLI requires a native executable; use node.exe with the CLI script for npm tools")
    else:
        url = raw.get("url", "")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("MCP URL must be HTTP(S), with no embedded credentials or fragment")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("remote MCP endpoints require HTTPS")
        cfg["url"] = url
        headers = raw.get("headers_env") or {}
        if not isinstance(headers, dict) or any(not re.fullmatch(r"[A-Za-z0-9-]+", k)
                                               or not isinstance(v, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
                                               for k, v in headers.items()):
            raise ValueError("headers_env must map header names to environment variable names")
        cfg["headers_env"] = headers
    return cfg


def listing(_args=None):
    with _LOCK:
        apps = _read()
    return {"ok": True, "apps": [{"id": name, "kind": a["config"]["kind"],
            "description": a["config"]["description"], "state": a.get("state", "configured"),
            "revision": _revision(a["config"]),
            "verified_at": a.get("verified_at"), "tools": a.get("tools", []),
            "detail": "状态是上次验证结果；call 会重新连接，不自动重放失败请求。"}
            for name, a in sorted(apps.items())]}


def register(args):
    name = _name(args.get("id"))
    cfg = _validate(args.get("config"))
    with _LOCK:
        apps = _read()
        apps[name] = {"config": cfg, "state": "configured", "tools": []}
        _save(apps)
    return {"ok": True, "id": name, "revision": _revision(cfg), "state": "configured", "next": "probe"}


def remove(args):
    with _LOCK:
        apps = _read()
        apps.pop(_name(args.get("id")), None)
        _save(apps)
    return {"ok": True}


def _get(args):
    name = _name(args.get("id"))
    with _LOCK:
        item = _read().get(name)
    if not item:
        raise ValueError("application is not registered")
    if args.get("revision") != _revision(item["config"]):
        raise ValueError("configuration_changed: use the revision from register/list")
    return name, item, item["config"]


def _environment(cfg):
    result = {}
    for key, ref in cfg.get("env", {}).items():
        if ref not in os.environ:
            raise ValueError("missing environment variable: " + ref)
        result[key] = os.environ[ref]
    return result


def _cli(cfg, argv):
    # Do not buffer unbounded CLI output in memory, and do not invoke a shell.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([cfg["command"], *cfg["args"], *_argv(argv)],
                                cwd=cfg.get("cwd"), env={**os.environ, **_environment(cfg)},
                                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            code = proc.wait(timeout=cfg["timeout"])
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return {"ok": False, "status": "unknown", "error": "cli_timeout",
                    "detail": "命令已超时，可能已产生副作用；不会自动重试。"}
        out.seek(0); err.seek(0)
        return {"ok": code == 0, "exit_code": code,
                "stdout": out.read(_LIMIT).decode("utf-8", errors="replace"),
                "stderr": err.read(_LIMIT).decode("utf-8", errors="replace")}


async def _mcp(cfg, tool=None, arguments=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import anyio
    async with AsyncExitStack() as stack:
        if cfg["transport"] == "stdio":
            params = StdioServerParameters(command=cfg["command"], args=cfg["args"],
                                           cwd=cfg.get("cwd"), env=_environment(cfg))
            # Never leak a server's stderr (which can contain credentials) into chat logs.
            sink = stack.enter_context(open(os.devnull, "w"))
            read, write = await stack.enter_async_context(stdio_client(params, errlog=sink))
        else:
            headers = {}
            for key, ref in cfg.get("headers_env", {}).items():
                if ref not in os.environ:
                    raise ValueError("missing environment variable: " + ref)
                headers[key] = os.environ[ref]
            if cfg["transport"] == "http":
                import httpx
                from mcp.client.streamable_http import streamable_http_client
                client = await stack.enter_async_context(httpx.AsyncClient(headers=headers,
                    timeout=cfg["timeout"], follow_redirects=False))
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(cfg["url"], http_client=client))
            else:
                from mcp.client.sse import sse_client
                read, write = await stack.enter_async_context(sse_client(cfg["url"], headers=headers,
                    timeout=cfg["timeout"], sse_read_timeout=cfg["timeout"]))
        session = await stack.enter_async_context(ClientSession(read, write,
            read_timeout_seconds=timedelta(seconds=cfg["timeout"])))
        with anyio.fail_after(cfg["timeout"]):
            await session.initialize()
            tools, cursor = [], None
            for _ in range(32):
                page = await session.list_tools(cursor=cursor)
                tools.extend(t.model_dump(mode="json", exclude_none=True) for t in page.tools)
                cursor = page.nextCursor
                if not cursor:
                    break
            else:
                raise ValueError("MCP tool pagination exceeded limit")
            if tool is None:
                return {"ok": True, "tools": tools}
            spec = next((t for t in tools if t["name"] == tool), None)
            if not spec:
                raise ValueError("tool is not advertised by this MCP server")
            from jsonschema import validate
            validate(arguments, spec["inputSchema"])
            result = await session.call_tool(tool, arguments=arguments)
            payload = result.model_dump(mode="json", exclude_none=True)
            encoded = json.dumps(payload, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > _LIMIT:
                return {"ok": not result.isError, "truncated": True, "text": encoded[:_LIMIT]}
            return {"ok": not result.isError, "result": payload}


def _redact(result, cfg):
    secrets = [os.environ.get(ref, "") for ref in
               [*cfg.get("env", {}).values(), *cfg.get("headers_env", {}).values()]]
    def clean(value):
        if isinstance(value, str):
            for secret in sorted(set(secrets), key=len, reverse=True):
                if secret:
                    value = value.replace(secret, "[redacted]")
            return value
        if isinstance(value, dict):
            return {clean(k): clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return clean(result)


def probe(args):
    name, item, cfg = _get(args)
    try:
        if cfg["kind"] == "mcp":
            result = asyncio.run(_mcp(cfg))
        elif cfg["kind"] == "cli":
            result = _cli(cfg, cfg["probe_args"])
            result["tools"] = [{"name": "run", "description": cfg["description"],
                "inputSchema": {"type": "object", "properties": {"argv": {
                    "type": "array", "items": {"type": "string"}}}, "required": ["argv"],
                    "additionalProperties": False}}] if result["ok"] else []
        else:
            result = {"ok": True, "tools": [{"name": "instructions", "description": cfg["description"]}]}
    except Exception as exc:
        result = {"ok": False, "error": type(exc).__name__, "detail": "连接验证失败；检查依赖、端点和授权配置。"}
    result = _redact(result, cfg)
    with _LOCK:
        apps = _read()
        if apps.get(name) != item:
            return {"ok": False, "error": "configuration_changed"}
        apps[name].update(state="verified" if result["ok"] else "unavailable",
                          verified_at=time.time(), tools=result.get("tools", []))
        _save(apps)
    return {"id": name, **result}


def call(args):
    _, item, cfg = _get(args)
    if item.get("state") != "verified":
        return {"ok": False, "error": "probe_required"}
    if cfg["kind"] == "skill":
        return {"ok": False, "error": "use_skill_read"}
    arguments = args.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    try:
        if cfg["kind"] == "cli":
            if args.get("tool", "run") != "run" or set(arguments) != {"argv"}:
                raise ValueError("CLI call requires tool=run and arguments={argv:[...]}")
            result = _cli(cfg, arguments["argv"])
        else:
            result = asyncio.run(_mcp(cfg, str(args.get("tool") or ""), arguments))
        return _redact(result, cfg)
    except Exception as exc:
        return {"ok": False, "status": "unknown", "error": type(exc).__name__,
                "detail": "未收到完整执行回执；可能已产生副作用，不会自动重试。"}


def skill_read(args):
    _, _, cfg = _get(args)
    if cfg["kind"] != "skill":
        raise ValueError("not an instruction skill")
    return {"ok": True, "instructions": cfg["instructions"], "resource_root": cfg.get("resource_root"),
            "detail": "这是用户安装的任务指南；不会自动授予工具、目录或外部账户权限。"}


def install_skill(args):
    source = Path(str(args.get("path") or ""))
    if not source.is_absolute() or not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("path must be an existing absolute skill file, at most 16MB")
    if source.name.casefold() == "skill.md":
        name = _name(args.get("id"))
        # Copy the package as data; referenced scripts still need separately authorized tools.
        import paths
        root = (Path(paths.DATA_DIR) / "instruction_skills").resolve()
        if root.is_relative_to(source.parent.resolve()):
            raise ValueError("skill source cannot contain the installation directory")
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=name + '-', dir=root))
        total, count = 0, 0
        try:
            for directory, dirs, files in os.walk(source.parent):
                dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '.venv', '__pycache__')]
                for filename in [*dirs, *files]:
                    if (Path(directory) / filename).is_symlink() or (Path(directory) / filename).is_junction():
                        raise ValueError("skill resources cannot contain symlinks")
                for filename in files:
                    item = Path(directory) / filename
                    total += item.stat().st_size
                    count += 1
                    if total > 16 * 1024 * 1024 or count > 1000:
                        raise ValueError("skill package exceeds 16MB or 1000 files")
                    target = staging / item.relative_to(source.parent)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, target)
        except Exception:
            shutil.rmtree(staging)
            raise
        result = register({"id": name, "config": {"kind": "skill",
            "description": args.get("description", source.parent.name),
            "instructions": (staging / source.name).read_text(encoding="utf-8-sig")}})
        with _LOCK:
            installed = _read()
            installed[name]["config"]["resource_root"] = str(staging)
            result["revision"] = _revision(installed[name]["config"])
            _save(installed)
        return {**result, **probe({"id": name, "revision": result["revision"]})}
    if source.suffix.lower() != ".zip":
        raise ValueError("supported formats: SKILL.md or Vigil JS Skill ZIP")
    import base64
    from capability_registry import _skill_install_handler
    return _skill_install_handler({"zip_b64": base64.b64encode(source.read_bytes()).decode("ascii")})


def register_capability(registry):
    from capability_registry import READ, WRITE
    registry.register("external_apps", source="brain.external_apps",
        status=lambda: {"connected": True, "state": "ready", "apps": len(_read())},
        metadata={"description": "安装接入并验证 MCP/CLI/Skill；list 返回持久工具清单，call 重新连接执行。"},
        operations={"list": {"permission": READ, "handler": listing},
                    "skill_read": {"permission": READ, "handler": skill_read},
                    **{name: {"permission": WRITE, "handler": fn} for name, fn in {
                        "register": register, "remove": remove, "probe": probe,
                        "call": call, "install_skill": install_skill}.items()}})
