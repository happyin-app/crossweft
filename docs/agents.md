# Using crossweft with coding agents

Three moments matter, whatever the agent:

| Moment | Command | What the agent gets |
|---|---|---|
| session start | `crossweft hook <agent> session-start` | one paragraph: this repository declares its seams, how many, and the rules |
| after an edit | `crossweft hook <agent> post-edit` | for each seam the edited file sits on: the file on the **other** side to re-read, the guard that compares them, and -- if a guard already disagrees -- the disagreement |
| before "done" | `crossweft hook <agent> stop` | if `crossweft check` fails, the agent is sent back with the reasons |

The stop hook can request a follow-up while the agent makes progress (the set
of failing keys changes), at most four times in a row. It is not an
unconditional block: when it stops making progress, the agent may stop and the
user is told that the check still fails. There is no endless loop and no silent
pass. Set `"agent": {"stop": "warn"}` in
`crossweft.json` to only warn. While the model declares nothing yet
(`crossweft check` exits 2, "scanned nothing") the user is told so at every
stop, but the agent is not sent back: an adopted, not yet mapped repository
would otherwise cost an extra agent turn on every prompt.

The hooks do nothing in a repository without `crossweft.json`, so a global
install is safe. `crossweft hook` never exits 2 (Claude Code reads that as
"block"): an agent or event this crossweft does not know -- a hook
configuration newer than crossweft -- is skipped with exit 1, which Claude
Code shows as a non-blocking hook error (exit 0 would hide the note), and
an unreadable payload or a working directory that does not exist is reported
as "seams were NOT checked".

## The repository carries the harness

The agent loop should not depend on each developer installing something. `crossweft init`
(or `crossweft agents` in a repository that already has a map) commits the
harness into the repository itself, so every agent that opens it picks the
seams up as part of the architecture:

| File | Who reads it | What it does |
|---|---|---|
| `.claude/settings.json` | Claude Code | the three hooks, merged into whatever the file already holds |
| `AGENTS.md` (a marked block) | Codex, Copilot, Cursor, Claude Code | the working rules; an existing `CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md` gets an `@AGENTS.md` import (in the first of them), because Claude Code reads those instead of AGENTS.md when any of them exists ([memory docs](https://code.claude.com/docs/en/memory#agents-md)) |
| `.claude/skills/crossweft/SKILL.md` | Claude Code | the procedures: fix a failing check, attest a pair, map a repository from scratch |

`crossweft agents` never overwrites text that is not its own: the AGENTS.md
block sits between markers, the hooks are merged (a hook group that also holds
someone else's hook is never changed -- crossweft's hook gets a group of its
own), and a skill file it did not generate is left alone with an error. Files
keep their line endings and are replaced atomically; a file that is not UTF-8
is an error, and nothing is written.

How the hooks start crossweft is declared once, as `agent.command` in
`crossweft.json` (default `crossweft`, after the [tagged Git install](../README.md#install)).
`crossweft agents --command 'python -m crossweft'` (or `init --command ...`)
writes the hooks with that launcher and records it there; a plain rerun uses
the recorded one.

`agents` also sets `"agent": {"harness": true}`, unless you set it to `false`
yourself. From then on `crossweft check` fails when a hook is not exactly
`<agent.command> hook claude-code <event>` (removed, or rewritten to `echo`,
`true` or anything else), when `"disableAllHooks": true` turns them all off,
when the AGENTS.md block is edited, duplicated or out of date, when a CLAUDE
file hides AGENTS.md without importing it, or when the skill differs from the
installed crossweft's: the harness is guarded like any other seam, and
upgrading crossweft tells you to re-run `crossweft agents`.

## Claude Code (tested)

Install the plugin:

```
/plugin marketplace add happyin-app/crossweft
/plugin install crossweft@crossweft
```

It registers the three hooks (`SessionStart`, `PostToolUse` on
`Edit|Write|MultiEdit|NotebookEdit`, `Stop`) and a `crossweft` skill. The hooks
run the package bundled with the plugin through `bin/crossweft`, which finds
Python 3.9+ on `PATH`. No `pip install` is needed.

The plugin is for your own machine; the repository's harness (above) is for
everyone who works in it. When both are present the plugin's hooks stay quiet
in that repository, so the agent hears each message once -- but only when the
repository's hooks actually run crossweft there: the session started in the
directory that holds `crossweft.json` (Claude Code reads
`.claude/settings.json` from where the session started), the hooks match
`agent.command`, and `<agent.command> --version`, run in the hook shell (Git
Bash on Windows, `sh` elsewhere), answers `crossweft <version>` within 10
seconds. Anything else -- no `crossweft` on `PATH`, a launcher that is `echo`,
no shell to ask -- and the plugin does the checking itself.

## OpenAI Codex (supported skill plugin)

Install the same public repository as a Codex marketplace, then install its
`crossweft` plugin:

```
codex plugin marketplace add happyin-app/crossweft
codex plugin add crossweft@crossweft
```

The Codex plugin loads the same `crossweft` skill that Claude Code uses. It
does not declare automatic Codex hooks. After a cross-component edit, the skill
has the agent run `crossweft impact <path>` and, before concluding the task,
`crossweft check`. This is deliberate: the supported local Codex plugin hook
example invokes an MCP tool, and Crossweft does not publish an unverified
third-party command-hook configuration. Install the Crossweft CLI using the
[tagged Git install](../README.md#install) before using those commands.
`crossweft agents` still writes
`AGENTS.md`, so Codex receives the repository-level working rules.

## Other agents (experimental)

The adapters below follow each agent's published hook reference as of
2026-09. They are covered by unit tests of the JSON shapes, not yet by
end-to-end runs in each agent -- reports and fixes are welcome.

**Gemini CLI** -- `settings.json`:

```json
{"hooks": {
  "AfterTool": [{"matcher": "write_file|replace",
                 "hooks": [{"type": "command", "command": "crossweft hook gemini post-edit"}]}],
  "AfterAgent": [{"hooks": [{"type": "command", "command": "crossweft hook gemini stop"}]}]
}}
```

**GitHub Copilot (CLI and coding agent)** -- `.github/hooks/crossweft.json`:

```json
{"version": 1, "hooks": {
  "postToolUse": [{"type": "command", "matcher": "edit|create",
                   "bash": "crossweft hook copilot post-edit"}],
  "agentStop": [{"type": "command", "bash": "crossweft hook copilot stop"}]
}}
```

**Cursor** -- `.cursor/hooks.json`. Cursor's `afterFileEdit` cannot send text
to the agent, so only the stop hook is useful: it sends the agent back with a
`followup_message`.

```json
{"version": 1, "hooks": {"stop": [{"command": "crossweft hook cursor stop", "loop_limit": 3}]}}
```

**Aider** -- no hook protocol; use the test command, which Aider feeds back to
the model when it fails: `aider --test-cmd "crossweft check" --auto-test`.

## Any agent: AGENTS.md

`crossweft agents` writes this block for you. For an agent that reads another
file (`GEMINI.md`, `.cursorrules`), paste it there:

```markdown
## Seams (crossweft)

This repository declares where its components must agree -- routes, headers,
protocol versions, DTO fields, env vars, duplicated algorithms -- in
`seams/model/`. Before editing a file that another component depends on, run
`crossweft impact <file>` and re-read the other side it names. Change both
sides together. Before you finish, `crossweft check` must print
`RESULT: PASS`. Never weaken a guard to make it pass; if a drift must stay,
record a finding with an owner and a next step.
```

## CI

GitHub Action (annotates the diff):

```yaml
- uses: actions/checkout@v4
- uses: happyin-app/crossweft@v0.1.0
```

pre-commit:

```yaml
repos:
  - repo: https://github.com/happyin-app/crossweft
    rev: v0.1.0
    hooks:
      - id: crossweft-check
```

Anything else: `crossweft check --format github`, `--format json`,
`--format sarif` (SARIF 2.1.0, for code-scanning uploads), or the plain text
output; exit 0 = consistent, 1 = problems, 2 = the model or config
could not be read.
