"""Joint-kind plugins: repository-specific checks the core does not know.

A repository sets ``"joints": {"dir": "<dir>"}`` in ``crossweft.json``. Every
``<dir>/joints_<kind>.py`` there is one kind of joint the map checks (who opens
which port, which feature flags both sides read, ...),
so a new kind never edits crossweft itself. The model carries the kind's
entries under the top-level key ``<kind>``, and ``meta.joint_kinds`` pins the
kinds a model must carry, so deleting a model file or a plugin can never turn a
check off without a red build.

A plugin module defines:

  SECTION = "<kind>"                    must equal the file name's <kind>
  validate(entries) -> list[str]        schema errors (empty when valid)
  check(root, entries, model) -> dict   {"problems": [(key, message, fatal)],
                                         "items": int, "files": iterable of
                                         repo-relative paths, "info": str}
  self_test() -> int                    0 when every planted fault is red

and optionally:

  PAGE = "<name>.md"                    a generated page in output_dir
  TITLE = str | {language: str}         its title (and README link)
  render_markdown(entries) -> str       the page body, below crossweft's header
  generated(root, entries) -> {rel: text}  other files `render` writes and
                                         `check` compares (inside the repository)
  IMPACT_GLOBS = [globs]                 files that feed the kind besides the paths
                                         in its entries (for glob-scoped kinds)
  impact(entries, changed) -> [{"ref": str, "message": str, "other": [paths]}]
                                         richer rows: what to re-read on the other side

Without any of these, `impact` still names a kind whenever a changed file is a
path written in its entries (a string with a '/'), and says so when a kind has
no file references at all, so a plugin gate is never silently left out.

Anything a plugin gets wrong -- an import error, a missing name, a crash or
sys.exit() (any BaseException but KeyboardInterrupt), a malformed result,
entries it never actually checked -- is a loud error, never a silent pass.
Generated paths must be normalised and may not name crossweft's own files
(config, model_dir, output_dir, lock file, joints.dir, the plugin itself).
The plugin's directory is on sys.path only while it is imported, and the modules
that import added from that directory are dropped afterwards. `crossweft
self-test` runs each plugin's self_test().
"""

from __future__ import annotations

import dataclasses
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Iterable

PREFIX = "joints_"
KIND_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
PAGE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*\.md\Z")
REQUIRED = ("SECTION", "validate", "check", "self_test")


@dataclasses.dataclass
class Plugin:
    kind: str
    path: Path
    module: ModuleType

    @property
    def page(self) -> str | None:
        return getattr(self.module, "PAGE", None)

    def title(self, language: str) -> str:
        title = getattr(self.module, "TITLE", None) or self.kind
        if isinstance(title, dict):
            return str(title.get(language) or title.get("en") or self.kind)
        return str(title)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _inside(candidate: object, directory: Path) -> bool:
    try:
        Path(str(candidate)).resolve().relative_to(directory)
    except (OSError, ValueError):
        return False
    return True


def _drop_plugin_modules(before: set[str], directory: Path, keep: str) -> None:
    """Forget every module a plugin's import added from its own directory, so a
    helper there (a csv.py, say) can never stand in for a module the host has
    not imported yet. The plugin module itself stays, under its private name."""
    directory = directory.resolve()
    for name in set(sys.modules) - before:
        if name == keep:
            continue
        module = sys.modules.get(name)
        location = getattr(module, "__file__", None)
        paths = list(getattr(module, "__path__", None) or [])
        if (location and _inside(location, directory)) or any(_inside(p, directory) for p in paths):
            sys.modules.pop(name, None)


def _import(path: Path, kind: str) -> ModuleType:
    """Import one plugin file under a private module name. Its directory is on
    sys.path only while it executes, so it can import its own helpers; the
    modules it added from that directory are dropped afterwards (even when the
    import fails), so a plugin never shadows a module of the host (or of another
    repository). Modules the host had already imported are never touched."""
    name = f"crossweft_joints_{kind}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    directory = str(path.parent)
    before = set(sys.modules)
    sys.path.insert(0, directory)
    sys.modules[name] = module
    failed = True
    try:
        spec.loader.exec_module(module)
        failed = False
    finally:
        try:
            sys.path.remove(directory)
        except ValueError:
            pass
        if failed:
            sys.modules.pop(name, None)
        _drop_plugin_modules(before, path.parent, name)
    return module


def load(root: Path, directory: Path | None, reserved: set[str],
         reserved_pages: set[str]) -> tuple[list[Plugin], list[str]]:
    """Plugins from `directory` (repo-relative), and every problem found."""
    if directory is None:
        return [], []
    base = root / directory
    if not base.is_dir():
        return [], [f"joints.dir {directory.as_posix()} is not a directory"]
    plugins: list[Plugin] = []
    errors: list[str] = []
    pages: dict[str, str] = {}
    for path in sorted(base.glob(f"{PREFIX}*.py")):
        kind = path.stem[len(PREFIX):]
        label = f"joint plugin {(directory / path.name).as_posix()}"
        if not KIND_RE.match(kind):
            errors.append(f"{label}: '{kind}' is not a kind name (lowercase letters, digits, _)")
            continue
        if kind in reserved:
            errors.append(f"{label}: '{kind}' is a crossweft model key -- rename the plugin")
            continue
        try:
            module = _import(path, kind)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 -- a broken plugin (even sys.exit()) fails the map, loudly
            errors.append(f"{label} failed to import: {_describe(exc)}")
            continue
        missing = [name for name in REQUIRED if not hasattr(module, name)]
        if missing:
            errors.append(f"{label} lacks {', '.join(missing)}")
            continue
        if module.SECTION != kind:
            errors.append(f"{label}: SECTION is {module.SECTION!r}, expected {kind!r} "
                          "(the file name is the kind's name)")
            continue
        page = getattr(module, "PAGE", None)
        if page is not None:
            if not isinstance(page, str) or not PAGE_RE.match(page):
                errors.append(f"{label}: PAGE must be a plain .md file name, got {page!r}")
                continue
            if not hasattr(module, "render_markdown"):
                errors.append(f"{label} declares PAGE but has no render_markdown")
                continue
            if page in reserved_pages or page in pages:
                owner = pages.get(page, "crossweft")
                errors.append(f"{label}: PAGE {page} is already generated by {owner}")
                continue
            pages[page] = kind
        plugins.append(Plugin(kind, path, module))
    return plugins, errors


def validate(plugin: Plugin, entries: list) -> list[str]:
    try:
        errors = plugin.module.validate(entries)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        return [f"joint kind '{plugin.kind}': validate crashed: {_describe(exc)}"]
    if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
        return [f"joint kind '{plugin.kind}': validate must return a list of strings"]
    return [f"{plugin.kind}: {error}" for error in errors]


@dataclasses.dataclass
class Outcome:
    problems: list   # (key, message, fatal)
    files: set
    items: int
    info: str


def run_check(plugin: Plugin, root: Path, entries: list, model: object) -> Outcome:
    """One plugin's check, with its result held to the same rules as the core:
    a crash, a malformed result or entries it never checked are fatal."""
    kind = plugin.kind

    def broken(reason: str) -> Outcome:
        return Outcome([(f"joints:broken:{kind}", f"joint kind '{kind}': {reason} -- nothing of "
                         "it was verified", True)], set(), 0, "")

    try:
        result = plugin.module.check(root, entries, model)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        return broken(f"check crashed ({_describe(exc)})")
    if not isinstance(result, dict):
        return broken("check must return a dict")
    raw = result.get("problems", [])
    if not isinstance(raw, (list, tuple)):
        return broken(f"check returned problems={raw!r} (want a list of (key, message, fatal))")
    problems = []
    for row in raw:
        if not (isinstance(row, (tuple, list)) and len(row) == 3 and isinstance(row[0], str)
                and row[0] and isinstance(row[1], str) and isinstance(row[2], bool)):
            return broken(f"check returned a malformed problem {row!r} (want (key, message, fatal))")
        key = row[0] if row[0].startswith(f"{kind}:") else f"{kind}:{row[0]}"
        problems.append((key, row[1], row[2]))
    items = result.get("items", 0)
    if isinstance(items, bool) or not isinstance(items, int) or items < 0:
        return broken(f"check returned items={items!r} (want a count)")
    files = result.get("files", ())
    if not isinstance(files, (list, tuple, set, frozenset)) or not all(isinstance(f, str) for f in files):
        return broken("check returned files that are not a collection of repository-relative "
                      "path strings")
    if items == 0 and not problems:
        # entries present but nothing compared: "checked nothing" never reads as green
        return broken(f"check verified nothing for {len(entries)} entr"
                      f"{'y' if len(entries) == 1 else 'ies'}")
    info = result.get("info", "")
    return Outcome(problems, set(files), items, info if isinstance(info, str) else "")


def pin_problems(pinned: object, plugins: list[Plugin], sections: dict[str, list]
                 ) -> tuple[list[tuple[str, str]], str | None]:
    """`meta.joint_kinds` keeps every registered gate on. Returns (key, message)
    problems and an info line."""
    present = {kind for kind, entries in sections.items() if entries}
    known = {plugin.kind for plugin in plugins}
    problems: list[tuple[str, str]] = []
    if pinned is None:
        for kind in sorted(present):
            problems.append((f"joints:unpinned:{kind}",
                             f"joint kind '{kind}' carries data but meta.joint_kinds is absent -- "
                             "pin the list of joint kinds so no gate can be dropped silently"))
        return problems, None
    if not isinstance(pinned, list) or not all(isinstance(kind, str) for kind in pinned):
        return [("joints:meta", "meta.joint_kinds must be a list of joint-kind names")], None
    for kind in pinned:
        if kind not in known:
            problems.append((f"joints:unknown:{kind}",
                             f"meta.joint_kinds pins '{kind}' but no plugin joints_{kind}.py is "
                             "loaded (joints.dir in crossweft.json) -- it was removed, renamed or "
                             "failed to load"))
        elif kind not in present:
            problems.append((f"joints:empty:{kind}",
                             f"joint kind '{kind}' is pinned but the model has no '{kind}' "
                             "entries (was its model file deleted?) -- to retire a kind, remove "
                             "it from meta.joint_kinds together with its plugin and entries"))
    for kind in sorted(present - set(pinned)):
        problems.append((f"joints:unpinned:{kind}",
                         f"joint kind '{kind}' carries data but is not in meta.joint_kinds -- pin "
                         "it so its gate cannot be dropped silently"))
    return problems, f"joint kinds pinned: {', '.join(sorted(set(pinned))) or '-'}"


def render_markdown(plugin: Plugin, entries: list) -> str:
    """The body of a plugin's page. Any failure is a ValueError naming the kind."""
    try:
        body = plugin.module.render_markdown(entries)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        raise ValueError(f"joint kind '{plugin.kind}': render_markdown crashed: "
                         f"{_describe(exc)}") from exc
    if not isinstance(body, str):
        raise ValueError(f"joint kind '{plugin.kind}': render_markdown must return text")
    return body


def _generated_path_error(rel: object, protected: list[tuple[str, str]]) -> str | None:
    """Why `rel` is not an acceptable generated-file path, None when it is."""
    if not isinstance(rel, str) or not rel:
        return "must be a non-empty string"
    if "\\" in rel or ":" in rel or "\0" in rel or rel.startswith("/"):
        return "must be a repository-relative '/' path (no backslash, colon or leading '/')"
    segments = rel.split("/")
    if any(seg in ("", ".", "..") or seg != seg.rstrip(". ") for seg in segments):
        return "has an empty, '.', '..' or dot/space-ending segment; write it normalised"
    folded = rel.casefold()
    for prefix, reason in protected:
        guard = prefix.casefold().strip("/")
        if guard and guard != "." and (folded == guard or folded.startswith(guard + "/")):
            return f"is inside {prefix}: {reason}"
    return None


def generated(plugin: Plugin, root: Path, entries: list,
              protected: Iterable[tuple[str, str]] = ()) -> dict[str, str]:
    """Extra files a plugin generates. Paths are normalised repository-relative,
    '/'-separated, and never name crossweft's own files: `protected` is
    (repo-relative path or directory, reason) pairs, compared case-folded, and the
    plugin's own file is always protected. Anything else is an error, not a skip."""
    hook = getattr(plugin.module, "generated", None)
    if hook is None or not entries:
        return {}
    kind = plugin.kind
    try:
        files = hook(root, entries)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        raise ValueError(f"joint kind '{kind}': generated crashed: {_describe(exc)}") from exc
    if not isinstance(files, dict):
        raise ValueError(f"joint kind '{kind}': generated must return a dict")
    guard = list(protected)
    try:
        guard.append((plugin.path.resolve().relative_to(root.resolve()).as_posix(),
                      "a plugin may not overwrite its own code"))
    except ValueError:
        pass
    for rel, text in files.items():
        problem = _generated_path_error(rel, guard)
        if problem is None and not isinstance(text, str):
            problem = "must map to text"
        if problem:
            raise ValueError(f"joint kind '{kind}': generated file {rel!r} {problem}")
    return files


def _strings(value: object) -> set[str]:
    found: set[str] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            found.add(current)
        elif isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return found


def references(plugin: Plugin, entries: list) -> set[str]:
    """Paths and globs that feed a kind: path-like strings in its entries (a
    bare word or a lone '*' never matches every change) and IMPACT_GLOBS."""
    refs = {s for s in _strings(entries) if "/" in s and not s.startswith("/")}
    globs = getattr(plugin.module, "IMPACT_GLOBS", ())
    if isinstance(globs, (list, tuple)):
        refs |= {g for g in globs if isinstance(g, str) and g}
    return refs


def impact_rows(plugin: Plugin, entries: list, changed: list[str], hit) -> list[dict]:
    """`hit(changed_path, target)` is the engine's path matcher."""
    if not entries:
        return []
    refs = references(plugin, entries)
    touched = sorted({c for c in changed for r in refs if hit(c, r)})
    rows = []
    if touched:
        rows.append({"kind": plugin.kind, "ref": f"joints:{plugin.kind}",
                     "message": f"changed: {', '.join(touched)}", "other": [],
                     "files": touched})
    elif not refs:
        rows.append({"kind": plugin.kind, "ref": f"joints:{plugin.kind}",
                     "message": "no file references or IMPACT_GLOBS -- impact cannot tell which "
                                "changes feed this kind", "other": [], "files": []})
    hook = getattr(plugin.module, "impact", None)
    if hook is None:
        return rows
    def broken(message: str) -> list[dict]:
        return rows + [{"kind": plugin.kind, "ref": f"joints:broken:{plugin.kind}",
                        "message": message, "other": [], "files": []}]

    try:
        hooked = hook(entries, changed)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 -- shown to the agent, never swallowed
        return broken(f"impact crashed ({_describe(exc)})")
    clean = []
    for row in hooked if isinstance(hooked, list) else [None]:
        if not (isinstance(row, dict) and isinstance(row.get("ref"), str)
                and isinstance(row.get("message"), str)):
            return broken(f"impact returned a malformed row {row!r}")
        other = row.get("other")
        if other is None:
            other = []
        if not isinstance(other, (list, tuple)) or not all(isinstance(p, str) for p in other):
            return broken("impact returned a row whose 'other' is not a list of path "
                          f"strings: {row!r}")
        clean.append({"kind": plugin.kind, "ref": row["ref"], "message": row["message"],
                      "other": list(other), "files": []})
    return rows + clean


def self_test(plugin: Plugin) -> str | None:
    """Run the plugin's own self_test(); None when it returns 0, else why not."""
    try:
        code = plugin.module.self_test()
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        return f"self_test crashed ({_describe(exc)})"
    if isinstance(code, bool) or not isinstance(code, int):
        return f"self_test returned {code!r} (want the integer 0)"
    return None if code == 0 else f"self_test returned {code}"
