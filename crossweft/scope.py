"""Path matching shared by `check --changed` and the joints.sources check, so
the list that decides a full check and the list that validates it cannot differ."""
from __future__ import annotations

import fnmatch


def scope_hit(changed: str, target: str) -> bool:
    """Whether a guard target (file, directory or glob) may read the changed
    path. Errs towards True, never False: compared case-insensitively (a
    case-insensitive file system globs `Web/a.ts` for `web/*.ts`), and a glob
    is matched part by part, so a changed directory hits a glob that can reach
    into it, and `**` reaches anything below."""
    changed, target = changed.rstrip("/").casefold(), target.rstrip("/").casefold()
    if changed == target or changed.startswith(target + "/") or target.startswith(changed + "/"):
        return True
    if not any(ch in target for ch in "*?["):
        return False
    parts, pattern = changed.split("/"), target.split("/")
    for index, part in enumerate(parts):
        if index >= len(pattern):
            return False            # deeper than the glob reaches
        if pattern[index] == "**":
            return True
        if not fnmatch.fnmatchcase(part, pattern[index]):
            return False
    return True                     # the path, or a directory the glob reaches into
