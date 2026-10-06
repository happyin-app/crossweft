"""Regression tests for the engine review of 2026-09-30: each class plants the
exact shape a reviewer found, and fails if the engine lets it pass silently.

Run: python -m unittest discover -s tests -v   (stdlib only)
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from crossweft import cli, engine  # noqa: E402

META = {"schema": engine.SCHEMA_ID, "title": "t", "lanes": [{"id": "app", "name": "App"}]}


def run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


def blocks(web_code=("web/",), api_code=("server/",)) -> list[dict]:
    return [
        {"id": "web", "name": "Web", "kind": "site", "lane": "app", "status": "current",
         "summary": "s", "code": list(web_code)},
        {"id": "api", "name": "API", "kind": "service", "lane": "app", "status": "current",
         "summary": "s", "code": list(api_code)},
    ]


def link(**extra) -> dict:
    row = {"id": "web-api", "from": "web", "to": "api", "transport": "https",
           "status": "current", "summary": "s",
           "contract": {"name": "API", "enforcement": "duplicated"},
           "from_anchors": [{"path": "web/api.ts", "find": "export"}],
           "to_anchors": [{"path": "server/main.go", "find": "package"}]}
    row.update(extra)
    return row


SHARED = {"name": "API", "enforcement": "shared-code",
          "defined_in": [{"path": "server/main.go", "find": "package"}]}


class Fixture(unittest.TestCase):
    """A throwaway repository per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="crossweft-review-")
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make(self, files: dict, model: dict, config: dict | None = None,
             meta: dict | None = None) -> Path:
        (self.root / "seams" / "model").mkdir(parents=True, exist_ok=True)
        (self.root / "crossweft.json").write_text(json.dumps(config or {}), encoding="utf-8")
        payload = {"meta": {**META, **(meta or {})}, **model}
        self.model_file().write_text(json.dumps(payload, indent=1), encoding="utf-8")
        for rel, text in files.items():
            self.write(rel, text)
        return self.root

    def model_file(self) -> Path:
        return self.root / "seams" / "model" / "00.json"

    def write(self, rel: str, text) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_bytes(text.encode("utf-8"))

    def check(self, *extra: str) -> tuple[int, str]:
        return run(["--root", str(self.root), "check", *extra])

    def evaluate(self) -> engine.CheckResult:
        return engine.evaluate(engine.load_config(self.root))

    def keys(self) -> list[str]:
        return [p.key for p in self.evaluate().new]


# --------------------------------------------------------------------------- #
# 1. duplicate JSON keys
# --------------------------------------------------------------------------- #

class DuplicateJsonKeys(Fixture):
    def test_a_second_joins_array_in_one_model_file_is_a_load_error(self) -> None:
        self.make({"web/api.ts": 'export const V = "v1";\n',
                   "server/main.go": 'package main\nconst V = "v2"\n'},
                  {"blocks": blocks(), "links": [link()]})
        point = '{{"path": "{}", "side": "{}", "regex": "V = \\"([^\\"]+)\\""}}'
        join = ('{"id": "v", "name": "v", "link": "web-api", "points": ['
                + point.format("web/api.ts", "web") + ","
                + point.format("server/main.go", "api") + "]}")
        text = self.model_file().read_text(encoding="utf-8").rstrip().rstrip("}")
        self.model_file().write_text(text + f',\n "joins": [{join}],\n "joins": []\n}}\n',
                                     encoding="utf-8")
        code, output = self.check()
        self.assertEqual(code, 2, output)
        self.assertIn("duplicate key 'joins'", output)
        self.assertNotIn("RESULT: PASS", output)

    def test_a_duplicate_key_in_crossweft_json_is_refused(self) -> None:
        self.make({}, {"blocks": []})
        (self.root / "crossweft.json").write_text('{"output_dir": "a", "output_dir": "b"}',
                                                  encoding="utf-8")
        with self.assertRaisesRegex(engine.ModelError, "duplicate key 'output_dir'"):
            engine.load_config(self.root)

    def test_a_duplicate_pair_in_the_lock_file_is_refused(self) -> None:
        self.make({}, {"blocks": []})
        entry = '{"regions": {}, "attested_on": "2026-01-01", "reason": "r"}'
        lock = self.root / "seams" / "pairs.lock.json"
        lock.write_text('{"schema": "crossweft.lock.v1", "pairs": {"p": ' + entry + ', "p": '
                        + entry + "}}", encoding="utf-8")
        with self.assertRaisesRegex(engine.ModelError, "duplicate key 'p'"):
            engine.read_lock(engine.load_config(self.root))


# --------------------------------------------------------------------------- #
# 2. set sides must read every listed file
# --------------------------------------------------------------------------- #

class SetSideFiles(Fixture):
    def statuses(self, left_paths: list[str]) -> dict:
        return {"blocks": blocks(), "links": [link()], "sets": [{
            "id": "statuses", "name": "statuses", "link": "web-api", "mode": "left-subset",
            "left": {"label": "web", "paths": left_paths, "regex": "\"(st_[a-z]+)\""},
            "right": {"label": "go", "paths": ["server/main.go"], "regex": "\"(st_[a-z]+)\""}}]}

    FILES = {"web/api.ts": 'export const a = "st_new";\n',
             "web/status.ts": 'export const b = "st_paid";\n',
             "server/main.go": 'package main\nvar s = []string{"st_new", "st_paid"}\n'}

    def test_a_listed_file_that_is_gone_fails(self) -> None:
        self.make(self.FILES, self.statuses(["web/api.ts", "web/status.ts"]))
        self.assertEqual(self.check()[0], 0)
        (self.root / "web/status.ts").unlink()
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("anchor:set:statuses:web/status.ts", output)

    def test_a_glob_that_matches_nothing_is_reported_by_name(self) -> None:
        self.make(self.FILES, self.statuses(["web/*.ts", "web/legacy/*.ts"]))
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("anchor:set:statuses:web/legacy/*.ts", output)

    def test_a_path_with_brackets_is_read_literally(self) -> None:
        model = {"blocks": blocks(), "links": [link()], "sets": [{
            "id": "fields", "name": "fields", "link": "web-api", "mode": "left-subset",
            "left": {"label": "web", "paths": ["web/api.ts", "web/orders/[id]/page.ts"],
                     "regex": "order\\.([a-z_]+)"},
            "right": {"label": "go", "paths": ["server/main.go"], "regex": "json:\"([a-z_]+)\""}}]}
        self.make({"web/api.ts": "export const f = (order) => order.id;\n",
                   "web/orders/[id]/page.ts": "export const g = (order) => order.coupon;\n",
                   "server/main.go": 'package main\ntype O struct { ID string `json:"id"` }\n'},
                  model)
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("set:fields:left-only:coupon", output)


# --------------------------------------------------------------------------- #
# 3. a finding excuses one specific disagreement, not every later one
# --------------------------------------------------------------------------- #

class FindingKeys(Fixture):
    def finding(self, key: str) -> dict:
        return {"id": "SM-001", "title": "known", "severity": "minor", "status": "open",
                "kind": "contract-mismatch", "summary": "s", "detected_by": [key],
                "owner": "me", "next_step": "fix"}

    def record(self, key: str) -> None:
        data = json.loads(self.model_file().read_text(encoding="utf-8"))
        data["findings"] = [self.finding(key)]
        self.model_file().write_text(json.dumps(data), encoding="utf-8")

    VERSION_JOIN = {"id": "api-version", "name": "v", "link": "web-api", "points": [
        {"path": "web/api.ts", "side": "web", "regex": "VERSION = \"([^\"]+)\""},
        {"path": "server/main.go", "side": "api", "regex": "Version = \"([^\"]+)\""},
        {"path": "server/other.go", "side": "api", "regex": "Header = \"([^\"]+)\""}]}

    def versions(self, web: str, main: str, other: str, blank_lines: int = 0) -> dict:
        pad = "\n" * blank_lines
        return {"web/api.ts": f'{pad}export const VERSION = "{web}";\n',
                "server/main.go": f'package main\n{pad}const Version = "{main}"\n',
                "server/other.go": f'package main\n{pad}const Header = "{other}"\n'}

    def recorded_version_drift(self) -> str:
        """web=v1, both server files v2, recorded as a finding; returns its key."""
        self.make(self.versions("v1", "v2", "v2"),
                  {"blocks": blocks(), "links": [link()], "joins": [self.VERSION_JOIN]})
        (key,) = [k for k in self.keys() if k.startswith("join:api-version")]
        self.record(key)
        self.assertEqual(self.check()[0], 0)
        return key

    def test_a_new_join_disagreement_is_not_hidden_by_an_old_finding(self) -> None:
        key = self.recorded_version_drift()
        self.write("server/other.go", 'package main\nconst Header = "v3-typo"\n')
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("join:api-version disagrees", output)
        self.assertIn(f"SM-001:{key}", output)          # the old finding is stale now

    def test_the_same_values_in_other_files_are_a_new_disagreement(self) -> None:
        for label, now in (("server/other.go now agrees with web", ("v1", "v2", "v1")),
                           ("web and server/main.go swapped", ("v2", "v1", "v2"))):
            with self.subTest(label):
                key = self.recorded_version_drift()
                for rel, text in self.versions(*now).items():
                    self.write(rel, text)
                code, output = self.check()
                self.assertEqual(code, 1, output)
                self.assertIn(f"SM-001:{key}", output)

    def test_moving_a_value_to_another_line_keeps_the_join_key(self) -> None:
        key = self.recorded_version_drift()
        for rel, text in self.versions("v1", "v2", "v2", blank_lines=3).items():
            self.write(rel, text)
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn(f"SM-001: {key}", output)

    def test_a_join_key_does_not_depend_on_line_endings(self) -> None:
        join = {"id": "cols", "name": "c", "link": "web-api", "points": [
            {"path": "web/api.ts", "side": "web", "regex": "COLS = ([^;]*);"},
            {"path": "server/main.go", "side": "api", "regex": "Cols = ([^;]*);"}]}
        files = {"web/api.ts": "export\nCOLS = a\nb;\n", "server/main.go": "package\nCols = a\nc;\n"}
        self.make(files, {"blocks": blocks(), "links": [link()], "joins": [join]})
        lf = [k for k in self.keys() if k.startswith("join:cols")]
        for rel, text in files.items():
            self.write(rel, text.replace("\n", "\r\n"))
        crlf = [k for k in self.keys() if k.startswith("join:cols")]
        self.assertEqual(len(lf), 1)
        self.assertEqual(lf, crlf)

    def test_a_later_pair_edit_is_not_hidden_by_an_old_finding(self) -> None:
        pair = {"id": "rounding", "name": "r", "link": "web-api", "regions": [
            {"path": "web/api.ts", "side": "web"}, {"path": "server/main.go", "side": "api"}]}
        self.make({"web/api.ts": "export\n// crossweft:begin rounding\nconst r = (v) => "
                                 "Math.floor(v + 0.5);\n// crossweft:end rounding\n",
                   "server/main.go": "package main\n// crossweft:begin rounding\nfunc r() "
                                     "{ return 0.5 }\n// crossweft:end rounding\n"},
                  {"blocks": blocks(), "links": [link()], "pairs": [pair]})
        code, output = run(["--root", str(self.root), "attest", "rounding", "--reason", "read"])
        self.assertEqual(code, 0, output)
        self.write("server/main.go", (self.root / "server/main.go").read_text(encoding="utf-8")
                   .replace("0.5", "0.49"))
        (key,) = [k for k in self.keys() if k.startswith("pair:changed:rounding")]
        self.record(key)
        self.assertEqual(self.check()[0], 0)
        self.write("web/api.ts", (self.root / "web/api.ts").read_text(encoding="utf-8")
                   .replace("Math.floor(v + 0.5)", "Math.ceil(v)"))
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn(f"SM-001:{key}", output)

    def test_swapping_pair_regions_between_files_is_a_new_state(self) -> None:
        """The pair digest ties each fingerprint to its file: no set-only hole."""
        region = "// crossweft:begin p\n{}\n// crossweft:end p\n"
        pair = {"id": "p", "name": "p", "link": "web-api", "regions": [
            {"path": "web/api.ts", "side": "web"}, {"path": "server/main.go", "side": "api"}]}
        self.make({"web/api.ts": "export\n" + region.format("one"),
                   "server/main.go": "package main\n" + region.format("two")},
                  {"blocks": blocks(), "links": [link()], "pairs": [pair]})
        self.assertEqual(run(["--root", str(self.root), "attest", "p", "--reason", "r"])[0], 0)
        self.write("server/main.go", "package main\n" + region.format("three"))
        (key,) = [k for k in self.keys() if k.startswith("pair:changed:p")]
        self.record(key)
        self.write("web/api.ts", "export\n" + region.format("three"))
        self.write("server/main.go", "package main\n" + region.format("one"))
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn(f"SM-001:{key}", output)


# --------------------------------------------------------------------------- #
# 4. go-chi registrations the scanner must see (or say it cannot)
# --------------------------------------------------------------------------- #

CHI = '''package main

const (
	ordersPath  = "/v1/orders"
	refundsPath = "/v1/refunds"
	reportsPath = "/v1/reports"
)

type Router = chi.Router

type server struct {
	router chi.Router
	admin  Router
	mux    Router
}

func version(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get(headerName) != "" || r.URL.Query().Get(key) != "" {
		return
	}
}

func main() {
	r := chi.NewRouter()
	r.Get("/v1/health", h)
	r.Method(http.MethodDelete, "/v1/admin/wipe", h)
	r.MethodFunc("POST", "/v1/refunds", h)
	r.Get(ordersPath, h)
	r.Get(`/v1/raw`, h)
	r.Route(`/v2`, func(v2 chi.Router) {
		v2.With(limit).
			Post("/carts", h)
	})
}

func newServer() *server {
	s := &server{mux: chi.NewRouter()}
	s.admin = chi.NewRouter()
	return s
}

func (s *server) routes() {
	s.router.Get("/v1/status", h)
	s.router.Post(refundsPath, h)
	s.admin.With(auth).Delete(ordersPath, h)
	s.mux.Put(reportsPath, h)
	app.Router().Patch("/v1/" + id, h)
	app.Handler().Head(reportsPath, h)
	cache.Get(ctx, key)
	client.Post(url, "text/plain", body)
}
'''


class ChiScanner(Fixture):
    def test_every_registration_form_is_seen_or_reported(self) -> None:
        api = blocks()
        api[1]["route_server"] = True
        self.make({"web/api.ts": 'export const u = "/v1/health";\n', "server/main.go": CHI},
                  {"blocks": api, "links": [link(contract=SHARED,
                                                 identifiers={"route": ["GET /v1/health"]})]},
                  meta={"route_scan": {"routers": [{"path": "server/main.go",
                                                    "scanner": "go-chi"}]}})
        keys = self.keys()
        for want in ("route:unmapped:DELETE /v1/admin/wipe", "route:unmapped:POST /v1/refunds",
                     "route:unmapped:GET /v1/raw", "route:unmapped:POST /v2/carts",
                     "route:unmapped:GET /v1/status"):
            self.assertIn(want, keys)
        unscannable = "route:unscannable:server/main.go:"
        self.assertEqual(
            sorted(k[len(unscannable):] for k in keys if k.startswith(unscannable)),
            sorted([
                "Get ordersPath",                   # a router variable
                "Post refundsPath",                 # a router in a struct field
                "Delete ordersPath",                # a field assigned chi.NewRouter(), via With
                "Put reportsPath",                  # a field set in a struct literal
                'Patch "/v1/" + id',                # unknown receiver, path-like argument
                "Head reportsPath",                 # unknown receiver, path constant
            ]))
        # not flagged: r.Header.Get(name) and .Query().Get(key) in a handler (one
        # argument), cache.Get(ctx, key) and client.Post(url, ...) (no path-like argument)


# --------------------------------------------------------------------------- #
# 5. a guard must read both ends, not two files of one end
# --------------------------------------------------------------------------- #

class GuardSides(Fixture):
    BLOCKS = [
        {"id": "app", "name": "App", "kind": "executable", "lane": "app", "status": "current",
         "summary": "s", "code": ["src/"]},
        {"id": "ui", "name": "UI", "kind": "component", "lane": "app", "status": "current",
         "summary": "s", "parent": "app"},
        {"id": "engine", "name": "Engine", "kind": "component", "lane": "app",
         "status": "current", "summary": "s", "parent": "app"},
    ]
    LINK = {"id": "ui-engine", "from": "ui", "to": "engine", "transport": "named-pipe",
            "status": "current", "summary": "s",
            "contract": {"name": "pipe", "enforcement": "duplicated"},
            "from_anchors": [{"path": "src/ui/client.ts", "find": "PIPE"}],
            "to_anchors": [{"path": "src/engine/server.cpp", "find": "PIPE"}]}
    FILES = {"src/ui/client.ts": 'const PIPE = "shop.events";\n',
             "src/ui/const.ts": 'export const PIPE = "shop.events";\n',
             "src/engine/server.cpp": 'const char* PIPE = "shop.events";\n'}

    def join(self, second: str) -> dict:
        return {"id": "pipe-name", "name": "pipe", "link": "ui-engine", "points": [
            {"path": "src/ui/client.ts", "side": "ui", "regex": "PIPE = \"([^\"]+)\""},
            {"path": second, "side": "engine", "regex": "PIPE = \"([^\"]+)\""}]}

    def test_two_files_under_the_shared_parent_code_do_not_guard_the_seam(self) -> None:
        self.make(self.FILES, {"blocks": self.BLOCKS, "links": [self.LINK],
                               "joins": [self.join("src/ui/const.ts")]})
        self.assertIn("seam:unguarded:ui-engine", self.keys())

    def test_the_anchor_files_of_each_end_guard_the_seam(self) -> None:
        self.make(self.FILES, {"blocks": self.BLOCKS, "links": [self.LINK],
                               "joins": [self.join("src/engine/server.cpp")]})
        self.assertNotIn("seam:unguarded:ui-engine", self.keys())

    def test_an_end_without_code_still_owns_its_parents_code(self) -> None:
        """web -> auth, where auth is a code-less part of api: server files count for auth."""
        api = blocks()
        api.append({"id": "auth", "name": "Auth", "kind": "component", "lane": "app",
                    "status": "current", "summary": "s", "parent": "api"})
        join = {"id": "v", "name": "v", "link": "web-auth", "points": [
            {"path": "web/api.ts", "side": "web", "regex": "V = \"([^\"]+)\""},
            {"path": "server/auth.go", "side": "auth", "regex": "V = \"([^\"]+)\""}]}
        self.make({"web/api.ts": 'export const V = "1";\n', "server/main.go": "package main\n",
                   "server/auth.go": 'package main\nconst V = "1"\n'},
                  {"blocks": api, "links": [link(id="web-auth", to="auth")], "joins": [join]})
        self.assertNotIn("seam:unguarded:web-auth", self.keys())


# --------------------------------------------------------------------------- #
# 6. model paths are spelled exactly as git reports them
# --------------------------------------------------------------------------- #

class PathSpelling(Fixture):
    FILES = {"web/api.ts": 'export const VERSION = "v1";\n',
             "server/main.go": 'package main\nconst Version = "v1"\n',
             "web/orders/[id]/page.ts": 'export const VERSION = "v1";\n'}

    def model_with(self, web_path: str) -> dict:
        join = {"id": "api-version", "name": "v", "link": "web-api", "points": [
            {"path": web_path, "side": "web", "regex": "VERSION = \"([^\"]+)\""},
            {"path": "server/main.go", "side": "api", "regex": "Version = \"([^\"]+)\""}]}
        return {"blocks": blocks(), "links": [link()], "joins": [join]}

    def test_backslashes_dot_segments_and_double_slashes_are_schema_errors(self) -> None:
        for bad, word in (("web\\api.ts", "backslash"), ("./web/api.ts", "'.'"),
                          ("web//api.ts", "'//'")):
            with self.subTest(bad=bad):
                self.make(self.FILES, self.model_with(bad))
                code, output = self.check()
                self.assertEqual(code, 2, output)
                self.assertIn("model schema", output)
                self.assertIn(word, output)

    def test_a_path_in_the_wrong_letter_case_fails(self) -> None:
        self.make(self.FILES, self.model_with("Web/API.ts"))
        if not (self.root / "Web/API.ts").exists():
            self.skipTest("case-sensitive file system: the path is simply missing here")
        self.assertIn("path-case:Web/API.ts", self.keys())

    def test_impact_matches_a_bracket_path_literally(self) -> None:
        self.make(self.FILES, self.model_with("web/orders/[id]/page.ts"))
        model = engine.load_model(engine.load_config(self.root))
        report = engine.build_impact(model, ["web/orders/[id]/page.ts"])
        self.assertEqual([row["ref"] for row in report["guards"]], ["join:api-version"])


# --------------------------------------------------------------------------- #
# 7. optional regex groups
# --------------------------------------------------------------------------- #

class OptionalGroups(Fixture):
    FILES = {"web/api.ts": 'export interface Order { id: string }\nexport type Legacy = {}\n',
             "server/main.go": 'package main\ntype Order struct { ID string `json:"id"` }\n'}

    def fields(self, left: dict) -> dict:
        return {"blocks": blocks(), "links": [link()], "sets": [{
            "id": "fields", "name": "f", "link": "web-api", "mode": "equal",
            "left": {"label": "ts", "paths": ["web/api.ts"], **left},
            "right": {"label": "go", "paths": ["server/main.go"], "regex": "json:\"([a-z_]+)\""}}]}

    def test_within_alternative_without_the_group(self) -> None:
        self.make(self.FILES, self.fields({
            "within": "interface Order \\{([^}]*)\\}|type Legacy = \\{\\}",
            "regex": "([a-z_]+):"}))
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_regex_alternative_without_the_group_and_a_transform(self) -> None:
        self.make(self.FILES, self.fields({"regex": "(?:([a-z_]+): string|Legacy)",
                                           "transform": ["lower"]}))
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_route_regexes_with_optional_groups(self) -> None:
        api = blocks()
        api[1]["route_server"] = True
        files = {"web/api.ts": 'export const u = "/v1/orders"; fetch(url)\n',
                 "server/main.go": 'package main\napp.get("/v1/orders", h)\napp.get(dynamic, h)\n'}
        self.make(files, {"blocks": api, "links": [link(
            contract=SHARED, identifiers={"route": ["GET /v1/orders"]})]},
            meta={"route_scan": {
                "routers": [{"path": "server/main.go", "scanner": "regex",
                             "pattern": "app\\.(?P<method>get)\\((?:\"(?P<path>/[^\"]*)\"|dynamic)"}],
                "client_globs": ["web/*.ts"],
                "client_literal_regexes": ["\"(/v1/[^\"]*)\"|fetch\\(url\\)"]}})
        code, output = self.check()
        self.assertEqual(code, 0, output)


# --------------------------------------------------------------------------- #
# 8, 9. render: never overwrite what we did not generate; never crash on bytes
# --------------------------------------------------------------------------- #

class Render(Fixture):
    BASE = {"web/api.ts": "export const a = 1;\n", "server/main.go": "package main\n"}

    def model(self) -> dict:
        return {"blocks": blocks(), "links": [link(contract=SHARED)]}

    def test_a_hand_written_page_that_mentions_the_command_is_kept(self) -> None:
        notes = "# Our notes\n\nHand-written. To refresh the diagrams run `crossweft render`.\n"
        self.make({**self.BASE, "docs/README.md": notes}, self.model(), {"output_dir": "docs"})
        code, output = run(["--root", str(self.root), "render"])
        self.assertEqual(code, 2, output)
        self.assertEqual((self.root / "docs/README.md").read_text(encoding="utf-8"), notes)

    def test_a_generated_page_is_still_recognised_after_the_model_changes(self) -> None:
        self.make(self.BASE, self.model(), {"output_dir": "docs", "language": "ru"})
        self.assertEqual(run(["--root", str(self.root), "render"])[0], 0)
        data = json.loads(self.model_file().read_text(encoding="utf-8"))
        data["blocks"][0]["summary"] = "changed"
        self.model_file().write_text(json.dumps(data), encoding="utf-8")
        code, output = run(["--root", str(self.root), "render"])
        self.assertEqual(code, 0, output)

    def test_a_non_utf8_file_in_the_output_dir_is_an_error_not_a_crash(self) -> None:
        self.make({**self.BASE, "seams/findings.md": "Caf\xe9 notes\n".encode("latin-1")},
                  self.model(), {"check_rendered_docs": True})
        code, output = self.check()
        self.assertEqual(code, 2, output)
        self.assertIn("seams/findings.md", output)
        for argv in (["render"], ["render", "--check"]):
            code, output = run(["--root", str(self.root), *argv])
            self.assertEqual(code, 2, output)
        self.assertEqual((self.root / "seams/findings.md").read_bytes(), b"Caf\xe9 notes\n")


# --------------------------------------------------------------------------- #
# 10. a transform may not fold two files into one member
# --------------------------------------------------------------------------- #

class BasenameCollision(Fixture):
    def test_two_files_with_one_bare_name_are_reported(self) -> None:
        model = {"blocks": blocks(), "links": [link()], "sets": [{
            "id": "payload", "name": "payload", "link": "web-api", "mode": "equal",
            "left": {"label": "installer", "paths": ["web/api.ts"], "regex": "\"([a-z]+\\.dll)\""},
            "right": {"label": "built", "files": ["server/*/*.dll"], "transform": ["basename"]}}]}
        self.make({"web/api.ts": 'export const p = ["core.dll", "net.dll"];\n',
                   "server/main.go": "package main\n", "server/x64/core.dll": "a",
                   "server/x64/net.dll": "b", "server/arm64/core.dll": "c",
                   "server/arm64/net.dll": "d"}, model)
        keys = self.keys()
        self.assertIn("set:payload:collision:core.dll", keys)
        self.assertIn("set:payload:collision:net.dll", keys)


# --------------------------------------------------------------------------- #
# 11. coverage skips only hidden directories
# --------------------------------------------------------------------------- #

class Coverage(Fixture):
    def test_an_underscore_directory_is_a_source_area(self) -> None:
        files = {"web/api.ts": "export\n", "server/main.go": "package\n",
                 "services/_billing/main.py": "x\n", "services/mail/main.py": "x\n",
                 "services/.cache/x": "x\n", "services/__pycache__/x.pyc": "x\n"}
        self.make(files, {"blocks": blocks(), "links": [link(contract=SHARED)]},
                  meta={"coverage": {"roots": ["services/*"]}})
        coverage = sorted(k for k in self.keys() if k.startswith("coverage:"))
        self.assertEqual(coverage, ["coverage:services/_billing", "coverage:services/mail"])


# --------------------------------------------------------------------------- #
# 12. route checks scale with the number of routes, not its square
# --------------------------------------------------------------------------- #

class RouteScaling(Fixture):
    def test_each_route_is_normalised_a_bounded_number_of_times(self) -> None:
        count = 300
        routes = [f"/v1/r{i}/{{id}}/x" for i in range(count)]
        go = ("package main\nfunc main() {\n\tr := chi.NewRouter()\n"
              + "".join(f'\tr.Get("{p}", h)\n' for p in routes) + "}\n")
        ts = "".join(f'export const u{i} = "/v1/r{i}/:id/x";\n' for i in range(count))
        api = blocks()
        api[1]["route_server"] = True
        self.make({"web/api.ts": ts, "server/main.go": go},
                  {"blocks": api, "links": [link(
                      contract=SHARED, identifiers={"route": [f"GET {p}" for p in routes]})]},
                  meta={"route_scan": {"routers": [{"path": "server/main.go", "scanner": "go-chi"}],
                                       "client_globs": ["web/*.ts"],
                                       "client_literal_regexes": ["\"(/v1/[^\"]*)\""]}})
        real = engine.Checker._norm_route_path
        calls = [0]

        def counting(path: str) -> str:
            calls[0] += 1
            return real(path)

        with mock.patch.object(engine.Checker, "_norm_route_path", staticmethod(counting)):
            result = self.evaluate()
        self.assertTrue(result.ok, [p.message for p in result.new])
        # registered + declared + client literals, each a small constant number of times
        self.assertLess(calls[0], 10 * count, calls[0])


# --------------------------------------------------------------------------- #
# 13. "scanned nothing" is never shown as a pass
# --------------------------------------------------------------------------- #

class ScannedNothing(Fixture):
    def setUp(self) -> None:
        super().setUp()
        code, output = run(["--root", str(self.root), "init", "--no-agents"])
        self.assertEqual(code, 0, output)

    def test_text_says_nothing_was_verified(self) -> None:
        code, output = self.check()
        self.assertEqual(code, 2, output)
        self.assertNotIn("RESULT: PASS", output)
        self.assertIn("nothing was verified", output)

    def test_json_is_valid_and_not_ok(self) -> None:
        code, output = self.check("--format", "json")
        self.assertEqual(code, 2, output)
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertIn("scanned nothing", payload["error"])

    def test_github_format_annotates_the_error(self) -> None:
        code, output = self.check("--format", "github")
        self.assertEqual(code, 2, output)
        self.assertIn("::error", output)
        self.assertIn("scanned nothing", output)


if __name__ == "__main__":
    unittest.main()
