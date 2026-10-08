"""`crossweft import`: guards for a seam whose one side is an OpenAPI or .proto
schema. The suggested guard must make `check` pass as pasted, and then catch a
planted drift on either side.

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
DEMO = REPO / "examples" / "polyglot-shop"
FIXTURES = REPO / "tests" / "fixtures" / "importers"
sys.path.insert(0, str(REPO))

from crossweft import cli, importers  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


class ImportIntoDemoTest(unittest.TestCase):
    """The OpenAPI document sits with the web client; the Go server is hand-written."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="crossweft-import-")
        self.root = Path(self.tmp.name) / "shop"
        shutil.copytree(DEMO, self.root)
        shutil.copy(FIXTURES / "openapi.json", self.root / "web" / "openapi.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"test setup: {old!r} not in {rel}")
        path.write_bytes(text.replace(old, new).encode("utf-8"))

    def imported(self, against: str) -> tuple[int, dict]:
        code, output = run(["--root", str(self.root), "import", "openapi",
                            str(self.root / "web" / "openapi.json"),
                            "--against", str(self.root / against), "--link", "web-orders",
                            "--json"])
        return code, json.loads(output)

    def paste(self, ready: dict) -> None:
        model = self.root / "seams" / "model" / "20-imported.json"
        model.write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")
        code, output = run(["--root", str(self.root), "render"])
        self.assertEqual(code, 0, output)

    def check(self) -> tuple[int, str]:
        return run(["--root", str(self.root), "check"])

    def test_pasted_guards_pass_then_catch_drift(self) -> None:
        code, report = self.imported("server/main.go")
        self.assertEqual(code, 0, report)
        by_id = {s["id"]: s for s in report["suggestions"]}
        self.assertEqual(sorted(s["id"] for s in report["ready"]["sets"]),
                         ["openapi-order-fields", "openapi-order-status-values", "openapi-paths"])
        self.assertEqual([j["id"] for j in report["ready"]["joins"]], ["openapi-version"])
        # Error has no counterpart in main.go: marked, and left out of the paste block
        self.assertEqual(by_id["openapi-error-fields"]["hand"], "matched nothing")
        self.assertNotIn("set", by_id["openapi-error-fields"])
        self.assertEqual(by_id["openapi-paths"]["routes"], ["GET /v1/orders/{id}",
                                                            "POST /v1/orders"])
        self.paste(report["ready"])
        code, output = self.check()
        self.assertEqual(code, 0, output)

        # drift on the hand-written side: a renamed JSON field
        self.edit("server/main.go", 'json:"total_cents"', 'json:"total"')
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("set:openapi-order-fields:left-only:total_cents", output)
        self.assertIn("set:openapi-order-fields:right-only:total", output)
        self.edit("server/main.go", 'json:"total"', 'json:"total_cents"')

        # drift on the schema side: a new status and a new version
        self.edit("web/openapi.json", '"shipped"\n', '"shipped",\n              "lost"\n')
        self.edit("web/openapi.json", '"version": "2026-09-01"', '"version": "2026-10-01"')
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("set:openapi-order-status-values:left-only:lost", output)
        self.assertIn("join:openapi-version:", output)

    def test_typescript_and_python_sides(self) -> None:
        code, report = self.imported("web/src/api.ts")
        self.assertEqual(code, 0)
        ids = {s["id"] for s in report["ready"]["sets"]}
        self.assertIn("openapi-order-fields", ids)
        self.assertIn("openapi-order-status-values", ids)
        code, report = self.imported("worker/worker.py")
        self.assertEqual(code, 0)
        self.assertEqual([s["id"] for s in report["ready"]["sets"]],
                         ["openapi-order-status-values"])
        self.assertEqual(report["ready"]["joins"], [])

    def test_drift_already_present_is_reported(self) -> None:
        self.edit("server/main.go", 'json:"status"', 'json:"state"')
        code, output = run(["--root", str(self.root), "import", "openapi",
                            str(self.root / "web" / "openapi.json"),
                            "--against", str(self.root / "server" / "main.go")])
        self.assertEqual(code, 0, output)
        self.assertIn("only in the schema: status", output)
        self.assertIn("disagrees now", output)

    def test_minified_spec_has_no_exact_schema_regex(self) -> None:
        spec = self.root / "web" / "openapi.json"
        spec.write_text(json.dumps(json.loads(spec.read_text(encoding="utf-8"))),
                        encoding="utf-8")
        code, output = run(["--root", str(self.root), "import", "openapi", str(spec),
                            "--against", str(self.root / "server" / "main.go")])
        self.assertEqual(code, 1, output)
        self.assertIn("pretty-printed", output)

    def test_unreadable_specs_exit_2(self) -> None:
        web = self.root / "web"
        (web / "api.yaml").write_text("openapi: 3.0.0\ninfo:\n  version: '1'\n",
                                      encoding="utf-8")
        (web / "broken.json").write_text("{\"openapi\": ", encoding="utf-8")
        (web / "swagger.json").write_text('{"swagger": "2.0"}', encoding="utf-8")
        (web / "yaml-in.json").write_text("openapi: 3.0.0\n", encoding="utf-8")
        for name, needle in (("api.yaml", "YAML is not supported"),
                             ("broken.json", "not valid JSON"),
                             ("swagger.json", "not an OpenAPI 3.x document"),
                             ("yaml-in.json", "looks like YAML"),
                             ("missing.json", "not found")):
            code, output = run(["--root", str(self.root), "import", "openapi",
                                str(web / name), "--against",
                                str(self.root / "server" / "main.go")])
            self.assertEqual(code, 2, (name, output))
            self.assertIn(needle, output, name)
        outside = Path(self.tmp.name) / "outside.json"
        shutil.copy(FIXTURES / "openapi.json", outside)
        code, output = run(["--root", str(self.root), "import", "openapi", str(outside),
                            "--against", str(self.root / "server" / "main.go")])
        self.assertEqual(code, 2, output)
        self.assertIn("outside the repository", output)


class ProtoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="crossweft-proto-")
        self.root = Path(self.tmp.name)
        (self.root / "crossweft.json").write_text("{}\n", encoding="utf-8")
        for name in ("shop.proto", "orders.go", "unrelated.ts"):
            shutil.copy(FIXTURES / name, self.root / name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_parse_strips_comments_and_reads_nesting(self) -> None:
        text = (FIXTURES / "shop.proto").read_text(encoding="utf-8")
        messages, enums = importers.parse_proto(text)
        self.assertEqual([(m["name"], m["fields"]) for m in messages],
                         [("Order", ["id", "items", "total_cents", "status", "labels",
                                     "card_token", "invoice_id"])])
        self.assertEqual([(e["name"], e["values"]) for e in enums],
                         [("Order.Status", ["STATUS_UNSPECIFIED", "PENDING", "PAID", "SHIPPED"]),
                          ("Currency", ["EUR", "USD"])])

    def test_import_against_go(self) -> None:
        code, output = run(["--root", str(self.root), "import", "proto",
                            str(self.root / "shop.proto"), "--against",
                            str(self.root / "orders.go"), "--json"])
        self.assertEqual(code, 0, output)
        report = json.loads(output)
        sets = {s["id"]: s for s in report["ready"]["sets"]}
        self.assertEqual(sorted(sets), ["proto-currency-values", "proto-order-fields"])
        status = next(s for s in report["suggestions"] if s["id"] == "proto-order-status-values")
        self.assertEqual(status["hand"], "matched nothing")
        # the schema side reproduces the message exactly; the commented-out
        # Ghost message and the nested enum's values are not fields
        left = sets["proto-order-fields"]["left"]
        text = (self.root / "shop.proto").read_text(encoding="utf-8")
        self.assertEqual(importers.extract(text, left["within"], left["regex"], []),
                         {"id", "items", "total_cents", "status", "labels", "card_token",
                          "invoice_id"})

    def test_nothing_matched_exits_1(self) -> None:
        code, output = run(["--root", str(self.root), "import", "proto",
                            str(self.root / "shop.proto"), "--against",
                            str(self.root / "unrelated.ts")])
        self.assertEqual(code, 1, output)
        self.assertIn("matched nothing", output)

    def test_broken_proto_exits_2(self) -> None:
        (self.root / "bad.proto").write_text("message A {\n  string id = 1;\n",
                                             encoding="utf-8")
        (self.root / "comment.proto").write_text("/* never closed\nmessage A {}\n",
                                                 encoding="utf-8")
        for name in ("bad.proto", "comment.proto"):
            code, output = run(["--root", str(self.root), "import", "proto",
                                str(self.root / name), "--against",
                                str(self.root / "orders.go")])
            self.assertEqual(code, 2, (name, output))


if __name__ == "__main__":
    unittest.main()
