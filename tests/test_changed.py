"""`crossweft check --changed REV`: only the guards that read a changed file run,
and every case where the change set cannot be trusted runs the full check.

Run: python -m unittest discover -s tests -v   (stdlib only)
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from crossweft import cli, engine  # noqa: E402

META = {"schema": engine.SCHEMA_ID, "title": "t", "lanes": [{"id": "app", "name": "App"}]}

BLOCKS = [
    {"id": "web", "name": "Web", "kind": "site", "lane": "app", "status": "current",
     "summary": "s", "code": ["web/"]},
    {"id": "api", "name": "API", "kind": "service", "lane": "app", "status": "current",
     "summary": "s", "code": ["server/"]},
    {"id": "worker", "name": "Worker", "kind": "service", "lane": "app", "status": "current",
     "summary": "s", "code": ["worker/"]},
]


def _link(ident: str, src: str, dst: str, src_path: str, dst_path: str) -> dict:
    return {"id": ident, "from": src, "to": dst, "transport": "https", "status": "current",
            "summary": "s", "contract": {"name": ident, "enforcement": "duplicated"},
            "from_anchors": [{"path": src_path, "find": "VERSION"}],
            "to_anchors": [{"path": dst_path, "find": "Version"}]}


def _join(ident: str, link: str, left: str, right: str) -> dict:
    return {"id": ident, "name": ident, "link": link, "points": [
        {"path": left, "side": "a", "regex": "VERSION = \"([^\"]+)\""},
        {"path": right, "side": "b", "regex": "Version = \"([^\"]+)\""}]}


MODEL = {
    "blocks": BLOCKS,
    "links": [_link("web-api", "web", "api", "web/api.ts", "server/main.go"),
              _link("worker-api", "worker", "api", "worker/job.py", "server/jobs.go")],
    "joins": [_join("web-version", "web-api", "web/api.ts", "server/main.go"),
              _join("worker-version", "worker-api", "worker/job.py", "server/jobs.go")],
}

FILES = {
    "web/api.ts": 'export const VERSION = "1";\n',
    "server/main.go": 'package main\nconst Version = "1"\n',
    "worker/job.py": 'VERSION = "7"\n',
    "server/jobs.go": 'package main\nconst Version = "7"\n',
}


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


class GitRepo(unittest.TestCase):
    """A throwaway git repository holding a model and its files."""

    def make_repo(self, model: dict, files: dict[str, str]) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="crossweft-changed-")
        self.root = Path(self._tmp.name) / "repo"
        (self.root / "seams" / "model").mkdir(parents=True)
        (self.root / "crossweft.json").write_text("{}", encoding="utf-8")
        self.save_model(model)
        for rel, text in files.items():
            self.write(rel, text)
        git(self.root, "init", "-q")
        self.commit()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    meta = META

    def save_model(self, model: dict) -> None:
        payload = {"meta": self.meta, **model}
        self.write("seams/model/00.json", json.dumps(payload, indent=1))

    def commit(self) -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "c")

    def check(self, *extra: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--root", str(self.root), "check", *extra])
        return code, buffer.getvalue()


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class Changed(GitRepo):
    def setUp(self) -> None:
        self.make_repo(MODEL, FILES)

    def commit_hidden_drift(self) -> None:
        """A drift in worker/job.py that `--changed HEAD` cannot see: committed."""
        self.write("worker/job.py", 'VERSION = "8"\n')
        self.commit()

    # -- only the touched guards run ----------------------------------------
    def test_drift_in_a_changed_file_is_caught(self) -> None:
        self.write("web/api.ts", 'export const VERSION = "2";\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("join:web-version disagrees", output)
        # web/ code path + web/api.ts anchor + web-version join (3 of 9 guards)
        self.assertIn("(changed-only: 3 of 9 guards)", output)

    def test_an_untracked_file_is_in_the_change_set(self) -> None:
        self.write("web/api.ts", "")
        git(self.root, "rm", "-q", "--cached", "web/api.ts")
        self.commit()
        self.write("web/api.ts", 'export const VERSION = "2";\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("join:web-version disagrees", output)

    def test_a_git_ignored_guarded_file_always_runs(self) -> None:
        # git cannot say whether an ignored file changed: its guard runs every time
        self.write(".gitignore", "web/\n")
        git(self.root, "rm", "-q", "-r", "--cached", "web")
        self.commit()
        self.write("web/api.ts", 'export const VERSION = "2";\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("join:web-version disagrees", output)

    def test_an_unchanged_drift_is_skipped_and_says_so(self) -> None:
        # the partial run is honest about being partial: the full check still fails
        self.commit_hidden_drift()
        self.write("web/api.ts", 'export const VERSION = "1"; // touched\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 0, output)
        self.assertIn("changed-only: 3 of 9 guards", output)
        self.assertIn("Not a full check", output)
        self.assertEqual(self.check()[0], 1)

    # -- negative controls: the full check runs, so the hidden drift is caught --
    def test_a_changed_model_file_runs_the_full_check(self) -> None:
        self.commit_hidden_drift()
        self.save_model({**MODEL, "$comment": "edited"})
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("join:worker-version disagrees", output)
        self.assertIn("ran the FULL check instead -- the model changed", output)
        self.assertNotIn("changed-only:", output)

    def test_a_changed_config_or_lock_runs_the_full_check(self) -> None:
        self.commit_hidden_drift()
        for rel, text in (("crossweft.json", '{"language": "en"}'),
                          ("seams/pairs.lock.json", '{"schema": "crossweft.lock.v1", '
                                                    '"pairs": {}}')):
            with self.subTest(rel=rel):
                self.write(rel, text)
                code, output = self.check("--changed", "HEAD")
                self.assertEqual(code, 1, output)
                self.assertIn("join:worker-version disagrees", output)
                self.assertIn("ran the FULL check instead", output)
                git(self.root, "checkout", "-q", "--", ".")
                git(self.root, "clean", "-q", "-fd")

    def test_an_unknown_revision_runs_the_full_check(self) -> None:
        self.commit_hidden_drift()
        for rev in ("no-such-ref", "--output=x"):
            with self.subTest(rev=rev):
                code, output = self.check(f"--changed={rev}")
                self.assertEqual(code, 1, output)
                self.assertIn("join:worker-version disagrees", output)
                self.assertIn("(full check; --changed fell back)", output)
        self.assertFalse((self.root / "x").exists())

    # -- findings ------------------------------------------------------------
    def test_a_finding_of_a_skipped_guard_is_not_stale(self) -> None:
        finding = {"id": "SM-001", "title": "known", "severity": "minor", "status": "open",
                   "kind": "contract-mismatch", "summary": "s", "owner": "me",
                   "next_step": "fix", "detected_by": ["join:worker-version:00000000"]}
        self.save_model({**MODEL, "findings": [finding]})
        self.commit()
        code, output = self.check()
        self.assertEqual(code, 1, output)              # full: the detector no longer fires
        self.assertIn("SM-001:join:worker-version:00000000", output)
        self.write("web/api.ts", 'export const VERSION = "1"; // touched\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 0, output)              # skipped: not re-checked, not stale
        self.assertIn("not re-checked", output)
        self.assertIn("SM-001:join:worker-version:00000000", output)
        self.write("worker/job.py", 'VERSION = "7"  # touched\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)              # its guard ran: stale again
        self.assertIn("stale findings: 1", output)

    # -- formats -------------------------------------------------------------
    def test_json_states_the_scope_and_sarif_is_refused(self) -> None:
        self.write("web/api.ts", 'export const VERSION = "1"; // touched\n')
        code, output = self.check("--changed", "HEAD", "--json")
        self.assertEqual(code, 0, output)
        scope = json.loads(output)["changed_only"]
        self.assertEqual((scope["guards_run"], scope["guards_total"], scope["fallback"]),
                         (3, 9, None))
        self.assertIsNone(json.loads(self.check("--json")[1])["changed_only"])
        code, output = self.check("--changed", "HEAD", "--format", "sarif")
        self.assertEqual(code, 2, output)


ROUTE_BLOCKS = [
    {"id": "web", "name": "Web", "kind": "site", "lane": "app", "status": "current",
     "summary": "s", "code": ["web/"]},
    {"id": "api", "name": "API", "kind": "service", "lane": "app", "status": "current",
     "summary": "s", "code": ["server/"], "route_server": True},
]

ROUTE_MODEL = {
    "blocks": ROUTE_BLOCKS,
    "links": [{"id": "web-api", "from": "web", "to": "api", "transport": "https",
               "status": "current", "summary": "s",
               "contract": {"name": "c", "enforcement": "shared-code",
                            "defined_in": [{"path": "web/a.ts", "find": "fetch"}]},
               "identifiers": {"route": ["GET /v1/a", "GET /v1/b", "GET /v1/shared"]},
               "from_anchors": [{"path": "web/a.ts", "find": "fetch"}],
               "to_anchors": [{"path": "server/a.go", "find": "chi.NewRouter"}]}],
}

ROUTE_SCAN = {"routers": [{"path": "server/a.go", "scanner": "go-chi"},
                          {"path": "server/b.go", "scanner": "go-chi"}],
              "router_search": ["server/**/*.go"], "client_globs": ["web/**/*.ts"],
              "client_literal_regexes": ['"(/v1/[^"]*)"']}


def _router(*paths: str) -> str:
    body = "".join(f'\tr.Get("{p}", h)\n' for p in paths)
    return f"package main\n\nfunc main() {{\n\tr := chi.NewRouter()\n{body}}}\n"


ROUTE_FILES = {
    "server/a.go": _router("/v1/a", "/v1/shared"),
    "server/b.go": _router("/v1/b", "/v1/shared"),
    "web/a.ts": 'fetch("/v1/a");\n',
    "web/b.ts": 'fetch("/v1/b");\nfetch("/v1/shared");\n',
}


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class ChangedRoutes(GitRepo):
    """The route scan is two guards: every router when any router changed, and
    only the changed client files. A narrowed scan must never hide a problem."""

    meta = {**META, "route_scan": ROUTE_SCAN}

    def setUp(self) -> None:
        self.make_repo(ROUTE_MODEL, ROUTE_FILES)
        code, output = self.check()
        self.assertEqual(code, 0, output)

    def test_a_route_dropped_by_a_changed_router_that_an_unchanged_client_calls(self) -> None:
        # web/b.ts still calls /v1/b and is not in the change set; the link still
        # declares it: route:unserved, found because every router is rescanned
        self.write("server/b.go", _router("/v1/shared"))
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("route:unserved:GET /v1/b", output)
        self.assertIn("changed-only:", output)

    def test_a_route_still_served_by_an_unchanged_router_is_not_unserved(self) -> None:
        # negative control for scanning the changed router alone: /v1/shared
        # leaves b.go but server/a.go (unchanged) still registers it
        self.write("server/b.go", _router("/v1/b"))
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 0, output)
        self.assertNotIn("route:unserved", output)

    def test_a_new_route_in_a_changed_router_is_unmapped(self) -> None:
        self.write("server/a.go", _router("/v1/a", "/v1/shared", "/v1/ghost"))
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("route:unmapped:GET /v1/ghost", output)

    def test_a_new_unscanned_router_file_is_caught(self) -> None:
        self.write("server/c.go", _router("/v1/a"))
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("route:unscanned-router:server/c.go", output)

    def test_a_changed_client_is_scanned_and_only_it(self) -> None:
        # committed drift in an unchanged client and an unchanged router: skipped
        self.write("web/old.ts", 'fetch("/v1/old");\n')
        self.write("server/a.go", _router("/v1/a", "/v1/shared", "/v1/ghost"))
        self.commit()
        self.write("web/a.ts", 'fetch("/v1/a");\nfetch("/v1/new");\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("route:client-unexplained:/v1/new", output)
        self.assertNotIn("/v1/old", output)
        self.assertNotIn("/v1/ghost", output)
        code, output = self.check()
        self.assertEqual(code, 1, output)
        for key in ("/v1/new", "/v1/old", "route:unmapped:GET /v1/ghost"):
            self.assertIn(key, output)

    def test_a_client_finding_in_an_unchanged_file_is_not_stale(self) -> None:
        finding = {"id": "SM-001", "title": "known", "severity": "minor", "status": "open",
                   "kind": "contract-mismatch", "summary": "s", "owner": "me", "next_step": "fix",
                   "detected_by": ["route:client-unexplained:/v1/old"]}
        self.write("web/old.ts", 'fetch("/v1/old");\n')
        self.save_model({**ROUTE_MODEL, "findings": [finding]})
        self.commit()
        self.assertEqual(self.check()[0], 0)
        # the literal now lives only in the unchanged file: not re-checked, not stale
        self.write("web/a.ts", 'fetch("/v1/a"); // touched\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 0, output)
        self.assertIn("not re-checked", output)
        # every client file in scope: the literal is gone, so the finding is stale
        self.write("web/old.ts", "// fixed\n")
        self.write("web/b.ts", ROUTE_FILES["web/b.ts"] + "// touched\n")
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 1, output)
        self.assertIn("stale findings: 1", output)


class Within(unittest.TestCase):
    """Checker.within: inside() without a resolve() per file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="crossweft-within-")
        base = Path(self._tmp.name)
        self.root, self.outside = base / "repo", base / "outside"
        (self.root / "web").mkdir(parents=True)
        self.outside.mkdir()
        (self.root / "web" / "a.ts").write_text("x", encoding="utf-8")
        (self.outside / "b.ts").write_text("x", encoding="utf-8")
        self.checker = engine.Checker(type("M", (), {"root": self.root})())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assertAgrees(self, rel: str, expected: bool) -> None:
        path = self.root / rel
        self.assertEqual(engine.inside(self.root, path), expected, rel)
        self.assertEqual(self.checker.within(path), expected, rel)

    def test_a_plain_path_is_inside(self) -> None:
        self.assertAgrees("web/a.ts", True)
        self.assertAgrees("web/../../outside/b.ts", False)

    def test_a_symlink_out_of_the_tree_is_outside(self) -> None:
        try:
            (self.root / "web" / "Link").symlink_to(self.outside, target_is_directory=True)
            (self.root / "web" / "file.ts").symlink_to(self.outside / "b.ts")
        except (OSError, NotImplementedError):
            self.skipTest("cannot create a symlink here")
        self.assertAgrees("web/Link/b.ts", False)
        # on a case-insensitive file system `link` opens the symlink `Link`; on a
        # case-sensitive one it is a plain (missing) directory inside the tree
        case_insensitive = (self.root / "WEB").exists()
        self.assertAgrees("web/link/b.ts", not case_insensitive)
        self.assertAgrees("web/file.ts", False)

    @unittest.skipUnless(sys.platform == "win32", "junctions are a Windows feature")
    def test_a_junction_out_of_the_tree_is_outside(self) -> None:
        junction = self.root / "web" / "j"
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.outside)],
                              capture_output=True)
        if proc.returncode != 0:
            self.skipTest("cannot create a junction here")
        self.assertAgrees("web/j/b.ts", False)


class ScopeHit(unittest.TestCase):
    def test_globs_reach_only_where_they_can_match(self) -> None:
        hit = engine._scope_hit
        self.assertTrue(hit("server/a.go", "server/**/*.go"))
        self.assertTrue(hit("server/x/y/a.go", "server/**/*.go"))
        self.assertTrue(hit("server/gen", "server/*/*.go"))         # a directory it reaches
        self.assertTrue(hit("Web/A.ts", "web/*.ts"))                # case-insensitive
        self.assertTrue(hit("web/[id]/page.ts", "web/[id]/page.ts"))  # literal file name
        self.assertTrue(hit("web", "web/api.ts"))                   # a changed directory
        self.assertFalse(hit("docs/a.md", "server/**/*.go"))
        self.assertFalse(hit("server/x/a.go", "server/*.go"))       # deeper than it reaches
        self.assertFalse(hit("server/a.py", "server/*.go"))
        self.assertFalse(hit("web/api.tsx", "web/api.ts"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class Incremental(GitRepo):
    """check --incremental: --changed against the last commit at which the check
    passed in this work tree, with the paths that were uncommitted then."""

    def setUp(self) -> None:
        self.make_repo(MODEL, FILES)

    def record(self) -> dict:
        path = engine._pass_record_path(engine.load_config(self.root))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_first_run_is_full_then_only_the_changes_run(self) -> None:
        code, output = self.check("--incremental")
        self.assertEqual(code, 0, output)
        self.assertIn("incremental: full check (no passing check recorded yet)", output)
        self.write("web/api.ts", 'export const VERSION = "1"; // touched\n')
        code, output = self.check("--incremental")
        self.assertEqual(code, 0, output)
        self.assertIn("incremental: changes since the last verified commit", output)
        self.assertIn("(changed-only:", output)

    def test_drift_after_a_pass_is_caught(self) -> None:
        self.assertEqual(self.check("--incremental")[0], 0)
        self.write("server/jobs.go", 'package main\nconst Version = "9"\n')
        code, output = self.check("--incremental")
        self.assertEqual(code, 1, output)
        self.assertIn("join:worker-version disagrees", output)

    def test_reverting_an_uncommitted_file_reruns_its_guards(self) -> None:
        # committed drift that only the uncommitted fix hides
        self.write("worker/job.py", 'VERSION = "8"\n')
        self.commit()
        self.write("worker/job.py", 'VERSION = "7"\n')      # uncommitted fix: passes
        self.assertEqual(self.check("--incremental")[0], 0)
        self.assertEqual(self.record()["dirty"], ["worker/job.py"])
        git(self.root, "checkout", "--", "worker/job.py")   # back to the committed drift
        code, output = self.check("--incremental")
        self.assertEqual(code, 1, output)
        self.assertIn("join:worker-version disagrees", output)

    def test_a_failing_run_does_not_move_the_base(self) -> None:
        self.assertEqual(self.check("--incremental")[0], 0)
        base = self.record()["commit"]
        self.write("server/main.go", 'package main\nconst Version = "2"\n')
        self.commit()
        self.assertEqual(self.check("--incremental")[0], 1)
        self.assertEqual(self.record()["commit"], base)

    def test_another_crossweft_version_runs_the_full_check(self) -> None:
        self.assertEqual(self.check("--incremental")[0], 0)
        path = engine._pass_record_path(engine.load_config(self.root))
        record = self.record()
        record["version"] = "0.0.1"
        path.write_text(json.dumps(record), encoding="utf-8")
        code, output = self.check("--incremental")
        self.assertEqual(code, 0, output)
        self.assertIn("incremental: full check (the last pass was checked by crossweft 0.0.1)",
                      output)

    def test_incremental_and_changed_are_exclusive_and_sarif_is_refused(self) -> None:
        self.assertEqual(self.check("--incremental", "--changed", "HEAD")[0], 2)
        self.assertEqual(self.check("--incremental", "--format", "sarif")[0], 2)


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class JointSources(GitRepo):
    """joints.sources narrows the 'a joint plugin changed' fallback to the plugin
    files and their helpers, and refuses a list that misses a helper."""

    meta = {**META, "joint_kinds": ["demo"]}
    PLUGIN = ('import helper\nSECTION = "demo"\n'
              'def validate(entries):\n    return []\n'
              'def check(root, entries, model):\n'
              '    return {"problems": [], "items": len(entries), "files": [], "info": helper.X}\n'
              'def self_test():\n    return 0\n')

    def setUp(self) -> None:
        self.make_repo({**MODEL, "demo": [{"id": "one"}]}, {
            **FILES, "tools/joints_demo.py": self.PLUGIN, "tools/helper.py": 'X = "ok"\n',
            "tools/unrelated.py": "print(1)\n"})

    def configure(self, sources: list[str]) -> None:
        self.write("crossweft.json", json.dumps({"joints": {"dir": "tools", "sources": sources}}))
        self.commit()

    def test_an_unrelated_file_in_the_plugin_dir_does_not_run_the_full_check(self) -> None:
        self.configure(["tools/joints_*.py", "tools/helper.py"])
        self.write("tools/unrelated.py", "print(2)\n")
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 0, output)
        self.assertIn("(changed-only:", output)

    def test_a_changed_helper_runs_the_full_check(self) -> None:
        self.configure(["tools/joints_*.py", "tools/helper.py"])
        self.write("tools/helper.py", 'X = "changed"\n')
        code, output = self.check("--changed", "HEAD")
        self.assertEqual(code, 0, output)
        self.assertIn("(full check; --changed fell back)", output)

    def test_a_helper_missing_from_sources_is_an_error(self) -> None:
        self.configure(["tools/joints_*.py"])
        code, output = self.check()
        self.assertEqual(code, 2, output)
        self.assertIn("tools/helper.py is a plugin file or a helper it imports", output)


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class IncrementalReviewHoles(GitRepo):
    """Cases from the independent review: each produced a PASS a full check fails."""

    def setUp(self) -> None:
        self.make_repo(MODEL, FILES)

    def test_a_commit_landing_during_the_check_is_not_recorded_as_verified(self) -> None:
        from unittest import mock
        real = engine.evaluate

        def commit_drift_meanwhile(*args, **kwargs):
            result = real(*args, **kwargs)                       # checked the old bytes
            self.write("worker/job.py", 'VERSION = "8"\n')       # drift committed meanwhile
            self.commit()
            return result

        with mock.patch.object(engine, "evaluate", side_effect=commit_drift_meanwhile):
            self.assertEqual(self.check("--incremental")[0], 0)
        code, output = self.check("--incremental")
        self.assertEqual(code, 1, output)
        self.assertIn("join:worker-version disagrees", output)

    def test_an_edited_crossweft_of_the_same_version_runs_the_full_check(self) -> None:
        self.assertEqual(self.check("--incremental")[0], 0)
        path = engine._pass_record_path(engine.load_config(self.root))
        record = json.loads(path.read_text(encoding="utf-8"))
        record["engine"] = "0" * 64
        path.write_text(json.dumps(record), encoding="utf-8")
        code, output = self.check("--incremental")
        self.assertEqual(code, 0, output)
        self.assertIn("a different build of crossweft", output)


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class JointSourcesReviewHoles(GitRepo):
    meta = {**META, "joint_kinds": ["demo"]}

    def plugin(self, body: str) -> str:
        return (f'{body}\nSECTION = "demo"\n'
                'def validate(entries):\n    return []\n'
                'def self_test():\n    return 0\n')

    def test_a_glob_that_change_detection_cannot_reach_is_refused(self) -> None:
        # "tools/*.py" does not reach tools/sub/h.py for --changed, so it may not cover it
        self.make_repo({**MODEL, "demo": [{"id": "one"}]}, {
            **FILES,
            "tools/joints_demo.py": self.plugin(
                "import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sub'))\n"
                "import h\n"
                "def check(root, entries, model):\n"
                "    return {'problems': [], 'items': len(entries), 'files': [], 'info': h.X}"),
            "tools/sub/h.py": 'X = "ok"\n'})
        self.write("crossweft.json", json.dumps(
            {"joints": {"dir": "tools", "sources": ["tools/joints_*.py", "tools/*.py"]}}))
        self.commit()
        code, output = self.check()
        self.assertEqual(code, 2, output)
        self.assertIn("tools/sub/h.py is a plugin file or a helper it imports", output)

    def test_an_unlisted_data_file_never_gets_an_incremental_pass(self) -> None:
        # the base an incremental check builds on is a passing full check, and that
        # fails on the unlisted read -- so a change to the file cannot hide behind it
        reader = self.plugin(
            "from pathlib import Path\n"
            "def check(root, entries, model):\n"
            "    x = (Path(root) / 'tools' / 'data.json').read_text()\n"
            "    return {'problems': [], 'items': len(entries), 'files': [], 'info': x}")
        self.make_repo({**MODEL, "demo": [{"id": "one"}]}, {
            **FILES, "tools/joints_demo.py": reader, "tools/data.json": "{}\n"})
        self.write("crossweft.json", json.dumps({"joints": {"dir": "tools",
                                                            "sources": ["tools/joints_*.py"]}}))
        self.commit()
        self.assertEqual(self.check("--incremental")[0], 1)
        path = engine._pass_record_path(engine.load_config(self.root))
        self.assertFalse(path.exists(), "a failing run must not record a base")
        self.write("tools/data.json", '{"changed": 1}\n')
        code, output = self.check("--incremental")
        self.assertEqual(code, 1, output)
        self.assertIn("joints:unlisted-read:demo:tools/data.json", output)

    def test_a_data_file_the_plugin_reads_must_be_listed(self) -> None:
        reader = self.plugin(
            "from pathlib import Path\n"
            "def check(root, entries, model):\n"
            "    x = (Path(root) / 'tools' / 'data.json').read_text()\n"
            "    return {'problems': [], 'items': len(entries), 'files': [], 'info': x}")
        self.make_repo({**MODEL, "demo": [{"id": "one"}]}, {
            **FILES, "tools/joints_demo.py": reader, "tools/data.json": "{}\n"})
        self.write("crossweft.json", json.dumps({"joints": {"dir": "tools",
                                                            "sources": ["tools/joints_*.py"]}}))
        self.commit()
        code, output = self.check()
        self.assertEqual(code, 1, output)
        self.assertIn("joints:unlisted-read:demo:tools/data.json", output)
        self.write("crossweft.json", json.dumps({"joints": {"dir": "tools", "sources": [
            "tools/joints_*.py", "tools/data.json"]}}))
        self.commit()
        self.assertEqual(self.check()[0], 0)
        self.write("tools/data.json", '{"changed": 1}\n')
        code, output = self.check("--changed", "HEAD")
        self.assertIn("(full check; --changed fell back)", output)
