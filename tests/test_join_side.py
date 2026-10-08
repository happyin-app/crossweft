"""A join point's `side` is a label; when the label is a block id, it must be
a block that holds the point's file.

Run: python -m unittest discover -s tests -v   (stdlib only)
"""

from __future__ import annotations

import unittest

from test_engine_review import Fixture, blocks, link

FILES = {"web/api.ts": 'export const V = "v1";\n',
         "server/main.go": 'package main\nconst V = "v1"\n',
         "docs/notes.md": 'V = "v1"\n'}


def join(web_side: str = "web", api_side: str = "api",
         api_path: str = "server/main.go") -> dict:
    return {"id": "v", "name": "v", "link": "web-api", "points": [
        {"path": "web/api.ts", "side": web_side, "regex": "V = \"([^\"]+)\""},
        {"path": api_path, "side": api_side, "regex": "V = \"([^\"]+)\""}]}


class JoinSide(Fixture):
    def run_check(self, the_join: dict, extra_blocks: list | None = None) -> tuple[int, str]:
        self.make(FILES, {"blocks": blocks() + (extra_blocks or []), "links": [link()],
                          "joins": [the_join]})
        return self.check()

    def test_the_block_that_holds_the_file_passes(self) -> None:
        code, output = self.run_check(join())
        self.assertEqual(code, 0, output)

    def test_a_free_label_that_is_no_block_id_passes(self) -> None:
        code, output = self.run_check(join("client (TypeScript)", "backend"))
        self.assertEqual(code, 0, output)

    def test_a_block_id_that_does_not_hold_the_file_is_a_schema_error(self) -> None:
        # "api" names the other end: the label contradicts where the file is.
        code, output = self.run_check(join(web_side="api"))
        self.assertEqual(code, 2, output)
        self.assertIn("join v point 0.side: 'api' is a block id, but web/api.ts is code of web",
                      output)
        self.assertNotIn("RESULT: PASS", output)

    def test_a_third_block_that_is_no_end_of_the_link_may_be_named(self) -> None:
        # A join may read more files than the link's two ends (release version in
        # three manifests); the side then names the block that holds that file.
        docs = {"id": "docs", "name": "Docs", "kind": "artifact", "lane": "app",
                "status": "current", "summary": "s", "code": ["docs/"]}
        the_join = join()
        the_join["points"].append({"path": "docs/notes.md", "side": "docs",
                                   "regex": "V = \"([^\"]+)\""})
        code, output = self.run_check(the_join, [docs])
        self.assertEqual(code, 0, output)

    def test_a_codeless_part_of_the_owner_may_be_named(self) -> None:
        auth = {"id": "auth", "name": "Auth", "kind": "component", "lane": "app",
                "status": "current", "summary": "s", "parent": "api"}
        code, output = self.run_check(join(api_side="auth"), [auth])
        self.assertEqual(code, 0, output)

    def test_an_empty_side_is_a_schema_error(self) -> None:
        code, output = self.run_check(join(api_side="  "))
        self.assertEqual(code, 2, output)
        self.assertIn("join v point 1.side: must be a non-empty string", output)


if __name__ == "__main__":
    unittest.main()
