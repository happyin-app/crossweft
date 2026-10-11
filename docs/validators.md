# Validators that cannot silently pass

Some seams are not "value A equals value B" but a rule over code: *this
identity constant has exactly one source*, *no file calls this deprecated API*,
*every migration has a down step*. Write those as small validators and let
`crossweft validators` run them.

The failure this runner exists for: a validator that quietly stops doing its
job -- a path bug makes it scan zero files, it prints `[OK]`, and the gate
"passes" for weeks precisely because it never ran. A check that can pass
without working is worse than no check.

## The contract

`crossweft validators` discovers every `validate_*.py` in `validators.dir` and
fails the suite when any of them:

1. has no `--self-test`, or its self-test exits non-zero, or its self-test does
   not print `SELF-TEST: checks=<n>` with n > 0 (proof the self-test path ran,
   not the normal one);
2. exits non-zero on a normal run (that is a finding);
3. prints no `SCANNED: files=<n> items=<n>` line, or prints `files=0 items=0`;
4. exceeds its timeout (`validators.timeout`, per-file `validators.timeouts`;
   a timeout entry for a validator that no longer exists is also an error).
   On a timeout the validator and every process it started are killed, so a
   child it left running cannot stretch the wait.

A `validate_*.py` that is not a suite validator -- for example a commit-time
gate that judges one staged change and would misjudge a shared working tree --
goes in `validators.exclude` with the reason: `{"validate_complexity.py":
"commit gate, runs on --staged"}`. Excluded files are listed with their reason
at the top of the report; an entry naming no file, or one without a reason, is
an error.

A validator that is legitimately not applicable to this checkout prints
`APPLICABILITY: <reason>` and is reported as **SKIP**, never PASS -- and only
after the three contracts above held. A bare `APPLICABILITY:` without a reason
fails.

Validators run in parallel (`CROSSWEFT_JOBS=1` forces serial; an invalid value
is an error, not a default). Rows are always reported in name order.

## Template

```python
"""validate_single_service_name.py -- the service name has one source."""
import re, sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = next((p for p in HERE.parents if (p / "crossweft.json").is_file()), None)
if ROOT is None:                                  # robust root, not __file__ depth
    sys.exit("[ERR] no crossweft.json above this validator -- misconfigured")
CANON = "src/common/names.h"
LITERAL = re.compile(r'"MyService"')

def scan(files):
    return [str(f) for f in files if LITERAL.search(f.read_text(errors="replace"))
            and f.as_posix() != (ROOT / CANON).as_posix()]

def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        bad, good = Path(tmp, "bad.cpp"), Path(tmp, "good.cpp")
        bad.write_text('auto n = "MyService";'); good.write_text("auto n = kName;")
        if not scan([bad]) or scan([good]):
            print("[SELF-TEST FAIL] planted bad input not flagged, or good input flagged")
            return 1
    print("SELF-TEST: checks=2")
    return 0

def main(argv):
    if "--self-test" in argv:
        return self_test()
    files = sorted((ROOT / "src").rglob("*.cpp")) + sorted((ROOT / "src").rglob("*.h"))
    if not files:
        print("[ERR] scanned nothing -- misconfigured (expected sources under src/)")
        return 1
    offenders = scan(files)
    for path in offenders:
        print(f"[FAIL] {path}: re-types the service name; use the constant from {CANON}")
    print(f"SCANNED: files={len(files)} items={len(offenders)}")
    return 1 if offenders else 0

sys.exit(main(sys.argv[1:]))
```

Rules of thumb: fail on zero inputs; find the root by walking up to an anchor;
plant one known-bad and one known-good input in the self-test; give every
allowlist entry a reason and fail when an entry is no longer needed.
