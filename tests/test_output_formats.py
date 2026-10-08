"""Machine output formats: SARIF results carry stable fingerprints, and a
`check` in a machine format keeps stdout one parseable document even when
crossweft.json cannot be read or the flags are refused.

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
sys.path.insert(0, str(REPO))

from crossweft import cli, engine  # noqa: E402

LEVELS = {"none", "note", "warning", "error"}


def run(argv: list) -> tuple:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def sarif_shape_errors(doc: dict) -> list:
    """The SARIF 2.1.0 schema constraints the crossweft results rely on (stdlib)."""
    errors = []
    if doc.get("version") != "2.1.0" or not isinstance(doc.get("runs"), list):
        return ["version must be 2.1.0 and runs a list"]
    for run_ in doc["runs"]:
        driver = run_.get("tool", {}).get("driver", {})
        if not isinstance(driver.get("name"), str):
            errors.append("tool.driver.name missing")
        rule_ids = {r.get("id") for r in driver.get("rules", [])}
        for i, row in enumerate(run_.get("results", [])):
            where = f"results[{i}]"
            if not isinstance(row.get("message", {}).get("text"), str):
                errors.append(f"{where}.message.text missing")
            if row.get("ruleId") not in rule_ids:
                errors.append(f"{where}.ruleId not among driver.rules")
            if row.get("level") not in LEVELS:
                errors.append(f"{where}.level invalid")
            fingerprints = row.get("partialFingerprints")
            if not (isinstance(fingerprints, dict) and fingerprints and all(
                    isinstance(k, str) and isinstance(v, str) and v
                    for k, v in fingerprints.items())):
                errors.append(f"{where}.partialFingerprints missing or not string->string")
            for loc in row.get("locations", []) or [None]:
                physical = (loc or {}).get("physicalLocation", {})
                if not isinstance(physical.get("artifactLocation", {}).get("uri"), str):
                    errors.append(f"{where} location has no uri")
                start = physical.get("region", {}).get("startLine")
                if not (isinstance(start, int) and start >= 1):
                    errors.append(f"{where} region.startLine must be an integer >= 1")
            for sup in row.get("suppressions", []):
                if sup.get("kind") not in ("inSource", "external"):
                    errors.append(f"{where}.suppressions kind invalid")
    return errors


class SarifFingerprintTest(unittest.TestCase):
    def payload(self, **fields) -> dict:
        return engine.sarif_payload(engine.CheckResult(**fields))

    def test_every_result_kind_has_a_fingerprint_and_a_valid_shape(self) -> None:
        doc = self.payload(load_error="crossweft.json is unreadable: boom",
                           schema_errors=["block x: name missing"],
                           stale=["F-1:join:a:b"], render_stale=["docs/map.md"])
        self.assertEqual(sarif_shape_errors(doc), [])
        rules = {r["ruleId"] for r in doc["runs"][0]["results"]}
        self.assertEqual(rules, {"crossweft/model", "crossweft/stale-finding",
                                 "crossweft/render"})

    def test_fingerprints_are_stable_and_distinct(self) -> None:
        def keys(**fields) -> list:
            return [r["partialFingerprints"]["crossweftKey/v1"]
                    for r in self.payload(**fields)["runs"][0]["results"]]
        # same problem, different whitespace or path separators: same alert
        self.assertEqual(keys(load_error="cannot read a\\b.json  now"),
                         keys(load_error="cannot read a/b.json now"))
        # the render alert does not move when the stale file list does
        self.assertEqual(keys(render_stale=["docs/a.md"]),
                         keys(render_stale=["docs/a.md", "docs/b.md"]))
        # two different schema errors are two alerts
        two = keys(schema_errors=["block x: name missing", "block y: name missing"])
        self.assertEqual(len(set(two)), 2)
        self.assertNotEqual(keys(load_error="e")[0], keys(schema_errors=["e"])[0])

    def test_shape_check_has_a_negative_control(self) -> None:
        doc = self.payload(render_stale=["docs/a.md"])
        row = doc["runs"][0]["results"][0]
        row.pop("partialFingerprints")
        row["locations"][0]["physicalLocation"]["region"]["startLine"] = 0
        self.assertEqual(len(sarif_shape_errors(doc)), 2)


class MachineFormatDocumentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="crossweft-formats-")
        self.root = Path(self.tmp.name) / "shop"
        shutil.copytree(DEMO, self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def check(self, *extra: str) -> tuple:
        return run(["--root", str(self.root), "check", *extra])

    def test_demo_sarif_has_a_valid_shape(self) -> None:
        path = self.root / "web/src/api.ts"
        path.write_text(path.read_text(encoding="utf-8").replace(
            '"2026-09-01"', '"2026-10-01"'), encoding="utf-8")
        code, output, _ = self.check("--format", "sarif")
        self.assertEqual(code, 1)
        doc = json.loads(output)
        self.assertTrue(doc["runs"][0]["results"])
        self.assertEqual(sarif_shape_errors(doc), [])

    def test_unreadable_config_is_one_json_document_with_the_normal_shape(self) -> None:
        code, output, _ = self.check("--format", "json")
        self.assertEqual(code, 0, output)
        normal_keys = set(json.loads(output))
        (self.root / "crossweft.json").write_text("{not json", encoding="utf-8")
        for flags in (("--format", "json"), ("--json",), ("--json", "--format", "sarif")):
            code, output, _ = self.check(*flags)
            self.assertEqual(code, 2, flags)
            doc = json.loads(output)
            self.assertEqual(set(doc), normal_keys, flags)
            self.assertIs(doc["ok"], False)
            self.assertIn("crossweft.json", doc["error"])
        code, output, _ = self.check("--format", "sarif")
        self.assertEqual(code, 2)
        self.assertEqual(sarif_shape_errors(json.loads(output)), [])
        code, output, _ = self.check()
        self.assertEqual(code, 2)
        self.assertTrue(output.startswith("[ERR] "), "text format stays plain text")

    def test_changed_with_sarif_is_refused_with_an_empty_stdout(self) -> None:
        code, output, err = self.check("--changed", "HEAD", "--format", "sarif")
        self.assertEqual(code, 2)
        self.assertEqual(output, "", "no partial or plain-text document on stdout")
        self.assertIn("--changed cannot be combined with --format sarif", err)


if __name__ == "__main__":
    unittest.main()
