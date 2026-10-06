"""`crossweft discover` -- find seams that already exist in the code but not on the map.

Writing the model is the real cost of adopting crossweft. Most hand-written
seams leave a fingerprint: the same literal (a route, a header, a protocol
version, a pipe name, an event name) typed into files of two different
languages, or environment variables read by the code but declared somewhere
else. This module lists those candidates, each with a ready-to-paste guard
whose regexes were already tried against the files they name.

It is advisory: candidates are starting points for a human or an agent to
confirm, attach to a link, and commit. It never edits the model.
"""

from __future__ import annotations

import bisect
import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path

from .engine import (Checker, Config, ModelError, inside, load_model, out, validate_schema)

LANGUAGE_BY_SUFFIX = {
    ".go": "go", ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts", ".js": "js",
    ".jsx": "js", ".mjs": "js", ".cjs": "js", ".py": "py", ".c": "c", ".h": "c/c++",
    ".cc": "c++", ".cpp": "c++", ".cxx": "c++", ".hpp": "c++", ".hh": "c++", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".cs": "c#", ".swift": "swift",
    ".rb": "ruby", ".php": "php", ".scala": "scala", ".dart": "dart", ".sh": "shell",
    ".bash": "shell", ".ps1": "powershell", ".psm1": "powershell", ".sql": "sql",
    ".proto": "proto", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".json": "json",
    ".ini": "ini", ".cmake": "cmake", ".gradle": "gradle", ".tf": "terraform", ".qml": "qml",
    ".vue": "vue", ".svelte": "svelte", ".lua": "lua", ".nsi": "nsis", ".iss": "inno",
    ".xml": "xml",
}
ENV_FILE_RE = re.compile(r"(^|/)(\.env\.(example|sample|template|dist)|[^/]*\.env\.example|"
                         r"example\.env|env\.example)$")
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv",
             "target", ".next", ".tox", ".mypy_cache", "third_party", "external"}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "poetry.lock",
              "Cargo.lock", "composer.lock", "Gemfile.lock"}
MAX_BYTES = 1_000_000

# Values that appear everywhere and are never a seam of THIS system.
STOPLIST = {
    "application/json", "text/plain", "text/html", "utf-8", "utf8", "content-type",
    "Content-Type", "Content-Length", "Authorization", "Accept", "User-Agent",
    "Cache-Control", "Accept-Encoding", "Accept-Language", "X-Requested-With", "localhost",
    "http://localhost", "https://localhost", "127.0.0.1", "0.0.0.0", "/", "//", "/v1", "/api",
    "PATH", "HOME", "USER", "TEMP", "TMP", "DEBUG", "NODE_ENV", "GOPATH", "PYTHONPATH",
    "CI", "LANG", "TERM", "SHELL", "PWD", "1.0", "1.0.0", "0.0.0", "0.1.0", "2.0",
}
FS_PREFIXES = ("/usr/", "/etc/", "/tmp/", "/dev/", "/bin/", "/var/", "/home/", "/opt/",
               "/proc/", "/sys/", "/Users/", "/Library/", "/System/", "/private/")

CATEGORY_WEIGHT = {"route": 5, "pipe": 5, "header": 5, "url": 4, "api-version": 4,
                   "event": 3, "env": 3, "version": 2}

STRING_RE = re.compile(r"(?P<q>[\"'`])(?P<v>(?:(?!(?P=q))[^\\\n]|\\.){3,200})(?P=q)")
ENV_READ_RE = re.compile(
    r"(?:os\.(?:Getenv|LookupEnv)\(\s*\"|os\.(?:getenv|environ\.get)\(\s*[\"']|"
    r"os\.environ\[\s*[\"']|process\.env\.|process\.env\[\s*[\"']|env::var\(\s*\"|"
    r"std::getenv\(\s*\"|getenv\(\s*\"|GetEnvironmentVariable\(\s*\"|System\.getenv\(\s*\"|"
    r"ENV\[\s*[\"']|ENV\.fetch\(\s*[\"'])([A-Z][A-Z0-9_]*)")
ENV_DECL_RE = r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*="


def classify(value: str) -> str | None:
    """The kind of cross-component value `value` looks like, or None."""
    if value in STOPLIST or len(value) < 3 or len(value) > 200 or value.strip() != value:
        return None
    if "\\\\.\\pipe\\" in value or "\\\\\\\\.\\\\pipe\\\\" in value or value.endswith(".sock"):
        return "pipe"
    if re.match(r"^(https?|wss?)://[^\s]+$", value):
        return "url"
    if value.startswith("/") and not value.startswith(FS_PREFIXES) and " " not in value:
        if re.match(r"^/[A-Za-z0-9_\-{}:.$/]*[A-Za-z][A-Za-z0-9_\-{}:.$/]*$", value) \
                and not re.search(r"\.(png|jpe?g|svg|css|js|ts|html?|ico|json|txt|md)$", value):
            return "route"
    if re.match(r"^X-[A-Za-z0-9]+(-[A-Za-z0-9]+)*$", value):
        return "header"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return "api-version"
    if re.match(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$", value) and len(value) >= 5:
        return "env"
    if re.match(r"^v?\d+\.\d+\.\d+([-+][0-9A-Za-z.]+)?$", value):
        return "version"
    if re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,4}$", value) \
            and not re.search(r"\.(py|go|ts|js|json|md|txt|yml|yaml|toml|html|css|exe|dll|so|"
                              r"com|org|net|io|dev|app|h|cpp|c|rs)$", value):
        return "event"
    return None


def repo_files(cfg: Config) -> list[str]:
    """Tracked + untracked-but-not-ignored files (git), else a filtered walk."""
    root = cfg.root
    files: list[str] = []
    try:
        proc = subprocess.run(["git", "-C", str(root), "-c", "core.quotePath=false", "ls-files",
                               "--cached", "--others", "--exclude-standard", "-z"],
                              capture_output=True)
        if proc.returncode == 0:
            files = [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]
    except OSError:
        files = []
    if not files:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                files.append(Path(dirpath, name).relative_to(root).as_posix())
    own = (cfg.model_dir.as_posix().rstrip("/") + "/", cfg.output_dir.as_posix().rstrip("/") + "/")
    kept = []
    for rel in sorted(set(files)):
        parts = rel.split("/")
        if any(part in SKIP_DIRS for part in parts[:-1]) or parts[-1] in SKIP_FILES:
            continue
        if rel.startswith(own) or rel == cfg.lock_file.as_posix():
            continue
        if any(fnmatch.fnmatch(rel, pattern) for pattern in cfg.discover_exclude):
            continue
        if language_of(rel) is None:
            continue
        kept.append(rel)
    return kept


def language_of(rel: str) -> str | None:
    if ENV_FILE_RE.search(rel):
        return "env"
    name = rel.rsplit("/", 1)[-1]
    if name == "Dockerfile" or name.startswith("Dockerfile."):
        return "docker"
    return LANGUAGE_BY_SUFFIX.get(Path(name).suffix.lower())


def _read(root: Path, rel: str) -> str | None:
    path = root / rel
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES or not inside(root, path):
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _suggest_regex(text: str, value: str, pos: int, quote: str) -> str | None:
    """A regex that captures `value` at `pos` and captures ONLY `value` everywhere
    it matches in `text`; None when no such regex can be built from the line."""
    line_start = text.rfind("\n", 0, pos) + 1
    before = text[line_start:pos - 1]          # up to, not including, the opening quote
    body = "([^" + re.escape(quote) + r"\n]+)"
    for take in (24, 48, len(before)):
        prefix = before[-take:] if take < len(before) else before
        # start the context on a token boundary so the regex reads naturally
        cut = re.search(r"[A-Za-z_(\[{,:=]", prefix)
        if cut and take < len(before):
            prefix = prefix[cut.start():]
        prefix = prefix.lstrip()
        if not prefix.strip():
            continue
        candidate = re.escape(prefix) + re.escape(quote) + body + re.escape(quote)
        try:
            compiled = re.compile(candidate, re.MULTILINE)
        except re.error:
            continue
        values = {m.group(1) for m in compiled.finditer(text)}
        if values == {value}:
            return candidate
    return None


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48].strip("-") or "value"


def discover(cfg: Config, limit: int = 20) -> dict:
    root = cfg.root
    files = repo_files(cfg)
    if not files:
        raise ModelError("scanned nothing -- no source files found (check discover.exclude in "
                         "crossweft.json)")
    guarded, env_on_map, model_note = guarded_values(cfg)
    languages: dict[str, int] = {}
    occurrences: dict[str, list[tuple[str, int, int, str]]] = {}   # value -> (rel, line, pos, quote)
    env_reads: dict[str, list[tuple[str, int]]] = {}
    env_decls: dict[str, list[tuple[str, int]]] = {}
    texts: dict[str, str] = {}
    items = 0
    for rel in files:
        text = _read(root, rel)
        if text is None:
            continue
        texts[rel] = text
        newlines = [m.start() for m in re.finditer("\n", text)]

        def line(pos: int, _index=newlines) -> int:
            return bisect.bisect_left(_index, pos) + 1

        language = language_of(rel)
        languages[language] = languages.get(language, 0) + 1
        if language == "env":
            for match in re.finditer(ENV_DECL_RE, text, re.MULTILINE):
                env_decls.setdefault(match.group(1), []).append((rel, line(match.start())))
                items += 1
            continue
        for match in ENV_READ_RE.finditer(text):
            env_reads.setdefault(match.group(1), []).append((rel, line(match.start())))
            items += 1
        for match in STRING_RE.finditer(text):
            value = match.group("v")
            if match.group("q") == "`" and "${" in value:
                value = value.split("${", 1)[0]
            if classify(value) is None:
                continue
            items += 1
            occurrences.setdefault(value, []).append(
                (rel, line(match.start()), match.start("v"), match.group("q")))
    candidates = []
    for value, places in occurrences.items():
        langs = {language_of(rel) for rel, _, _, _ in places}
        areas = {rel.split("/", 1)[0] for rel, _, _, _ in places}
        if len(langs) < 2 and len(areas) < 2:
            continue
        if value in guarded:
            continue   # a join, set or anchor already compares it
        category = classify(value)
        score = CATEGORY_WEIGHT[category] * (len(langs) + 0.5 * (len(areas) - 1))
        # one suggested point per language (first occurrence whose regex is exact)
        points, used_langs = [], set()
        for rel, line, pos, quote in places:
            language = language_of(rel)
            if language in used_langs:
                continue
            regex = _suggest_regex(texts[rel], value, pos, quote)
            if regex:
                points.append({"path": rel, "side": language, "regex": regex})
                used_langs.add(language)
        guard = None
        if len(points) >= 2:
            guard = {"id": f"{category}-{_slug(value)}", "name": f"{category} {value}",
                     "points": points[:4]}
        candidates.append({
            "value": value, "category": category, "score": round(score, 1),
            "languages": sorted(langs), "places": [f"{rel}:{line}" for rel, line, _, _ in places][:8],
            "count": len(places), "join": guard,
        })
    candidates.sort(key=lambda c: (-c["score"], -c["count"], c["value"]))
    env = None
    if env_reads and env_decls:
        read_files = sorted({rel for places in env_reads.values() for rel, _ in places})
        decl_files = sorted({rel for places in env_decls.values() for rel, _ in places})
        undeclared = sorted(set(env_reads) - set(env_decls))
        unused = sorted(set(env_decls) - set(env_reads))
        env = {
            "read_by_code": len(env_reads), "declared": len(env_decls),
            "undeclared_now": undeclared, "declared_but_unread": unused,
            "set": {"id": "env-vars", "name": "Environment variables the code reads are declared",
                    "mode": "right-subset",
                    "left": {"label": "declared", "paths": decl_files, "regex": ENV_DECL_RE},
                    "right": {"label": "read by code", "paths": read_files,
                              "regex": ENV_READ_RE.pattern}},
        }
        if env_on_map:
            env["already_on_map"] = True
    return {"scanned": {"files": len(texts), "items": items}, "languages": languages,
            "candidates": candidates[:limit], "total_candidates": len(candidates), "env": env,
            "model_note": model_note}


def guarded_values(cfg: Config) -> tuple[set[str], bool, str | None]:
    """Values the model ALREADY compares: what every join extracts, every set
    member, every anchor literal. A value merely mentioned (a summary, a
    declared identifier) is not guarded and stays a candidate. Returns
    (values, env vars already guarded by a set, note when the model could not
    be read)."""
    try:
        model = load_model(cfg)
    except ModelError as exc:
        return set(), False, f"model not read ({exc}); every candidate is shown"
    if validate_schema(model):
        return set(), False, "model has schema errors; every candidate is shown"
    checker = Checker(model)
    values: set[str] = set()
    for join in model.entities["joins"]:
        for point in join["points"]:
            text = checker.text(point["path"]) or ""
            for match in re.finditer(point["regex"], text, re.MULTILINE):
                values.update(group for group in match.groups() if group)
    env_guarded = False
    for entry in model.entities["sets"]:
        for side in ("left", "right"):
            items, _ = checker._extract_set(entry[side], f"set:{entry['id']}")
            values.update(items)
            if any(re.match(r"^[A-Z][A-Z0-9_]*$", item) for item in items):
                env_guarded = env_guarded or "=" in entry[side].get("regex", "")
    for kind in ("blocks", "links", "data"):
        for item in model.entities[kind]:
            for key in ("anchors", "from_anchors", "to_anchors", "defined_in"):
                for anchor in item.get(key, []):
                    if "find" in anchor:
                        values.update(re.findall(r"[\"'`]([^\"'`]{3,200})[\"'`]", anchor["find"]))
    return values, env_guarded, None


def run_discover(cfg: Config, limit: int, as_json: bool) -> int:
    try:
        report = discover(cfg, limit)
    except ModelError as exc:
        out(f"[ERR] {exc}")
        return 2
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0
    langs = ", ".join(f"{k} {v}" for k, v in sorted(report["languages"].items(),
                                                    key=lambda kv: -kv[1]))
    out(f"CROSSWEFT DISCOVER  files={report['scanned']['files']} ({langs})")
    if report.get("model_note"):
        out(f"[NOTE] {report['model_note']}")
    if report["candidates"]:
        out(f"Values typed into more than one language/area and not yet on the map "
            f"(top {len(report['candidates'])} of {report['total_candidates']}):")
        for index, cand in enumerate(report["candidates"], 1):
            out(f"  {index:>2}. [{cand['category']}] {cand['value']!r}  "
                f"({', '.join(cand['languages'])}; {cand['count']} places)")
            out(f"      at: {', '.join(cand['places'][:4])}"
                + (" ..." if cand["count"] > 4 else ""))
            if cand["join"]:
                out(f"      join: {json.dumps(cand['join'], ensure_ascii=True)}")
            else:
                out("      join: - (no exact regex on two sides; write the points by hand)")
    else:
        out("No cross-language value candidates found.")
    env = report["env"]
    if env and not env.get("already_on_map"):
        out(f"Environment variables: {env['read_by_code']} read by code, {env['declared']} "
            f"declared in env templates.")
        if env["undeclared_now"]:
            out(f"  read but NOT declared right now: {', '.join(env['undeclared_now'])}")
        out(f"  set: {json.dumps(env['set'], ensure_ascii=True)}")
    out("Next: confirm each candidate belongs to a real link between two blocks, add the guard "
        "with \"link\": \"<link-id>\" to the model, then run `crossweft check`.")
    out(f"SCANNED: files={report['scanned']['files']} items={report['scanned']['items']}")
    return 0
