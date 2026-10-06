# Model reference

The map lives in `model_dir` (default `seams/model/`) as any number of `*.json`
files, merged in file-name order. Exactly one file carries `meta`. Each file may
hold any of the entity lists below. Unknown keys and unknown fields are errors:
a typo never loads silently.

All paths are relative to the repository root. An absolute path, a path with
`..`, or a symlink that resolves outside the repository is refused. A path is
written exactly as git names the file -- `/` separators, no `./` or `//`, and
the file's own letter case (checked on case-insensitive file systems too,
`path-case:<path>`) -- because `impact` and the agent hooks compare paths as
written.

## `crossweft.json`

At the repository root; it also marks the root. Every key is optional.

| Key | Default | Meaning |
|---|---|---|
| `model_dir` | `seams/model` | where the `*.json` model files live |
| `output_dir` | `seams` | where `crossweft render` writes the generated docs and `viewer.html`; it never overwrites a file there that it did not generate |
| `lock_file` | `<output_dir>/pairs.lock.json` | pair attestations; may not live in `model_dir` |
| `language` | `en` | language of the generated docs and viewer (`en`, `ru`) |
| `viewer_template` | packaged | your own HTML template for `viewer.html` |
| `check_rendered_docs` | `false` | `check` also fails when the generated docs are stale |
| `requirements` | -- | `{"doc": "docs/REQUIREMENTS.md", "id_pattern": "^[A-Z]+-\\d+$"}`: entities may reference requirement ids; each must have a table row `\| ID \|` in the doc |
| `agent` | `{"stop": "block"}` | `stop`: `block` sends the agent back while the check fails, `warn` only tells the user. `harness`: `true` makes `check` fail when the agent harness written by `crossweft agents` (hooks, AGENTS.md block, skill) is missing or out of date; `init` sets it |
| `validators` | -- | `{"dir": "scripts/validators", "timeout": 120, "timeouts": {"validate_x.py": 300}}` for `crossweft validators` |
| `discover` | -- | `{"exclude": ["vendor/**", "**/*_test.go"]}` for `crossweft discover` |
| `joints` | -- | `{"dir": "tools/joints"}`: where your [joint-kind plugins](#joint-kind-plugins) live |

## `meta`

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | `"crossweft.map.v1"` |
| `title` | yes | shown on the generated pages |
| `lanes` | yes | `[{"id", "name", "plane"?}]` -- columns of the map (where a block runs) |
| `planes` | no | `[{"id", "name"}]` -- optional groups of lanes (e.g. runtime vs delivery); when present every lane names its plane |
| `verified_on`, `verified_against` | no | date and commit the map was last reviewed at; `check` then reports which blocks' code changed since |
| `route_scan` | no | see [Routes](#routes) |
| `joint_kinds` | no | the [joint kinds](#joint-kind-plugins) this model must carry, e.g. `["handles", "pipes"]` |
| `coverage` | no | `{"roots": ["src/*"], "ignore": [{"path", "reason"}]}` -- every directory matched by `roots` must be the code of some block (hidden directories, `.name`, and `__pycache__` are skipped; anything else not on the map goes in `ignore` with a reason) |

## `blocks`

A program, service, component, library, store, external service, artifact or tool.

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | kebab-case |
| `name`, `summary` | yes | |
| `kind` | yes | `actor executable service component library store external artifact tool contract site host ci group` |
| `lane` | yes | a lane id |
| `status` | yes | `current planned out-of-scope test-only legacy` |
| `code` | no | files/directories that are this block's code (used by coverage, impact, and to decide which side a file is on) |
| `anchors` | no | claims about the code, see [Anchors](#anchors) |
| `parent` | no | a containing block |
| `route_server` | no | `true` for the block whose routes `route_scan` reads |
| `short`, `owner`, `runs`, `docs`, `evidence`, `waits_for`, `notes`, `requirements` | no | documentation fields |

## `links`

Who passes what to whom.

| Field | Required | Meaning |
|---|---|---|
| `id`, `from`, `to`, `summary` | yes | `from`/`to` are block ids |
| `transport` | yes | `named-pipe https http-loopback process-launch file registry scm in-process generated-code build-input embedded git tunnel smtp sql object-store cache webhook oauth manual ui mtls dpapi docker os-api ssh` |
| `status` | yes | as for blocks; a current link may not touch a legacy/planned block |
| `contract` | when both ends are our code | `{"name", "enforcement", "defined_in"?, "note"?}` -- see below |
| `from_anchors`, `to_anchors` | when that end has code | where each side implements the link |
| `identifiers` | no | `{"route": ["GET /v1/x"], "header": [...], "env": [...], "pipe": [...], ...}` |
| `carries`, `returns` | no | data ids this link carries (`returns`: back to the caller) |
| `label`, `sync`, `waits`, `on_error`, `evidence`, `envs`, `notes`, `requirements` | no | documentation fields |

### `contract.enforcement`

| Value | Who keeps the two sides equal | Rule |
|---|---|---|
| `shared-code` | one symbol both sides import | `defined_in` must name it |
| `generated` | a generator from one source | `defined_in` must name the source |
| `schema-tests` | shared test vectors both sides run | `defined_in` must name them |
| `duplicated` | nobody -- hand-copied on each side | a join/set/pair must read a file of **each** side |
| `convention` | nobody -- agreed by discipline | same |
| `none` | nobody | same, when both ends are our code |

A link of transport `in-process` needs no contract: the compiler checks the call.

## Guards

A guard with `"link": "<id>"` counts for that link's seam only if it reads a
file on the `from` side **and a different** file on the `to` side. Each file is
on one side at most:

- an anchor file of exactly one side (`from_anchors` / `to_anchors`) is on that
  side;
- any other file belongs to the block with the most specific `code` path that
  holds it, and so to the end that is that block or contains it (the deeper
  end, when one end contains the other);
- a file under the code of a block that only *encloses* an end -- an end
  without `code` of its own -- is on that end's side, unless the other end
  inherits the same code. Two parts of one block cannot be told apart by code:
  give them their own `code`, or read their anchor files.

### `joins` -- a value must be equal

```json
{"id": "api-version", "name": "API version", "link": "web-orders", "points": [
  {"path": "web/src/api.ts", "side": "web", "regex": "API_VERSION = \"([^\"]+)\""},
  {"path": "server/main.go", "side": "api", "regex": "APIVersion = \"([^\"]+)\""}]}
```

Each point's regex is applied to its file; **every** match counts, so a second,
drifted definition on one side is caught too. Capture groups are joined with
`,`. Optional per point: `transform` (`unescape-c`, `lower`, `strip`,
`csv-words`, `basename` -- the last path segment), `prefix`, `suffix`, `note`.

### `sets` -- members must match

```json
{"id": "order-fields", "name": "Order JSON fields", "link": "web-orders", "mode": "equal",
 "left":  {"label": "TypeScript", "paths": ["web/src/api.ts"],
           "within": "interface Order \\{([^}]*)\\}", "regex": "^\\s*([a-z_]+)\\??:"},
 "right": {"label": "Go", "paths": ["server/main.go"],
           "within": "type Order struct \\{([^}]*)\\}", "regex": "json:\"([a-z_]+)"}}
```

`mode`: `equal`, `left-subset` (every left member is on the right),
`right-subset`. A side is either `paths` + `regex` (one capture group; optional
`within` narrows each file to regions first; optional `exclude`, `transform`) or
`files` (glob -- the members are the matching file paths; add
`"transform": ["basename"]` to compare them with bare file names; two files
that a transform maps to one member are a problem `set:<id>:collision:<member>`,
because the set could not notice either one disappearing). `allow`:
`[{"item", "side", "reason"}]` for intentional one-sided members; an allow entry
that is no longer needed fails the check.

Every entry of `paths` / `files` must yield a file: a listed file that is
missing, or a glob that matches nothing, fails the check (key
`anchor:set:<id>:<entry>`) -- it cannot silently drop out while the other
entries keep the side non-empty. An entry that names an existing file is read
as that file even when it contains glob characters (`web/orders/[id]/page.ts`).

### `pairs` -- duplicated regions that cannot be compared by value

```json
{"id": "price-rounding", "name": "Price rounding", "link": "web-orders",
 "regions": [{"path": "web/src/pricing.ts", "side": "web"},
             {"path": "server/pricing.go", "side": "api"}]}
```

Each region is the lines between `crossweft:begin <id>` and `crossweft:end <id>`
(in any comment syntax; exactly one of each per file, on separate lines, with a
non-empty region between them), or the whole file with `"whole_file": true`. `crossweft attest <id> --reason "..."` records a
fingerprint of every region (trailing whitespace and line endings ignored) in
the lock file. Any later change to any region fails the check until someone
re-reads the others and attests again. `crossweft attest --all` attests every
changed pair; `--prune` drops lock entries for removed pairs.

## Anchors

`{"path": "server/main.go", "find": "type Order struct"}` or
`{"path": "...", "regex": "..."}`; a path ending in `/` must be a directory. A
missing file or literal always fails -- the map must be corrected -- and cannot
be excused by a finding.

## Routes

```json
"route_scan": {
  "routers": [{"path": "server/main.go", "scanner": "go-chi"},
              {"path": "api/app.py", "scanner": "regex", "prefix": "/v1",
               "pattern": "@app\\.(?P<method>get|post|put|delete)\\(\"(?P<path>/[^\"]*)\""}],
  "router_search": ["server/**/*.go"],
  "ignore_routers": [{"path": "server/debug.go", "reason": "dev only"}],
  "client_globs": ["web/src/**/*.ts"],
  "client_exclude": ["**/*.test.ts"],
  "client_literal_regexes": ["[\"'`](/v1/[^\"'`$]*)"]
}
```

- `go-chi` understands `Get`/`Post`/... , `Handle`/`HandleFunc`, `Method` /
  `MethodFunc` (with an `http.MethodX` constant or a string literal), paths
  written as `"..."` or `` `...` ``, `r.Route("/v1", func...)` nesting, `.Mount`,
  `.Group`, `.With(...)`, and `function_prefixes` (`{"registerV1": "/v1"}` for
  routes registered in a helper). A registration whose path or method is not a
  literal -- a constant, a concatenation -- is a problem
  `route:unscannable:<file>:<Verb> <expression>` when it is called on a router
  (a variable or struct field set from `chi.NewRouter()`, or declared
  `chi.Router` / `*chi.Mux`), or on any other receiver when a handler follows
  a path-like argument (one containing a `"/..."` literal, or a name the file
  sets to one). Write it as a literal, or record a finding with that key.
  `router_search` finds `chi.NewRouter()` files that are neither scanned nor
  ignored.
- `regex` takes a pattern with a named `path` group and an optional `method`
  group (none = `ANY`).
- Parameters compare equal in any spelling: `{id}`, `:id`, `<int:id>`.

Checks, over **current** links only (a planned link may name a route nobody
serves yet; a legacy link does not map a live one): every registered route
belongs to a link's `identifiers.route`; every declared route of a link into the
`route_server` block is registered; every client literal is explained by a
declared route (a literal ending in `/` is a prefix, `*` globs are allowed).

## `data`

`{"id", "name", "kind", "origin", "summary"}` plus optional `defined_in`,
`stored_in`, `consumers`, `also_from`, `anchors`, `sensitivity`. Provenance
rule: data may leave a block over a link only if the block created it (`origin`,
`also_from`) or received it over another mapped link.

## `flows`

`{"id", "name", "status", "summary", "steps": [{"link", "action", "reply"?, "waits"?, "then"?}]}`
-- rendered as sequence diagrams; a current flow may not use a non-current link.

## `findings`

A known mismatch you cannot fix in this change.

| Field | Meaning |
|---|---|
| `id` | like `SM-001` |
| `title`, `summary`, `severity` (`blocker major minor info`), `kind`, `status` (`open accepted closed`) | |
| `detected_by` | problem keys this finding explains (or `manual`) |
| `owner`, `next_step` | required while open or accepted |
| `defer_reason` | required when `accepted`: `missing-data missing-dep arch-decision scope-explosion inaccessible-repo` |
| `closed_by` | required when closed (a commit or evidence) |
| `where`, `evidence`, `impact`, `since`, `notes`, `requirements` | optional |

A problem whose key is in an open finding's `detected_by` is reported as KNOWN
and does not fail the check -- unless it is fatal (a broken anchor, a stale
allow entry, a stale lock entry). A finding whose key no longer fires is
**stale** and fails the check, so the register cannot rot.

`crossweft baseline --owner <who> --next-step "<what next>"` writes one open
finding per category for every problem `check` reports now (into
`90-baseline.json`, never over an existing file) -- the adoption path for a
repository that already has drift.

Copy keys from the `check` output. The key of a join or a pair problem ends in
a short digest of that exact disagreement -- `join:<id>:<digest>` of which value
each point (its file and regex) reads, `pair:changed:<id>:<digest>` of the
current content of each region's file. A finding therefore excuses one
disagreement: when a value changes, or the same values move to other files, or
a region changes again, the new problem fails and the old finding goes stale.
Line numbers and line endings do not change the key, so moving code does not.


## Joint-kind plugins

Some joints are specific to one codebase: which port each service listens
on and each client dials, which feature flags both sides read, which queue
names a producer and a consumer agree on. A plugin teaches `check` one such kind without
changing crossweft. Each `<joints.dir>/joints_<kind>.py` is one kind; the model
carries its entries under the top-level key `<kind>` (in any model file).

```python
SECTION = "ports"                       # must equal the file name's <kind>

def validate(entries): ...              # -> list of schema errors
def check(root, entries, model): ...    # -> {"problems": [(key, message, fatal)],
                                        #     "items": int, "files": [...], "info": str}
def self_test(): ...                    # -> 0 when every planted fault is red

PAGE = "ports.md"                       # optional: a generated page in output_dir
TITLE = {"en": "Ports", "ru": "Порты"}  # its title and README link
def render_markdown(entries): ...       # its body (crossweft adds the header)
def generated(root, entries): ...       # optional: {repo-relative path: text} that
                                        # `render` writes and `check` compares
IMPACT_GLOBS = ["src/**/*.cmake"]       # optional: files that feed the kind
def impact(entries, changed): ...       # optional: [{"ref", "message", "other": [paths]}]
```

`meta.joint_kinds` pins the kinds a model must carry. A pinned kind with no
entries (its model file was deleted) or no plugin (the plugin was deleted or
renamed), and a kind with entries that is not pinned, all fail the check --
so no gate can be turned off by deleting a file. A plugin's problem keys are
prefixed with its kind (`ports:port:api`) and can be excused by findings like
any other key.

A plugin is held to the same rules as crossweft itself: an import error, a
missing name, a crash or `sys.exit()` in any hook (anything but Ctrl-C is caught
and named), a malformed result (`problems`, `files` or `items` of the wrong
type, an `impact` row whose `other` is not a list of path strings), or entries it
never compared (`items` is 0 with no problem of its own) are fatal
`joints:broken:<kind>` problems, a schema error or an `[ERR]`, never a silent
pass and never a raw traceback. A plugin that reports its own problem with
`items` 0 is reported as that problem. A crash in `render_markdown` or
`generated` fails `render` and, with `check_rendered_docs`, `check` (exit 2).

A plugin may not take a crossweft model key as its kind. The files its
`generated` returns must be written normalised (`/`-separated, no empty, `.` or
`..` segment, no backslash, colon or leading `/`) and may not be, or lie under,
`crossweft.json`, `model_dir`, `output_dir`, the lock file, `joints.dir` or the
plugin's own file (compared case-folded). Its directory is on `sys.path` only
while it is imported, and afterwards every module that import added from the
plugin's directory is dropped again, so a helper there (a `csv.py`) cannot stand
in for a host module that was not imported yet. Host modules already imported
are never replaced.

`crossweft self-test` also runs every plugin's `self_test()` (it must return
`0`); with no `crossweft.json`, no `joints.dir` or no plugin it prints
`joint plugins: none configured` so the absence is visible.

`impact` names a kind whenever a changed file is a path written in its entries
(a string containing `/`) or matches `IMPACT_GLOBS`, and says so when a kind has
neither, so a plugin gate is never silently left out of the pre-commit report.
