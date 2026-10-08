"""Native route scanners beyond go-chi: express (and fastify), fastapi, flask,
gin, echo. Each fixture under tests/fixtures/routes plants the prefix, group
and mount shapes of one framework plus registrations the scanner cannot read;
every route must be seen, and every unreadable one reported -- never dropped.

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

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "routes"
sys.path.insert(0, str(REPO))

from crossweft import cli, engine  # noqa: E402

META = {"schema": engine.SCHEMA_ID, "title": "t", "lanes": [{"id": "app", "name": "App"}]}
UNMAPPED, UNSCANNABLE = "route:unmapped:", "route:unscannable:"


class Repo(unittest.TestCase):
    """A throwaway repository with one server block that serves routes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="crossweft-routes-")
        self.root = Path(self._tmp.name) / "repo"
        (self.root / "seams" / "model").mkdir(parents=True)
        (self.root / "crossweft.json").write_text("{}", encoding="utf-8")
        self.write("web/api.ts", "export const x = 1;\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def model(self, route_scan: dict, routes: list[str] | None = None) -> None:
        blocks = [{"id": "web", "name": "Web", "kind": "site", "lane": "app", "status": "current",
                   "summary": "s", "code": ["web/"]},
                  {"id": "api", "name": "API", "kind": "service", "lane": "app",
                   "status": "current", "summary": "s", "code": ["server/"],
                   "route_server": True}]
        links = []
        if routes is not None:
            links.append({"id": "web-api", "from": "web", "to": "api", "transport": "https",
                          "status": "current", "summary": "s",
                          "contract": {"name": "API", "enforcement": "shared-code",
                                       "defined_in": [{"path": "web/api.ts", "find": "export"}]},
                          "from_anchors": [{"path": "web/api.ts", "find": "export"}],
                          "to_anchors": [{"path": "web/api.ts", "find": "export"}],
                          "identifiers": {"route": routes}})
        payload = {"meta": {**META, "route_scan": route_scan}, "blocks": blocks, "links": links}
        (self.root / "seams" / "model" / "00.json").write_text(json.dumps(payload),
                                                                encoding="utf-8")

    def fixture(self, name: str, scanner: str, **router) -> engine.CheckResult:
        (self.root / "server").mkdir(exist_ok=True)
        shutil.copy(FIXTURES / name, self.root / "server" / name)
        self.model({"routers": [{"path": f"server/{name}", "scanner": scanner, **router}]})
        return self.evaluate()

    def evaluate(self) -> engine.CheckResult:
        result = engine.evaluate(engine.load_config(self.root))
        self.assertIsNone(result.load_error)
        self.assertEqual(result.schema_errors, [])
        return result

    def check(self) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--root", str(self.root), "check"])
        return code, buffer.getvalue()

    def assertRoutes(self, result: engine.CheckResult, rel: str, routes: set[str],
                     unscannable: set[str], other: tuple[str, ...] = ()) -> None:
        keys = [p.key for p in result.new]
        self.assertEqual({k[len(UNMAPPED):] for k in keys if k.startswith(UNMAPPED)}, routes)
        prefix = f"{UNSCANNABLE}{rel}:"
        self.assertEqual({k[len(prefix):] for k in keys if k.startswith(UNSCANNABLE)},
                         unscannable)
        self.assertEqual([k for k in keys if not k.startswith((UNMAPPED, UNSCANNABLE))],
                         list(other))


class Fixtures(Repo):
    def test_express(self) -> None:
        result = self.fixture("express_app.js", "express")
        both = {f"{m} {v}{p}" for v in ("/v1", "/v2") for m, p in (
            ("GET", "/orders/{id}"), ("POST", "/orders"), ("POST", "/orders/bulk"),
            ("GET", "/carts/{cartId}"), ("PUT", "/carts/{cartId}"),
            ("DELETE", "/admin/users/{id}"))}
        self.assertRoutes(result, "server/express_app.js",
                          both | {"GET /health", "MOUNT /users", "GET /static"},
                          {"get `/items/${kind}`",          # template literal with ${}
                           "use prefix",                    # computed mount prefix
                           "axios.get '/remote/thing'"})    # not a router of this file

    def test_fastify(self) -> None:
        result = self.fixture("fastify_app.js", "express")
        self.assertRoutes(result, "server/fastify_app.js",
                          {"GET /ping", "GET /items/{id}", "HEAD /items/{id}", "MOUNT /users"},
                          set())
        self.assertTrue(any("register(cors)" in line for line in result.info), result.info)

    def test_fastapi(self) -> None:
        result = self.fixture("fastapi_app.py", "fastapi")
        self.assertRoutes(result, "server/fastapi_app.py",
                          {"GET /health", "GET /v1/items/{item_id}", "PUT /v1/items/{item_id}",
                           "POST /v1/items/bulk", "PATCH /v1/items/bulk",
                           "DELETE /v1/items/admin/users/{uid}", "GET /v1/items/admin/ws",
                           "MOUNT /users", "MOUNT /static", "GET /metrics"},
                          {'get f"/v1/{VERSION}/x"',        # f-string path
                           "APIRouter prefix=PREFIX",       # computed router prefix
                           "prefix router"})                # router handed to a helper
        self.assertTrue(any("include_router(users_router)" in line for line in result.info))

    def test_flask(self) -> None:
        result = self.fixture("flask_app.py", "flask")
        self.assertRoutes(result, "server/flask_app.py",
                          {"GET /", "GET /login", "POST /login", "GET /v1/orders/{order_id}",
                           "DELETE /v1/child/items/{rest}", "POST /o", "MOUNT /ext",
                           "GET /ping"},
                          {"route methods=METHODS"})

    def test_gin(self) -> None:
        result = self.fixture("gin_main.go", "gin")
        self.assertRoutes(result, "server/gin_main.go",
                          {"GET /health", "GET /v1/orders/{id}", "DELETE /v1/admin/users/{id}",
                           "POST /v1/admin/deep/x", "PATCH /legacy", "PUT /legacy",
                           "GET /match", "POST /match", "ANY /any", "MOUNT /assets",
                           "GET /favicon.ico", "POST /field"},
                          {"GET ordersPath", "Group prefix", "prefix rg"})

    def test_echo(self) -> None:
        result = self.fixture("echo_main.go", "echo")
        self.assertRoutes(result, "server/echo_main.go",
                          {"GET /", "GET /admin/users/{id}", "GET /admin", "DELETE /things/{id}",
                           "PUT /things/{id}", "PATCH /things/{id}", "MOUNT /static",
                           "GET /favicon.ico", "CONNECT /tunnel", "POST /api/v1/orders"},
                          {"prefix g"})           # a *echo.Group parameter shadows `g`


class Configuration(Repo):
    HELPER = ("module.exports = (app, axios) => {\n"
              "  app.get('/items/:id', getItem);\n"
              "  axios.get('/upstream/x', { timeout: 1 });\n"
              "};\n")

    def test_receivers_name_a_handed_in_router_and_silence_a_client(self) -> None:
        self.write("server/routes.js", self.HELPER)
        self.model({"routers": [{"path": "server/routes.js", "scanner": "express",
                                 "receivers": {"app": "/api", "axios": None}}]})
        self.assertRoutes(self.evaluate(), "server/routes.js", {"GET /api/items/{id}"}, set())

    def test_without_receivers_the_handed_in_router_is_reported(self) -> None:
        self.write("server/routes.js", self.HELPER)
        self.model({"routers": [{"path": "server/routes.js", "scanner": "express"}]})
        self.assertRoutes(self.evaluate(), "server/routes.js", set(),
                          {"app.get '/items/:id'", "axios.get '/upstream/x'"},
                          other=("anchor:route-scan:empty",))   # nothing scanned: no pass

    def test_declared_routes_in_any_spelling_pass_with_a_router_prefix(self) -> None:
        self.write("server/app.py", "from flask import Flask\napp = Flask(__name__)\n\n"
                                    "@app.route('/orders/<int:oid>', methods=['GET', 'PUT'])\n"
                                    "def order(oid):\n    return oid\n")
        self.write("web/api.ts", 'export const url = "/v2/orders/";\n')
        self.model({"routers": [{"path": "server/app.py", "scanner": "flask", "prefix": "/v2"}],
                    "client_globs": ["web/*.ts"],
                    "client_literal_regexes": ["\"(/v2/[^\"]*)\""]},
                   routes=["GET /v2/orders/:oid", "PUT /v2/orders/{id}"])
        code, output = self.check()
        self.assertEqual(code, 0, output)
        self.assertIn("RESULT: PASS", output)

    def test_a_router_file_with_no_registration_is_an_error(self) -> None:
        self.write("server/app.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        self.model({"routers": [{"path": "server/app.py", "scanner": "fastapi"}]})
        result = self.evaluate()
        self.assertIn("route:scan-empty:server/app.py", [p.key for p in result.new])
        self.assertTrue(any(p.fatal for p in result.new if p.key.startswith("route:scan-empty")))
        code, output = self.check()
        self.assertNotEqual(code, 0, output)

    def test_router_search_finds_an_unscanned_router_of_any_kind(self) -> None:
        self.write("server/main.go", 'package main\nfunc main() {\n\te := echo.New()\n'
                                     '\te.GET("/x", h)\n}\n')
        self.write("server/extra.js", "const app = express();\napp.get('/y', h);\n")
        self.write("server/util.js", "module.exports = { get: (k) => k };\n")
        self.model({"routers": [{"path": "server/main.go", "scanner": "echo"}],
                    "router_search": ["server/*"]})
        keys = [p.key for p in self.evaluate().new]
        self.assertIn("route:unscanned-router:server/extra.js", keys)
        self.assertNotIn("route:unscanned-router:server/util.js", keys)
        self.assertNotIn("route:unscanned-router:server/main.go", keys)

    def test_schema_rejects_misplaced_router_keys(self) -> None:
        self.write("server/main.go", "package main\n")
        cases = [
            ({"scanner": "go-chi", "receivers": {"r": ""}}, "receivers only applies"),
            ({"scanner": "gin", "receivers": {"r": "v1"}}, ".receivers: must map"),
            ({"scanner": "gin", "function_prefixes": {"f": "/v1"}},
             "function_prefixes only applies to the go-chi scanner"),
            ({"scanner": "koa"}, "'koa' not one of"),
        ]
        for router, message in cases:
            with self.subTest(router=router):
                self.model({"routers": [{"path": "server/main.go", **router}]})
                result = engine.evaluate(engine.load_config(self.root))
                self.assertTrue(any(message in e for e in result.schema_errors),
                                result.schema_errors)


if __name__ == "__main__":
    unittest.main()
