# Changelog

## 0.1.0 -- first public release

- License: Apache-2.0.
- Public name: Crossweft (`crossweft` package, command and plugin).
- `check --format sarif`: SARIF 2.1.0 for code scanning. Each problem carries
  its key as a partial fingerprint; recorded findings are suppressed results;
  an unreadable model is a failed invocation, not an empty run. Exit codes are
  those of `check`.
- `crossweft baseline --owner --next-step`: adopt in a repository with existing
  drift by recording every current problem as open findings (one per
  category). New drift still fails; each finding goes stale once fixed.
  Problems a finding cannot excuse are refused; an existing file is never
  overwritten.
- `attest` refuses when only some regions of a pair changed since the last
  attestation (the twin was not touched); `--other-side-unchanged` states that
  the twin was re-read and is already equivalent.
- A model that fails schema validation exits 2 with `RESULT: ERROR` (it was
  exit 1 with `RESULT: FAIL (new=0 ...)`), as documented: nothing was checked.
- The stop hook's advice follows the kind of problem (value drift, moved
  anchor, unmapped route, incomplete map, unloadable model) and carries a
  ready-to-fill finding with the problem key.
- `show` accepts the `kind:id` form that check, impact and hooks print
  (including a problem key) and suggests close ids on a miss; `check --help`
  lists the exit codes.
- Input integrity: reject duplicate JSON keys, missing set inputs, ambiguous
  paths and transforms that collapse distinct members.
- Findings identify the exact disagreement, so accepting known drift cannot
  silently accept a later different disagreement in the same guard.
- Go chi scanning covers `Method`/`MethodFunc`, raw strings and normalized
  paths; a two-sided guard must read two distinct component ends.
- Validator timeouts stop the subprocess tree. Hook input is UTF-8; plugin
  launching does not rely on the caller's `PYTHONPATH` layout.
- Agent integration preserves unrelated hooks and reports an unrunnable or
  incomplete harness instead of treating it as successful enforcement.

- Model: blocks, links, flows, data, findings; every link declares
  `contract.enforcement`; hand-written seams need a guard that reads both sides.
- Guards: `join` (every occurrence compared), `set`, and `pair` (fingerprinted
  regions re-attested with a written reason).
- Value transforms for joins and sets: `unescape-c`, `lower`, `strip`,
  `csv-words`, `basename`.
- Checks: anchors, routes (Go chi and regex scanners), data provenance, status,
  coverage, findings that go stale when fixed; paths may never leave the
  repository.
- `crossweft impact`, `crossweft discover`, `crossweft render` (Markdown +
  Mermaid + interactive viewer, English and Russian).
- Agent hooks: Claude Code plugin (tested); Codex, Gemini CLI, Copilot, Cursor
  adapters (experimental).
- `crossweft agents` (run by `init`): the repository carries its own agent
  harness -- Claude Code hooks, an AGENTS.md block, the skill -- and `check`
  fails when it goes missing or out of date. The skill includes mapping a
  repository from scratch: agents write the map, the check keeps it honest.
- `crossweft validators`: a meta-runner that fails any validator that scanned
  nothing or lacks a real self-test.
- GitHub Action, pre-commit hooks, `--format github|json`.

### Joint-kind plugins

- A repository can add its own kinds of joint as plugins (`joints.dir` in
  `crossweft.json`, one `joints_<kind>.py` per kind): `check` runs them,
  `render` writes their pages and generated files, `impact` names the kinds a
  change feeds. `meta.joint_kinds` pins the kinds, so deleting a model file or
  a plugin fails the check instead of turning a gate off. A plugin that fails
  to import, crashes, returns a malformed result or compares nothing is a
  fatal problem, never a pass.
- Hardened after independent review: `sys.exit()` or any non-Ctrl-C
  `BaseException` in a plugin (import, validate, check, render, generated,
  impact, self_test) is a loud error, never an exit 0; generated paths must be
  normalised and may not touch `crossweft.json`, `model_dir`, `output_dir`, the
  lock file, `joints.dir` or the plugin itself (case-folded); modules a plugin's
  import added from its own directory are dropped afterwards, so they cannot
  shadow a host module; `crossweft self-test` runs each plugin's `self_test()`
  and says `joint plugins: none configured` when there are none; wrong-typed
  `problems`/`files`/`items`/`other` and a crash in `render_markdown` or
  `generated` are named errors, and a plugin's own problem with 0 items is
  reported as itself.
- `check` prints every failing problem, including one outside the listed
  categories (before, it was counted in the result but not shown).

### Hardened before release (independent review, 2026-09-30)

Each item below was reproduced first and has a test that failed before the fix.

- Keys: join and pair problems end in a digest of the disagreement
  (`join:api-version:b5dfb7e5`), so a finding excuses only that disagreement
  and a new one fails again.
- Silent passes closed: a repeated JSON key in config, model or lock file is an
  error; every listed set file and glob must yield a file, and a literal path
  containing `[` is read as that file; two files a transform folds into one
  member fail; a guard counts as reading both sides only with files of two
  different ends; go-chi `Method`/`MethodFunc`, raw strings and non-literal
  paths (`route:unscannable`) are seen; directories starting with `_` are
  covered.
- Model paths have one spelling (no `\`, `//`, `./`; wrong case fails), so
  `impact` matches what `check` accepts.
- A regex group that does not take part is no match, not a crash; `render`
  touches only files it generated; a non-UTF-8 file there is an error.
- "Scanned nothing" is an error in every output (`RESULT: ERROR`, JSON
  `ok: false`, a GitHub annotation); at Stop an empty model warns the user
  instead of sending the agent back.
- Route checks index routes by normalised path (2,000 routes: 29 s to under 1 s).
- Hooks: stdin is UTF-8; `crossweft hook` never exits 2, and a hook this
  version does not know is skipped with exit 1 (visible, not blocking); the
  plugin stays quiet only where the project's own hooks actually run; the
  launcher ignores `PYTHONPATH` and a `crossweft/` in the working directory.
- `crossweft agents` is idempotent with any `--command`, leaves other tools'
  hooks and shared groups alone, keeps line endings, indentation and non-ASCII
  text, writes atomically, respects `agent.harness: false`, and adds the
  `@AGENTS.md` import to `CLAUDE.local.md` too. `check` also fails on
  `disableAllHooks`, on a duplicate AGENTS.md block and on a `CLAUDE.local.md`
  that hides AGENTS.md.
- Validators: a timeout or Ctrl-C kills the whole process tree; the report is
  ASCII-safe. `discover --limit` must be at least 1; the Action passes `root`
  through the environment.
