"""`crossweft import` -- guards for a seam whose one side is a schema.

The common case: an OpenAPI document or a .proto file describes one side of a
link, and the other side is hand-written (a Go struct, a TypeScript interface,
a Python set of statuses). This module reads the schema, and for each thing it
declares -- the properties of a component schema, the values of an enum, the
API version, the paths -- prints a ready-to-paste `sets` / `joins` entry.

Like `discover`, every regex it prints was tried against the file it names
before it is printed: the schema side must reproduce exactly what the schema
declares, and the hand-written side is the region of `--against` that agrees
best with it. A side that matched nothing is marked and left out of the
paste-ready block. It never edits the model.

OpenAPI is read as JSON only: the standard library has no YAML parser, and a
hand-rolled one would guess. Convert a YAML document to JSON first.
"""

from __future__ import annotations

import json
import re
from json.decoder import scanstring
from pathlib import Path

from .discover import _slug, _suggest_regex
from .engine import Config, inside, out

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
TRANSFORM_OPTIONS = ([], ["lower"])

# Member regexes tried on the hand-written side. Each has exactly one group.
FIELD_REGEXES = (
    r'json:"([^",\s]+)',                                               # Go struct tag
    r"^[ \t]*(?:(?:pub|readonly|public|private|protected|export)\s+)*"
    r"([A-Za-z_$][\w$]*)\??[ \t]*:",                                     # TS, Python, Rust
    r"^[ \t]*(?:val|var|let)\s+([A-Za-z_]\w*)\s*:",                      # Kotlin, Swift
    r"^[ \t]*(?:(?:optional|repeated|required)\s+)?(?:map<[^>]*>|[\w.]+)\s+"
    r"([A-Za-z_]\w*)\s*=\s*\d+",                                         # protobuf
    r"^[ \t]*(?:(?:public|private|protected|internal|final|static|readonly)\s+)*"
    r"[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*(?:[;={]|$)",                    # Java, C#, C++
    r'''["']([^"'\s]+)["']''',                                         # quoted keys
)
VALUE_REGEXES = (
    r'"([^"\n]*)"',
    r"'([^'\n]*)'",
    r'''=\s*["']([^"'\n]+)["']''',
    r"^[ \t]*([A-Z_][A-Z0-9_]*)\s*=\s*-?\d+",                            # protobuf / C enum
    r"^[ \t]*([A-Za-z_]\w*)[ \t]*(?:=[^,\n]*)?,?[ \t]*$",                # bare enum constants
)
ROUTE_REGEXES = (
    r'''["'`](/[^"'`$\s?#]*)''',
)
PROTO_FIELD_REGEX = (r"^[ \t]*(?:(?:optional|repeated|required)\s+)?(?:map\s*<[^>]*>|[\w.]+)\s+"
                     r"([A-Za-z_]\w*)\s*=\s*\d+")
PROTO_VALUE_REGEX = r"^[ \t]*([A-Za-z_]\w*)\s*=\s*-?(?:0[xX][0-9A-Fa-f]+|\d+)"


class ImportError_(Exception):
    """The schema (or the --against file) cannot be read: exit 2."""


# ----------------------------------------------------------------- trying

def extract(text: str, within: str | None, regex: str, transform: list[str]) -> set[str]:
    """The members a set side yields -- the same steps as `check` (engine
    Checker._extract_set): `within` regions first, then every match of the
    one-group regex, then the transforms."""
    regions = [text]
    if within is not None:
        regions = [m.group(1) for m in re.finditer(within, text, re.MULTILINE)
                   if m.group(1) is not None]
    items = set()
    for region in regions:
        for match in re.finditer(regex, region, re.MULTILINE):
            if match.group(1) is not None:
                items.add(_transform(match.group(1), transform))
    return items


def _transform(value: str, transform: list[str]) -> str:
    for name in transform:
        if name == "lower":
            value = value.lower()
    return value


def _indent_re(indent: str) -> str:
    if indent and set(indent) == {" "}:
        return " {%d}" % len(indent)
    if indent and set(indent) == {"\t"}:
        return "\\t{%d}" % len(indent)
    return re.escape(indent)


def _line_indent(text: str, pos: int) -> str | None:
    """The whitespace before `pos` on its line, or None when something else is."""
    start = text.rfind("\n", 0, pos) + 1
    before = text[start:pos]
    return before if before.strip() == "" else None


# ------------------------------------------------------------ hand side

def _header_regex(text: str, open_pos: int) -> str | None:
    """A regex for the code that opens a region: its line up to the bracket,
    or the previous non-empty line when the bracket stands alone."""
    start = text.rfind("\n", 0, open_pos) + 1
    header = text[start:open_pos].strip()
    if not header:
        prev_end = start - 1
        while prev_end > 0:
            prev_start = text.rfind("\n", 0, prev_end) + 1
            header = text[prev_start:prev_end].strip()
            if header:
                break
            prev_end = prev_start - 1
    if not re.search(r"[A-Za-z_]", header):
        return None
    return r"\s+".join(re.escape(token) for token in header.split()) + r"\s*"


def _bracket_regions(text: str, comment_hash: bool) -> list[tuple[int, int]]:
    """(open, close) offsets of every balanced {...} [...] (...) region, skipping
    strings and comments roughly -- every result is tried anyway."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    closers = {v: k for k, v in pairs.items()}
    stack: list[tuple[str, int]] = []
    regions = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            j = i + 1
            while j < n and text[j] != ch and not (text[j] == "\n" and ch != "`"):
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if text.startswith("//", i) or (comment_hash and ch == "#"):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        if text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        if ch in pairs:
            stack.append((ch, i))
        elif ch in closers:
            while stack and stack[-1][0] != closers[ch]:
                stack.pop()
            if stack:
                regions.append((stack.pop()[1], i))
        i += 1
    return regions


def _hand_withins(text: str, rel: str, truth: set[str]) -> list[str | None]:
    """Candidate `within` regexes for the region(s) of the hand-written file
    that mention the schema's members; None = the whole file."""
    need = min(2, len(truth))
    mention = [re.compile(r"(?<![\w$])" + re.escape(t) + r"(?![\w$])") for t in truth]

    def mentions(body: str) -> int:
        return sum(1 for pattern in mention if pattern.search(body))

    withins: list[str | None] = []
    for open_pos, close_pos in _bracket_regions(text, rel.endswith((".py", ".rb", ".sh",
                                                                     ".yaml", ".yml", ".toml"))):
        body = text[open_pos + 1:close_pos]
        if mentions(body) < need:
            continue
        header = _header_regex(text, open_pos)
        if header is None:
            continue
        opener, closer = re.escape(text[open_pos]), re.escape(text[close_pos])
        if text[close_pos] not in body:
            withins.append(f"{header}{opener}([^{closer}]*){closer}")
        indent = _line_indent(text, close_pos)
        if indent is not None and text.rfind("\n", 0, close_pos) > open_pos:
            withins.append(f"{header}{opener}([\\s\\S]*?)\\n{_indent_re(indent)}{closer}")
    # indentation-delimited bodies (Python classes and enums)
    for match in re.finditer(r"^([ \t]*)class\s+(\w+)[^\n]*:[ \t]*$", text, re.MULTILINE):
        end = match.end()
        indent = match.group(1)
        body_end = re.compile(r"^(?!" + _indent_re(indent) + r"[ \t]+\S|[ \t]*$)", re.MULTILINE)
        stop = body_end.search(text, end + 1)
        body = text[end:stop.start() if stop else len(text)]
        if mentions(body) >= need:
            withins.append("^" + _indent_re(indent) + r"class\s+" + re.escape(match.group(2))
                           + r"\b[^\n]*:[ \t]*\n((?:" + _indent_re(indent)
                           + r"[ \t]+[^\n]*\n|[ \t]*\n)*)")
    # one line: `type Status = "a" | "b";`, `STATUSES = ("a", "b")`
    for match in re.finditer(r"^[^\n\"'`]*?([A-Za-z_]\w*)\s*[:=]\s*(?=[^\n]*[\"'`])", text,
                             re.MULTILINE):
        line_end = text.find("\n", match.end())
        rest = text[match.end():len(text) if line_end < 0 else line_end]
        if mentions(rest) >= need:
            header = r"\s+".join(re.escape(t) for t in match.group(0).split())
            withins.append(r"^[ \t]*" + header + r"\s*([^\n;]*)")
    withins.append(None)
    seen, unique = set(), []
    for within in withins:
        if within not in seen:
            seen.add(within)
            unique.append(within)
    return unique


def best_hand_side(text: str, rel: str, truth: set[str], spec_transform: list[str],
                   regexes: tuple[str, ...]) -> dict | None:
    """The hand-written side (within, regex, transform) whose ACTUAL extraction
    agrees best with the schema's members; None when no candidate shares
    enough of them (two, or all of them when the schema has one)."""
    target = {_transform(t, spec_transform) for t in truth}
    need = min(2, len(target))
    best, best_score = None, None
    for within in _hand_withins(text, rel, truth | target):
        for regex in regexes:
            for transform in TRANSFORM_OPTIONS:
                try:
                    got = extract(text, within, regex, transform)
                except re.error:
                    continue
                common = len(got & target)
                if common < need:
                    continue
                score = (common, -len(got ^ target), -len(transform),
                         -(len(within) if within else 10_000), -len(regex))
                if best_score is None or score > best_score:
                    best_score = score
                    best = {"within": within, "regex": regex, "transform": transform,
                            "members": got}
    return best


# --------------------------------------------------------------- OpenAPI

_WS = re.compile(r"[ \t\n\r]*")
_SCALAR = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?|true|false|null")


def json_positions(text: str, start: int = 0) -> dict[tuple, tuple[int, int]]:
    """For every object member: key path -> (offset of the key's opening quote,
    offset after its closing quote). Array elements are path steps too (ints)."""
    positions: dict[tuple, tuple[int, int]] = {}

    def skip(i: int) -> int:
        return _WS.match(text, i).end()

    def value(i: int, path: tuple) -> int:
        i = skip(i)
        ch = text[i]
        if ch == "{":
            i = skip(i + 1)
            if text[i] == "}":
                return i + 1
            while True:
                i = skip(i)
                key_start = i
                key, i = scanstring(text, i + 1)
                positions[path + (key,)] = (key_start, i)
                i = skip(i)
                i = value(i + 1, path + (key,))     # past ':'
                i = skip(i)
                if text[i] == ",":
                    i += 1
                    continue
                return i + 1
        if ch == "[":
            i = skip(i + 1)
            if text[i] == "]":
                return i + 1
            index = 0
            while True:
                i = value(i, path + (index,))
                index += 1
                i = skip(i)
                if text[i] == ",":
                    i += 1
                    continue
                return i + 1
        if ch == '"':
            return scanstring(text, i + 1)[1]
        match = _SCALAR.match(text, i)
        if not match:
            raise ValueError(f"unexpected {ch!r} at offset {i}")
        return match.end()

    value(start, ())
    return positions


def json_chain(text: str, positions: dict, path: tuple) -> str | None:
    """A regex that walks the pretty-printed JSON from the top to the member at
    `path`, each step held inside its parent object by the parent's closing
    brace (a key at the same indentation is a sibling, never a descendant).
    It ends just after the last key's ':'. None when a key is not on a line of
    its own, or the path runs through an array."""
    parts = []
    for depth in range(1, len(path) + 1):
        step = path[:depth]
        if not isinstance(step[-1], str) or step not in positions:
            return None
        key_start, key_end = positions[step]
        indent = _line_indent(text, key_start)
        if indent is None:
            return None
        key = "^" + _indent_re(indent) + re.escape(text[key_start:key_end]) + r"\s*:\s*"
        if parts:
            parent_indent = parts[-1][1]
            parts.append((r"\{(?:(?!\n" + _indent_re(parent_indent) + r"\})[\s\S])*?" + key,
                          indent))
        else:
            parts.append((key, indent))
    return "".join(part for part, _ in parts) if parts else None


def _json_side(text: str, positions: dict, path: tuple, kind: str, truth: set[str],
               child_path: tuple | None = None) -> dict | None:
    """The schema side of a set: within = the object/array at `path`, regex =
    its member keys (kind "keys") or string items (kind "values"). Returned
    only when its extraction equals `truth` exactly."""
    chain = json_chain(text, positions, path)
    if chain is None:
        return None
    if kind == "keys":
        key_start = positions[path][0]
        within = chain + r"\{([\s\S]*?)\n" + _indent_re(_line_indent(text, key_start)) + r"\}"
        indents = {_line_indent(text, positions[path + (name,)][0]) for name in truth}
        if len(indents) != 1 or None in indents:
            return None
        regex = "^" + _indent_re(indents.pop()) + r'"([^"]+)"\s*:'
    else:
        within = chain + r"\[([^\]]*)\]"
        regex = r'"([^"\n]*)"'
    try:
        got = extract(text, within, regex, [])
    except re.error:
        return None
    if got != truth:
        return None
    return {"within": within, "regex": regex}


def read_openapi(root: Path, rel: str) -> tuple[str, list[dict]]:
    """Parse an OpenAPI 3.x JSON document. Returns (text, items) where each item
    is {"kind", "id", "name", "members"|"value", "spec_side"|"spec_point"}."""
    if rel.lower().endswith((".yaml", ".yml")):
        raise ImportError_(f"{rel}: OpenAPI YAML is not supported -- crossweft is stdlib-only and "
                           "Python has no YAML parser; convert it to JSON (e.g. with your API "
                           "tooling) and import the .json")
    text = _read(root, rel, "spec")
    start = 1 if text.startswith("﻿") else 0
    try:
        doc = json.loads(text[start:])
    except ValueError as exc:
        hint = " (it looks like YAML, which is not supported: convert it to JSON)" \
            if re.match(r"\s*(openapi|swagger)\s*:", text[start:]) else ""
        raise ImportError_(f"{rel}: not valid JSON: {exc}{hint}") from None
    if not isinstance(doc, dict) or not str(doc.get("openapi", "")).startswith("3."):
        found = doc.get("openapi", doc.get("swagger")) if isinstance(doc, dict) else None
        raise ImportError_(f"{rel}: not an OpenAPI 3.x document (openapi: {found!r})")
    try:
        positions = json_positions(text, start)
    except (ValueError, IndexError):
        positions = {}
    items: list[dict] = []
    schemas = (doc.get("components") or {}).get("schemas") or {}
    for name, schema in schemas.items() if isinstance(schemas, dict) else []:
        if not isinstance(schema, dict):
            continue
        base = ("components", "schemas", name)
        props = schema.get("properties")
        if isinstance(props, dict) and props:
            members = set(props)
            items.append({"kind": "fields", "id": f"openapi-{_slug(name)}-fields",
                          "name": f"{name} fields", "members": members,
                          "spec_side": _json_side(text, positions, base + ("properties",), "keys",
                                                  members)})
            for prop, sub in props.items():
                values = _string_enum(sub)
                if values:
                    items.append({"kind": "values",
                                  "id": f"openapi-{_slug(name)}-{_slug(prop)}-values",
                                  "name": f"{name}.{prop} values", "members": values,
                                  "spec_side": _json_side(text, positions,
                                                          base + ("properties", prop, "enum"),
                                                          "values", values)})
        values = _string_enum(schema)
        if values:
            items.append({"kind": "values", "id": f"openapi-{_slug(name)}-values",
                          "name": f"{name} values", "members": values,
                          "spec_side": _json_side(text, positions, base + ("enum",), "values",
                                                  values)})
    version = (doc.get("info") or {}).get("version")
    if isinstance(version, str) and version:
        point = None
        chain = json_chain(text, positions, ("info", "version"))
        if chain is not None:
            regex = chain + r'"([^"\n]*)"'
            if {m.group(1) for m in re.finditer(regex, text, re.MULTILINE)} == {version}:
                point = {"path": rel, "side": "spec", "regex": regex}
        items.append({"kind": "version", "id": "openapi-version", "name": "API version",
                      "value": version, "spec_point": point})
    paths = doc.get("paths")
    if isinstance(paths, dict) and paths:
        members = {p for p in paths if p.startswith("/")}
        side = _json_side(text, positions, ("paths",), "keys", members) if members else None
        routes = []
        base = _server_base(doc)
        for path, ops in paths.items():
            for method in HTTP_METHODS:
                if isinstance(ops, dict) and method in ops:
                    routes.append(f"{method.upper()} {base}{path}")
        items.append({"kind": "routes", "id": "openapi-paths", "name": "API paths",
                      "members": members, "spec_side": side, "routes": routes, "base": base})
    return text, items


def _string_enum(schema: object) -> set[str]:
    if not isinstance(schema, dict) or not isinstance(schema.get("enum"), list):
        return set()
    values = schema["enum"]
    return set(values) if values and all(isinstance(v, str) for v in values) else set()


def _server_base(doc: dict) -> str:
    """The path part of the first server URL ('/v1' of 'https://x/v1'), since
    OpenAPI paths are relative to it."""
    servers = doc.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url", ""))
        path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]*", "", url)
        return path.rstrip("/") if "{" not in path else ""
    return ""


# ------------------------------------------------------------------ proto

def strip_proto_comments(text: str) -> str:
    """Comments replaced by spaces (newlines kept), strings left alone."""
    result, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            result.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            result.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise ImportError_("unterminated /* comment")
            result.append(re.sub(r"[^\n]", " ", text[i:j + 2]))
            i = j + 2
        else:
            result.append(ch)
            i += 1
    return "".join(result)


_PROTO_TOKEN = re.compile(r"\s*(?:(\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')|([A-Za-z_][\w.]*)"
                          r"|(-?(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?))|([{}()\[\]<>;=,:.-]))")


def parse_proto(text: str) -> tuple[list[dict], list[dict]]:
    """Messages {"name", "fields", "open", "close"} and enums {"name", "values",
    "open", "close"} (offsets of their braces), nested names qualified with '.'."""
    clean = strip_proto_comments(text)
    tokens: list[tuple[str, int]] = []
    i = 0
    while True:
        match = _PROTO_TOKEN.match(clean, i)
        if not match or match.end() == i:
            if clean[i:].strip():
                raise ImportError_(f"cannot tokenize at line {clean.count(chr(10), 0, i) + 1}")
            break
        tokens.append((match.group(match.lastindex), match.start(match.lastindex)))
        i = match.end()
    messages: list[dict] = []
    enums: list[dict] = []

    def expect_brace(k: int) -> int:
        if k >= len(tokens) or tokens[k][0] != "{":
            raise ImportError_(f"expected '{{' at line {_line(clean, tokens, k)}")
        return k

    def skip_block(k: int) -> int:
        """From an opening brace to just past its closing one."""
        depth = 0
        while k < len(tokens):
            if tokens[k][0] == "{":
                depth += 1
            elif tokens[k][0] == "}":
                depth -= 1
                if depth == 0:
                    return k + 1
            k += 1
        raise ImportError_("unbalanced braces")

    def skip_statement(k: int) -> int:
        depth = 0
        while k < len(tokens):
            tok = tokens[k][0]
            if tok in "[(<{" and len(tok) == 1:
                depth += 1
            elif tok in "])>}" and len(tok) == 1:
                depth -= 1
            elif tok == ";" and depth <= 0:
                return k + 1
            k += 1
        raise ImportError_("missing ';'")

    def enum_body(k: int, name: str) -> int:
        k = expect_brace(k)
        entry = {"name": name, "values": [], "open": tokens[k][1]}
        k += 1
        while k < len(tokens) and tokens[k][0] != "}":
            tok = tokens[k][0]
            if tok in ("option", "reserved"):
                k = skip_statement(k)
            elif tok == ";":
                k += 1
            elif re.match(r"[A-Za-z_]\w*$", tok) and k + 1 < len(tokens) \
                    and tokens[k + 1][0] == "=":
                entry["values"].append(tok)
                k = skip_statement(k)
            else:
                raise ImportError_(f"unexpected {tok!r} in enum {name} at line "
                                   f"{_line(clean, tokens, k)}")
        if k >= len(tokens):
            raise ImportError_(f"enum {name} is not closed")
        entry["close"] = tokens[k][1]
        enums.append(entry)
        return k + 1

    def message_body(k: int, name: str) -> int:
        k = expect_brace(k)
        entry = {"name": name, "fields": [], "open": tokens[k][1]}
        messages.append(entry)
        k += 1
        while k < len(tokens) and tokens[k][0] != "}":
            tok = tokens[k][0]
            if tok == "message":
                k = message_body(k + 2, f"{name}.{tokens[k + 1][0]}")
            elif tok == "enum":
                k = enum_body(k + 2, f"{name}.{tokens[k + 1][0]}")
            elif tok == "oneof":
                k = expect_brace(k + 2) + 1
                while k < len(tokens) and tokens[k][0] != "}":
                    if tokens[k][0] == "option":
                        k = skip_statement(k)
                        continue
                    k = field(k, entry)
                k += 1
            elif tok in ("extend", "group"):
                while k < len(tokens) and tokens[k][0] != "{":
                    k += 1
                k = skip_block(k)
            elif tok in ("option", "reserved", "extensions"):
                k = skip_statement(k)
            elif tok == ";":
                k += 1
            else:
                k = field(k, entry)
        if k >= len(tokens):
            raise ImportError_(f"message {name} is not closed")
        entry["close"] = tokens[k][1]
        return k + 1

    def field(k: int, entry: dict) -> int:
        """[label] type name = number [options];  or  map<K, V> name = number;"""
        start = k
        if tokens[k][0] in ("optional", "repeated", "required"):
            k += 1
        if tokens[k][0] == "map":
            while tokens[k][0] != ">":
                k += 1
        k += 1
        if k + 1 >= len(tokens) or tokens[k + 1][0] != "=" \
                or not re.match(r"[A-Za-z_]\w*$", tokens[k][0]):
            raise ImportError_(f"cannot read the field at line {_line(clean, tokens, start)} "
                               f"in message {entry['name']}")
        entry["fields"].append(tokens[k][0])
        return skip_statement(k)

    k = 0
    while k < len(tokens):
        tok = tokens[k][0]
        if tok == "message":
            k = message_body(k + 2, tokens[k + 1][0])
        elif tok == "enum":
            k = enum_body(k + 2, tokens[k + 1][0])
        elif tok in ("service", "extend"):
            while k < len(tokens) and tokens[k][0] != "{":
                k += 1
            k = skip_block(k)
        elif tok in ("syntax", "edition", "package", "import", "option"):
            k = skip_statement(k)
        elif tok == ";":
            k += 1
        else:
            raise ImportError_(f"unexpected {tok!r} at line {_line(clean, tokens, k)}")
    return messages, enums


def _line(text: str, tokens: list, k: int) -> int:
    pos = tokens[min(k, len(tokens) - 1)][1] if tokens else 0
    return text.count("\n", 0, pos) + 1


def _proto_side(text: str, entry: dict, regex: str, truth: set[str]) -> dict | None:
    """within = the body of this message/enum (by its header line and the
    indentation of its closing brace); kept only if it reproduces `truth`."""
    keyword = "message" if "fields" in entry else "enum"
    short = entry["name"].rsplit(".", 1)[-1]
    header = r"\b" + keyword + r"\s+" + re.escape(short) + r"\s*"
    candidates = []
    indent = _line_indent(text, entry["close"])
    if indent is not None:
        candidates.append(header + r"\{([\s\S]*?)\n" + _indent_re(indent) + r"\}")
    if "}" not in text[entry["open"] + 1:entry["close"]]:
        candidates.append(header + r"\{([^}]*)\}")
    for within in candidates:
        if extract(text, within, regex, []) == truth:
            return {"within": within, "regex": regex}
    return None


def read_proto(root: Path, rel: str) -> tuple[str, list[dict]]:
    text = _read(root, rel, "spec")
    try:
        messages, enums = parse_proto(text)
    except ImportError_ as exc:
        raise ImportError_(f"{rel}: {exc}") from None
    except IndexError:
        raise ImportError_(f"{rel}: ends in the middle of a declaration") from None
    items = []
    for message in messages:
        members = set(message["fields"])
        if members:
            items.append({"kind": "fields", "id": f"proto-{_slug(message['name'])}-fields",
                          "name": f"{message['name']} fields", "members": members,
                          "spec_side": _proto_side(text, message, PROTO_FIELD_REGEX, members)})
    for enum in enums:
        members = set(enum["values"])
        if members:
            items.append({"kind": "values", "id": f"proto-{_slug(enum['name'])}-values",
                          "name": f"{enum['name']} values", "members": members,
                          "spec_side": _proto_side(text, enum, PROTO_VALUE_REGEX, members)})
    return text, items


# ---------------------------------------------------------------- driver

def _read(root: Path, rel: str, what: str) -> str:
    path = root / rel
    if not path.is_file():
        raise ImportError_(f"{what} file not found: {rel}")
    if not inside(root, path):
        raise ImportError_(f"{rel} resolves outside the repository -- refused")
    try:
        return path.read_text(encoding="utf-8")      # as `check` reads it (newlines folded)
    except (OSError, UnicodeDecodeError) as exc:
        raise ImportError_(f"{what} file unreadable: {rel} ({exc})") from None


def _relative(cfg: Config, given: str) -> str:
    path = Path(given)
    path = path if path.is_absolute() else Path.cwd() / path
    try:
        return path.resolve().relative_to(cfg.root.resolve()).as_posix()
    except ValueError:
        raise ImportError_(f"{given} is outside the repository {cfg.root} -- a guard may only "
                           "read files inside it") from None


def suggest(cfg: Config, kind: str, spec: str, against: str, link: str | None) -> dict:
    """The report: one suggestion per schema item, each side marked."""
    spec_rel, against_rel = _relative(cfg, spec), _relative(cfg, against)
    reader = read_openapi if kind == "openapi" else read_proto
    _, items = reader(cfg.root, spec_rel)
    hand_text = _read(cfg.root, against_rel, "--against")
    suggestions = []
    for item in items:
        suggestions.append(_suggestion(item, kind, spec_rel, against_rel, hand_text, link))
    return {"spec": spec_rel, "against": against_rel, "kind": kind, "suggestions": suggestions}


def _suggestion(item: dict, kind: str, spec_rel: str, against_rel: str, hand_text: str,
                link: str | None) -> dict:
    label = "OpenAPI" if kind == "openapi" else "protobuf"
    result = {"id": item["id"], "name": item["name"], "kind": item["kind"]}
    if item["kind"] == "version":
        value = item["value"]
        result["spec"] = "ok" if item["spec_point"] else "no exact regex"
        hand_point = None
        for match in re.finditer(r"([\"'`])" + re.escape(value) + r"\1", hand_text):
            regex = _suggest_regex(hand_text, value, match.start() + 1, match.group(1))
            if regex:
                hand_point = {"path": against_rel, "side": "hand-written", "regex": regex}
                break
        result["hand"] = "ok" if hand_point else "matched nothing"
        result["detail"] = f"version {value!r}"
        if item["spec_point"] and hand_point:
            join = {"id": item["id"], "name": item["name"]}
            if link:
                join["link"] = link
            join["points"] = [item["spec_point"], hand_point]
            result["join"] = join
        return result
    truth = item["members"]
    spec_side = item["spec_side"]
    result["spec"] = "ok" if spec_side else "no exact regex (is the file pretty-printed, " \
                                            "one key per line?)"
    regexes = {"fields": FIELD_REGEXES, "values": VALUE_REGEXES, "routes": ROUTE_REGEXES}
    best = None
    spec_transform: list[str] = []
    for option in TRANSFORM_OPTIONS:
        found = best_hand_side(hand_text, against_rel, truth, option, regexes[item["kind"]])
        if found and (best is None or len(found["members"] & {_transform(t, option)
                                                               for t in truth})
                      > len(best["members"] & {_transform(t, spec_transform) for t in truth})):
            best, spec_transform = found, option
    if item["kind"] == "routes":
        result["routes"] = item["routes"]
    if best is None:
        result["hand"] = "matched nothing"
        result["detail"] = f"{len(truth)} in the schema: {', '.join(sorted(truth)[:12])}"
        return result
    target = {_transform(t, spec_transform) for t in truth}
    missing = sorted(target - best["members"])
    extra = sorted(best["members"] - target)
    result["hand"] = "ok"
    mode = "right-subset" if item["kind"] == "routes" else "equal"
    result["detail"] = (f"{len(target)} in the schema, {len(best['members'])} in {against_rel}"
                        + (f"; only in the schema: {', '.join(missing)}" if missing else "")
                        + (f"; only in {against_rel}: {', '.join(extra)}" if extra else ""))
    fails = (missing or extra) if mode == "equal" else extra
    result["agrees_now"] = not fails
    if not spec_side:
        return result
    left = {"label": label, "paths": [spec_rel], "within": spec_side["within"],
            "regex": spec_side["regex"]}
    if spec_transform:
        left["transform"] = spec_transform
    right = {"label": "hand-written", "paths": [against_rel]}
    if best["within"] is not None:
        right["within"] = best["within"]
    right["regex"] = best["regex"]
    if best["transform"]:
        right["transform"] = best["transform"]
    entry = {"id": item["id"], "name": item["name"]}
    if link:
        entry["link"] = link
    entry.update({"mode": mode, "left": left, "right": right})
    result["set"] = entry
    return result


def run_import(cfg: Config, kind: str, spec: str, against: str, link: str | None,
               as_json: bool) -> int:
    try:
        report = suggest(cfg, kind, spec, against, link)
    except ImportError_ as exc:
        out(f"[ERR] {exc}")
        return 2
    suggestions = report["suggestions"]
    ready = {"sets": [s["set"] for s in suggestions if "set" in s],
             "joins": [s["join"] for s in suggestions if "join" in s]}
    matched = any(s.get("hand") == "ok" for s in suggestions)
    any_ready = bool(ready["sets"] or ready["joins"])
    if as_json:
        print(json.dumps({**report, "ready": ready}, indent=2, ensure_ascii=True,
                         default=sorted))
        return 0 if any_ready else 1
    out(f"CROSSWEFT IMPORT {kind}  spec={report['spec']}  against={report['against']}")
    if not suggestions:
        out(f"[FAIL] {report['spec']} declares no schema properties, enums, version or paths "
            "to guard")
        return 1
    for s in suggestions:
        state = "READY" if ("set" in s or "join" in s) else "NOT READY"
        drift = "" if s.get("agrees_now", True) else "  (disagrees now: check will fail)"
        out(f"  [{state}] {s['id']}: {s['detail']}{drift}")
        out(f"      schema side: {s['spec']}; hand-written side: {s['hand']}")
        if s.get("routes"):
            out(f"      identifiers.route for the link: {json.dumps(s['routes'])}")
    if any_ready:
        out("Paste into a model file (every regex above was tried against the file it names; "
            "review the scope before adding):")
        print(json.dumps({key: value for key, value in ready.items() if value}, indent=2,
                         ensure_ascii=True))
    elif not matched:
        out(f"[FAIL] the schema was read, but nothing in {report['against']} matched it")
        return 1
    else:
        out(f"[FAIL] {report['against']} matched, but no exact regex reproduces the schema side "
            "(an OpenAPI file must be pretty-printed, one key per line)")
        return 1
    out("Next: set \"link\" (or pass --link), add the guards to the model, run `crossweft check`.")
    return 0
