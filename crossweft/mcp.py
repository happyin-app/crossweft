"""`crossweft mcp`: a read-only Model Context Protocol server over stdio.

JSON-RPC 2.0, one message per line (MCP 2025-06-18, stdio transport). The
tools only read: check, impact, show, discover, other_side. Each call loads
crossweft.json and the model afresh, so an edit made between two calls is
seen. Nothing is written to disk; anything the engine prints while a tool
runs goes to stderr, because stdout carries only protocol messages.

A tool that could not produce a verdict answers with ``isError: true`` and
says why; a malformed request, an unknown tool or a bad argument is a
JSON-RPC error. No call ever ends in an empty success.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, Callable

from . import __version__, discover, engine

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")   # newest first

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

MAX_DISCOVER = 200

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
             "openWorldHint": False}

PATHS_SCHEMA = {"type": "array", "items": {"type": "string"},
                "description": "repository-relative files or directories (globs allowed)"}

TOOLS = [
    {"name": "check", "title": "Check seams",
     "description": "Run `crossweft check`: verify every guard, anchor, route and finding in "
                    "the model against the code. status is ok (exit 0), problems (exit 1) or "
                    "error (exit 2: no verdict, never a pass). With `paths`, only problems "
                    "located in those paths are listed; counts and status stay those of the "
                    "whole check.",
     "inputSchema": {"type": "object", "properties": {"paths": PATHS_SCHEMA},
                     "additionalProperties": False}},
    {"name": "impact", "title": "Seams a change touches",
     "description": "For changed paths: the seams (links) through them, the files on the "
                    "other side to re-read, and the guards (join/set/pair) that compare them.",
     "inputSchema": {"type": "object", "properties": {"paths": PATHS_SCHEMA},
                     "required": ["paths"], "additionalProperties": False}},
    {"name": "show", "title": "Show one entity",
     "description": "One model entity and its neighbours. `id` is a bare id or kind:id as "
                    "check and impact print it (link:web-orders, join:api-version:ff55dadb).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                     "required": ["id"], "additionalProperties": False}},
    {"name": "discover", "title": "Unmapped seam candidates",
     "description": "Values typed into more than one language or area and not yet guarded "
                    "by the map, best first, each with a suggested join.",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer", "minimum": 1,
                                              "maximum": MAX_DISCOVER, "default": 20}},
                     "additionalProperties": False}},
    {"name": "other_side", "title": "Files to re-read after an edit",
     "description": "Just the files on the other side of every seam the given file sits on: "
                    "what to re-read after editing it. Empty means no seam passes through it.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"], "additionalProperties": False}},
]
for _tool in TOOLS:
    _tool["annotations"] = dict(READ_ONLY)
TOOL_NAMES = {tool["name"]: tool for tool in TOOLS}

INSTRUCTIONS = ("crossweft keeps the seams of this repository (places where components meet "
                "across languages) in agreement. After editing a file, call other_side or "
                "impact and re-read the files on the other side; before concluding, call "
                "check. Every tool is read-only.")


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


class ToolFailure(Exception):
    """The tool ran but produced no verdict: answered with isError: true."""


# --------------------------------------------------------------------------- #
# arguments
# --------------------------------------------------------------------------- #

def _arguments(tool: dict, raw: Any) -> dict:
    """Validate `arguments` against the tool's small schema: unknown keys,
    missing required keys and wrong types are refused, never ignored."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RpcError(INVALID_PARAMS, f"{tool['name']}: arguments must be an object")
    schema = tool["inputSchema"]
    props = schema["properties"]
    unknown = sorted(set(raw) - set(props))
    if unknown:
        raise RpcError(INVALID_PARAMS, f"{tool['name']}: unknown argument(s) "
                       f"{', '.join(unknown)} (accepted: {', '.join(props) or 'none'})")
    for key in schema.get("required", []):
        if key not in raw:
            raise RpcError(INVALID_PARAMS, f"{tool['name']}: missing required argument '{key}'")
    for key, value in raw.items():
        kind = props[key]["type"]
        if kind == "string":
            if not isinstance(value, str) or not value.strip():
                raise RpcError(INVALID_PARAMS, f"{tool['name']}: '{key}' must be a non-empty "
                               "string")
        elif kind == "array":
            if not isinstance(value, list) or not value or \
                    not all(isinstance(v, str) and v.strip() for v in value):
                raise RpcError(INVALID_PARAMS, f"{tool['name']}: '{key}' must be a non-empty "
                               "list of non-empty strings")
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int) or \
                    not props[key]["minimum"] <= value <= props[key]["maximum"]:
                raise RpcError(INVALID_PARAMS, f"{tool['name']}: '{key}' must be a whole number "
                               f"from {props[key]['minimum']} to {props[key]['maximum']}")
    return raw


def _repo_paths(cfg: engine.Config, paths: list) -> list:
    """Repository-relative paths; relative input is taken from the root (an
    MCP server has no meaningful working directory)."""
    try:
        result = engine._normalize_paths(cfg.root, paths)
    except engine.ImpactError as exc:
        raise ToolFailure(str(exc)) from None
    for rel in result:
        problem = engine.path_error(rel)   # '..', backslashes: refused, not matched to nothing
        if problem:
            raise ToolFailure(problem)
    return result


def _load_model(cfg: engine.Config) -> engine.Model:
    try:
        model = engine.load_model(cfg)
    except engine.ModelError as exc:
        raise ToolFailure(f"model not loaded: {exc}") from None
    errors = engine.validate_schema(model)
    if errors:
        raise ToolFailure(f"model schema: {len(errors)} error(s), nothing compared -- run the "
                          "check tool; first: " + "; ".join(errors[:3]))
    return model


# --------------------------------------------------------------------------- #
# tools: each returns (summary text, structured result, is_error)
# --------------------------------------------------------------------------- #

def tool_check(cfg: engine.Config, args: dict) -> tuple:
    result = engine.evaluate(cfg)
    if result.load_error or result.schema_errors:
        status, exit_code = "error", 2
    else:
        status, exit_code = ("ok", 0) if result.ok else ("problems", 1)
    problems = [p.as_dict() for p in result.new]
    known = [p.as_dict() for p in result.known]
    payload = {
        "status": status, "exit_code": exit_code, "error": result.load_error,
        "schema_errors": result.schema_errors,
        "counts": {"new": len(problems), "known": len(known), "stale_findings": len(result.stale),
                   "stale_generated": len(result.render_stale)},
        "model": result.counts,
        "scanned": {"files": result.files, "items": result.items},
    }
    if "paths" in args:
        wanted = _repo_paths(cfg, args["paths"])

        def located(row: dict) -> bool:
            return bool(row.get("path")) and any(engine._path_hit(row["path"], w)
                                                 for w in wanted)
        payload["filter"] = {"paths": wanted,
                             "unlisted_new": sum(1 for p in problems if not located(p))}
        problems = [p for p in problems if located(p)]
        known = [p for p in known if located(p)]
    else:
        payload["stale_findings"] = result.stale
        payload["stale_generated"] = result.render_stale
        payload["info"] = result.info
    payload["new"] = problems
    payload["known"] = known
    lines = [f"crossweft check: {status.upper()} (exit {exit_code}) new={len(result.new)} "
             f"known={len(result.known)} stale_findings={len(result.stale)} "
             f"scanned files={result.files} items={result.items}"]
    if result.load_error:
        lines.append(f"error: {result.load_error}")
    lines += [f"schema: {e}" for e in result.schema_errors]
    if "filter" in payload:
        lines.append(f"listing problems located in: {', '.join(payload['filter']['paths'])} "
                     f"({payload['filter']['unlisted_new']} other new problem(s) not listed)")
    for row in problems:
        where = f" [{row['path']}]" if row.get("path") else ""
        lines.append(f"- {row['message']}{where} (key: {row['key']})")
    if "filter" not in payload:
        lines += [f"- stale finding {entry}" for entry in result.stale]
        if result.render_stale:
            lines.append(f"- generated docs are stale: {', '.join(result.render_stale)}")
    return "\n".join(lines), payload, status == "error"


def tool_impact(cfg: engine.Config, args: dict) -> tuple:
    model = _load_model(cfg)
    report = engine.build_impact(model, _repo_paths(cfg, args["paths"]))
    lines = [f"crossweft impact: {len(report['changed'])} path(s), "
             f"{len(report['links'])} seam(s) touched"]
    for row in report["links"]:
        lines.append(f"- link:{row['link']} {row['from']} -> {row['to']} [{row['transport']}] "
                     f"re-read: {', '.join(row['recheck']) or '-'}; guards: "
                     f"{', '.join(row['guards']) or 'none'}")
    for row in report["guards"]:
        if row.get("other"):
            lines.append(f"- {row['ref']} also reads: {', '.join(row['other'])}")
    for row in report["joints"]:
        lines.append(f"- {row['ref']}: {row['message']}")
    if report["unmapped"]:
        lines.append(f"not on the map: {', '.join(report['unmapped'])}")
    return "\n".join(lines), report, False


def tool_other_side(cfg: engine.Config, args: dict) -> tuple:
    model = _load_model(cfg)
    changed = _repo_paths(cfg, [args["path"]])
    report = engine.build_impact(model, changed)
    files = set()
    for row in report["links"]:
        files.update(row["recheck"])
    for row in report["guards"] + report["joints"]:
        files.update(row.get("other") or [])
    files = sorted(files - set(changed))
    seams = [f"link:{row['link']}" for row in report["links"]] + \
        [row["ref"] for row in report["guards"] + report["joints"]]
    payload = {"path": changed[0], "other_side": files, "via": seams,
               "on_map": changed[0] not in report["unmapped"]}
    if files:
        text = f"After editing {changed[0]}, re-read: " + ", ".join(files)
    elif payload["on_map"]:
        text = f"{changed[0]} is on the map, but no seam through it has another side to re-read"
    else:
        text = f"{changed[0]} is on no seam of the map"
    return text, payload, False


def tool_show(cfg: engine.Config, args: dict) -> tuple:
    model = _load_model(cfg)
    ident = args["id"].strip()
    kinds = engine.ENTITY_KEYS
    singular = {k[:-1] if k.endswith("s") else k: k for k in engine.ENTITY_KEYS}
    prefix, _, rest = ident.partition(":")
    if rest and prefix in singular:   # kind:id, including a problem key's tail
        kinds, ident = (singular[prefix],), rest.split(":")[0]
    matches = []
    for kind in kinds:
        entity = model.by_id[kind].get(ident)
        if not entity:
            continue
        row: dict = {"kind": kind, "id": ident, "file": model.where(entity), "entity": entity}
        if kind == "blocks":
            row["links_out"] = [link["id"] for link in model.entities["links"]
                                if link["from"] == ident]
            row["links_in"] = [link["id"] for link in model.entities["links"]
                               if link["to"] == ident]
            row["children"] = list(model.children(ident))
            row["creates_data"] = [item["id"] for item in model.entities["data"]
                                   if item["origin"] == ident]
        if kind == "data":
            row["carried_by"] = [link["id"] for link in model.entities["links"]
                                 if ident in link.get("carries", [])]
        matches.append(row)
    if not matches:
        known = sorted({i for kind in kinds for i in model.by_id[kind]})
        close = difflib.get_close_matches(ident, known, n=5)
        raise ToolFailure(f"no entity with id '{ident}'"
                          + (f" -- did you mean: {', '.join(close)}?" if close else ""))
    text = "\n".join(f"{m['kind']}:{m['id']} ({m['file']})\n"
                     + json.dumps(m["entity"], ensure_ascii=False, indent=2) for m in matches)
    return text, {"matches": matches}, False


def tool_discover(cfg: engine.Config, args: dict) -> tuple:
    try:
        report = discover.discover(cfg, args.get("limit", 20))
    except engine.ModelError as exc:
        raise ToolFailure(str(exc)) from None
    lines = [f"crossweft discover: top {len(report['candidates'])} of "
             f"{report['total_candidates']} candidate(s), scanned files="
             f"{report['scanned']['files']}"]
    if report.get("model_note"):
        lines.append(f"note: {report['model_note']}")
    for cand in report["candidates"]:
        lines.append(f"- [{cand['category']}] {cand['value']!r} ({', '.join(cand['languages'])};"
                     f" {cand['count']} places)")
    return "\n".join(lines), report, False


HANDLERS: dict = {"check": tool_check, "impact": tool_impact, "show": tool_show,
                  "discover": tool_discover, "other_side": tool_other_side}


# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #

class Server:
    def __init__(self, root: str | None) -> None:
        self.root = root
        self.initialized = False

    def config(self) -> engine.Config:
        root = Path(self.root).resolve() if self.root else engine.find_repo_root(Path.cwd())
        return engine.load_config(root)

    def call_tool(self, params: dict) -> dict:
        name = params.get("name")
        tool = TOOL_NAMES.get(name) if isinstance(name, str) else None
        if tool is None:
            raise RpcError(INVALID_PARAMS, f"unknown tool: {name!r} (tools: "
                           f"{', '.join(TOOL_NAMES)})")
        args = _arguments(tool, params.get("arguments"))
        try:
            # stdout carries protocol messages only: whatever the engine (or a
            # joint plugin) prints while working goes to stderr
            with contextlib.redirect_stdout(sys.stderr):
                text, structured, is_error = HANDLERS[name](self.config(), args)
        except (ToolFailure, engine.ModelError) as exc:
            text, structured, is_error = f"[ERR] {exc}", {"error": str(exc)}, True
        except Exception as exc:  # noqa: BLE001 -- a crash is reported, never a silent success
            message = (f"crossweft crashed in {name} ({type(exc).__name__}: {exc}) -- nothing "
                       "was verified; report this as a crossweft bug")
            text, structured, is_error = f"[ERR] {message}", {"error": message}, True
        return {"content": [{"type": "text", "text": text},
                            {"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
                "structuredContent": structured, "isError": is_error}

    def handle(self, method: str, params: Any) -> Any:
        if params is not None and not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        params = params or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            self.initialized = True
            return {"protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "crossweft", "title": "Crossweft seams",
                                   "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if not self.initialized:
            raise RpcError(INVALID_REQUEST, f"'{method}' before initialize -- send initialize "
                           "first")
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self.call_tool(params)
        raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    def respond(self, line: bytes) -> dict | None:
        """The response to one line, or None for a notification."""
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return _error(None, PARSE_ERROR, f"parse error: {exc}")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or \
                not isinstance(message.get("method"), str):
            ident = message.get("id") if isinstance(message, dict) else None
            if isinstance(message, dict) and "method" not in message and \
                    ("result" in message or "error" in message):
                return None   # a response to a request we never send: ignore
            return _error(ident if _valid_id(ident) else None, INVALID_REQUEST,
                          "invalid request: expected a JSON-RPC 2.0 object with a method "
                          "(batches are not supported)")
        if "id" not in message:
            return None       # notification (notifications/initialized, cancelled, ...)
        ident = message["id"]
        if not _valid_id(ident):
            return _error(None, INVALID_REQUEST, "invalid request: id must be a string or number")
        try:
            return {"jsonrpc": "2.0", "id": ident,
                    "result": self.handle(message["method"], message.get("params"))}
        except RpcError as exc:
            return _error(ident, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001
            return _error(ident, INTERNAL_ERROR, f"internal error: {type(exc).__name__}: {exc}")


def _valid_id(ident: Any) -> bool:
    return isinstance(ident, str) or (isinstance(ident, int) and not isinstance(ident, bool))


def _error(ident: Any, code: int, message: str, data: Any = None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": error}


def serve(root: str | None, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None,
          log: Callable[[str], None] | None = None) -> int:
    """Answer one JSON-RPC message per line until stdin closes. Returns 0."""
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout.buffer
    log = log or (lambda text: sys.stderr.write(text + "\n"))
    server = Server(root)
    log(f"crossweft {__version__} MCP server on stdio (read-only); root: "
        f"{root or 'found from the working directory'}")
    for line in iter(stdin.readline, b""):
        if not line.strip():
            continue
        response = server.respond(line)
        if response is None:
            continue
        # json.dumps escapes newlines inside strings: one message, one line
        stdout.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        stdout.flush()
    return 0
