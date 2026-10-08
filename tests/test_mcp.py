"""`crossweft mcp`: the stdio MCP server, driven as a real subprocess the way a
client (Claude Code, Cursor) drives it, on a copy of the polyglot demo.

Run: python -m unittest discover -s tests -v   (stdlib only)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "examples" / "polyglot-shop"


def tree_digest(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class McpServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="crossweft-mcp-")
        self.root = Path(self.tmp.name) / "shop"
        shutil.copytree(DEMO, self.root)
        self.before = tree_digest(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def session(self, messages: list, root: Path | None = None) -> list:
        """Run one server process over `messages` (dicts or raw lines) and
        return every response line, parsed."""
        env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8")
        lines = [m if isinstance(m, str) else json.dumps(m) for m in messages]
        proc = subprocess.run(
            [sys.executable, "-m", "crossweft", "--root", str(root or self.root), "mcp"],
            input=("\n".join(lines) + "\n").encode("utf-8"), capture_output=True, env=env,
            cwd=self.tmp.name, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8")
        self.assertTrue(out.endswith("\n"), out)
        return [json.loads(line) for line in out.splitlines()]

    def calls(self, *calls: tuple) -> list:
        """initialize -> notifications/initialized -> the tools/call requests."""
        messages = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ]
        for index, (name, arguments) in enumerate(calls, 1):
            messages.append({"jsonrpc": "2.0", "id": index, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})
        responses = self.session(messages)
        self.assertEqual([r["id"] for r in responses], list(range(len(calls) + 1)))
        return responses[1:]

    def result(self, response: dict) -> dict:
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][1]["text"]), result["structuredContent"])
        return result

    def test_handshake_and_tool_list(self) -> None:
        responses = self.session([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ])
        self.assertEqual(len(responses), 3)    # the notification gets no answer
        init = responses[0]["result"]
        self.assertEqual(init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", init["capabilities"])
        self.assertEqual(init["serverInfo"]["name"], "crossweft")
        tools = {t["name"]: t for t in responses[1]["result"]["tools"]}
        self.assertEqual(set(tools), {"check", "impact", "show", "discover", "other_side"})
        for tool in tools.values():
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertEqual(responses[2], {"jsonrpc": "2.0", "id": 3, "result": {}})

    def test_every_tool_on_the_demo(self) -> None:
        check, scoped, impact, show, other, disc = map(self.result, self.calls(
            ("check", {}),
            ("check", {"paths": ["web/src/api.ts"]}),
            ("impact", {"paths": ["web/src/api.ts"]}),
            ("show", {"id": "join:api-version:ff55dadb"}),
            ("other_side", {"path": "web/src/api.ts"}),
            ("discover", {"limit": 3}),
        ))
        self.assertFalse(check["isError"])
        self.assertEqual(check["structuredContent"]["status"], "ok")
        self.assertEqual(check["structuredContent"]["exit_code"], 0)
        self.assertGreater(check["structuredContent"]["scanned"]["files"], 0)
        self.assertEqual(scoped["structuredContent"]["filter"]["paths"], ["web/src/api.ts"])
        links = {row["link"]: row for row in impact["structuredContent"]["links"]}
        self.assertIn("server/main.go", links["web-orders"]["recheck"])
        self.assertIn("join:api-version", links["web-orders"]["guards"])
        match = show["structuredContent"]["matches"][0]
        self.assertEqual((match["kind"], match["id"]), ("joins", "api-version"))
        self.assertIn("server/main.go", other["structuredContent"]["other_side"])
        self.assertNotIn("web/src/api.ts", other["structuredContent"]["other_side"])
        self.assertIn("server/main.go", other["content"][0]["text"])
        self.assertFalse(disc["isError"])
        self.assertIn("candidates", disc["structuredContent"])
        self.assertEqual(tree_digest(self.root), self.before, "a read-only tool wrote to disk")

    def test_drift_is_reported_not_passed(self) -> None:
        path = self.root / "web" / "src" / "api.ts"
        path.write_bytes(path.read_bytes().replace(b'"2026-09-01"', b'"2026-10-01"'))
        full, elsewhere = map(self.result, self.calls(
            ("check", {}), ("check", {"paths": ["worker"]})))
        data = full["structuredContent"]
        self.assertEqual((data["status"], data["exit_code"]), ("problems", 1))
        self.assertTrue(any("api-version" in p["key"] for p in data["new"]), data["new"])
        self.assertIn("PROBLEMS", full["content"][0]["text"])
        # a filter narrows the list, never the verdict
        scoped = elsewhere["structuredContent"]
        self.assertEqual((scoped["status"], scoped["new"]), ("problems", []))
        self.assertEqual(scoped["filter"]["unlisted_new"], data["counts"]["new"])

    def test_bad_arguments_and_requests_are_errors(self) -> None:
        responses = self.calls(
            ("impact", {}),                         # missing required
            ("show", {"id": 3}),                    # wrong type
            ("discover", {"limit": 0}),             # out of range
            ("check", {"path": "x"}),               # unknown argument
            ("nope", {}),                           # unknown tool
            ("show", {"id": "link:no-such-link"}),  # ran, found nothing
            ("other_side", {"path": "../outside"}),  # leaves the repository
        )
        for response in responses[:5]:
            self.assertEqual(response["error"]["code"], -32602, response)
        for response in responses[5:]:
            self.assertTrue(response["result"]["isError"], response)
            self.assertIn("[ERR]", response["result"]["content"][0]["text"])
        raw = self.session([
            "{not json",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},   # before initialize
            [{"jsonrpc": "2.0", "id": 2, "method": "ping"}],      # batch
        ])
        self.assertEqual(raw[0]["error"]["code"], -32700)
        self.assertEqual(raw[1]["error"]["code"], -32600)
        self.assertEqual(raw[2]["error"]["code"], -32600)

    def test_missing_config_is_a_tool_error(self) -> None:
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.root = empty
        response = self.calls(("check", {}))[0]
        self.assertTrue(response["result"]["isError"])
        self.assertIn("crossweft.json", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
