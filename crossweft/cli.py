"""Command line: `crossweft <command>` (also `python -m crossweft`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, discover, engine, harness, hooks, runner


def _config(args: argparse.Namespace) -> engine.Config:
    root = Path(args.root).resolve() if args.root else engine.find_repo_root(Path.cwd())
    return engine.load_config(root)


def _declare_agent(root: Path, launcher: str, set_harness: bool, dry_run: bool) -> list[str]:
    """Record in crossweft.json what the installed harness relies on:
    agent.command, the one declared way the hooks start crossweft (what `check`
    compares them with), and agent.harness = true unless it is explicitly
    false. Returns the notes; raises OSError when the file cannot be written."""
    config_path = root / engine.CONFIG_FILE
    data = json.loads(config_path.read_text(encoding="utf-8"))   # load_config validated it
    agent = data.setdefault("agent", {})
    notes = []
    if agent.get("harness") is False:
        # an explicit opt-out is a decision, not a gap to fill in
        engine.out(f"[NOTE] {engine.CONFIG_FILE}: agent.harness is false, so `crossweft check` "
                   "does not guard the harness; left as it is (set it to true to guard it)")
    elif set_harness and agent.get("harness") is not True:
        agent["harness"] = True
        notes.append(f"{engine.CONFIG_FILE}: set agent.harness = true, so `crossweft check` "
                     "fails when the harness goes missing")
    if launcher != agent.get("command", harness.DEFAULT_COMMAND):
        agent["command"] = launcher
        notes.append(f"{engine.CONFIG_FILE}: agent.command = `{launcher}` -- how the hooks start "
                     "crossweft; `crossweft check` accepts only hooks that start it this way")
    if notes and not dry_run:
        harness.write_text(config_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    for note in notes:
        engine.out(f"[{'DRY' if dry_run else 'OK'}] {note}")
    return notes


def run_agents(cfg: engine.Config, command: str | None, dry_run: bool) -> int:
    try:
        # --command replaces the declared launcher (and is recorded below)
        launcher = command.strip() if command is not None else \
            harness.configured_command(cfg.root)
        if not launcher:
            raise harness.HarnessError("--command must not be empty")
        changes = harness.install(cfg.root, cfg.model_dir.as_posix(), launcher, dry_run)
    except harness.HarnessWriteError as exc:
        engine.out(f"[ERR] {exc}")     # says itself what was written before the failure
        return 2
    except harness.HarnessError as exc:
        engine.out(f"[ERR] {exc} -- nothing was written")
        return 2
    for change in changes:
        engine.out(f"[{'DRY' if dry_run else 'OK'}] {change}")
    try:
        changes += _declare_agent(cfg.root, launcher, True, dry_run)
    except OSError as exc:
        engine.out(f"[ERR] could not write {engine.CONFIG_FILE} ({exc}); the harness files "
                   "above were written")
        return 2
    if not changes:
        engine.out("[OK] the agent harness is already in place")
    else:
        engine.out(f"{len(changes)} change(s) {'would be made' if dry_run else 'made'}. Commit "
                   "them: every agent that opens this repository gets the seam hooks, the rules "
                   "and the skill.")
    return 0


def _positive_int(text: str) -> int:
    """--limit 0 would print "no candidates" without looking: refuse it."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def _skip_hook(message: str) -> None:
    """argparse's error() for a `crossweft hook` call. Its default exits 2, which
    blocks an agent at Stop: skip the hook with exit 1 instead, which the agent
    shows as a non-blocking notice (see hooks.run_hook)."""
    sys.stderr.write(f"crossweft hook: {message} -- skipped (is the hook configuration "
                     "newer than crossweft?)\n")
    sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crossweft",
        description="Every hand-written seam needs a guard: keep the places where components "
                    "meet in agreement across languages.")
    parser.add_argument("--version", action="version", version=f"crossweft {__version__}")
    parser.add_argument("--root", help="repository root (default: walk up to crossweft.json)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    init = sub.add_parser("init", help="create crossweft.json, an empty model and the agent "
                                       "harness")
    init.add_argument("--language", default="en", help="generated docs language (en, ru)")
    init.add_argument("--no-agents", action="store_true",
                      help="do not install the agent harness (see `crossweft agents`)")
    init.add_argument("--command", dest="launcher", default=harness.DEFAULT_COMMAND,
                      help="how the hooks start crossweft, recorded as agent.command "
                           "(default: crossweft)")

    agents = sub.add_parser("agents", help="make the repository carry its agent harness: "
                                           "hooks, AGENTS.md rules, skill")
    agents.add_argument("--command", dest="launcher", default=None,
                        help="how the hooks start crossweft, recorded as agent.command in "
                             "crossweft.json (default: agent.command, else crossweft; e.g. "
                             "'python -m crossweft' for a vendored copy)")
    agents.add_argument("--dry-run", action="store_true", help="print what would change")

    check = sub.add_parser("check", help="verify the model against the code",
        description="Verify every guard, anchor, route and finding in the model against the "
                    "code.",
        epilog="exit codes: 0 = consistent; 1 = problems (each printed with its key); "
               "2 = no verdict: the config or model could not be read or validated, or "
               "nothing was scanned -- never a pass. "
               "Example: crossweft check --format github")
    check.add_argument("--format", choices=("text", "json", "github", "sarif"), default="text")
    check.add_argument("--json", action="store_true", help="same as --format json")

    render = sub.add_parser("render", help="write the generated docs and viewer")
    render.add_argument("--check", action="store_true", help="only verify they are up to date")

    show = sub.add_parser("show", help="print one entity and its neighbours")
    show.add_argument("id")

    impact = sub.add_parser("impact", help="which seams a change touches, and where the other "
                                           "side of each lives")
    impact.add_argument("--base", help="also count committed work since the merge-base with "
                                       "this ref (e.g. origin/main)")
    impact.add_argument("--json", action="store_true")
    impact.add_argument("paths", nargs="*", help="explicit files/dirs instead of git changes")

    attest = sub.add_parser("attest", help="record that the regions of a pair were read and "
                                           "agree")
    attest.add_argument("ids", nargs="*", help="pair ids")
    attest.add_argument("--reason", help="what you compared (required, lands in the lock file)")
    attest.add_argument("--all", action="store_true", help="every changed or unattested pair")
    attest.add_argument("--prune", action="store_true",
                        help="drop lock entries for pairs the model no longer declares")
    attest.add_argument("--other-side-unchanged", dest="other_side_unchanged",
                        action="store_true",
                        help="only some regions changed and you re-read the others: they are "
                             "already equivalent (otherwise attest refuses)")

    base = sub.add_parser("baseline",
                          help="record every current problem as open findings (adopting in a "
                               "repo with existing drift); new drift still fails")
    base.add_argument("--owner", help="who owns the recorded drift (required)")
    base.add_argument("--next-step", dest="next_step", help="what happens next (required)")
    base.add_argument("--file", default="90-baseline.json",
                      help="model file to create (default: 90-baseline.json)")

    disc = sub.add_parser("discover", help="list seams that exist in the code but not on the map")
    disc.add_argument("--json", action="store_true")
    disc.add_argument("--limit", type=_positive_int, default=20,
                      help="how many candidates to list (at least 1)")

    val = sub.add_parser("validators", help="run every validate_*.py and prove each did work")
    val.add_argument("--dir", help="validators directory (default: validators.dir in "
                                   "crossweft.json)")
    val.add_argument("--self-test", action="store_true", help="prove the runner itself works")

    hook = sub.add_parser("hook", help="agent hook adapter (reads the agent's JSON on stdin)")
    # validated by hooks.run_hook, not by argparse: a usage error exits 2, which
    # blocks an agent at Stop (see run_hook)
    hook.add_argument("agent", nargs="?", help=f"one of {', '.join(hooks.AGENTS)}")
    hook.add_argument("event", nargs="?", help=f"one of {', '.join(hooks.EVENTS)}")
    hook.add_argument("--from-plugin", action="store_true",
                      help="called by the Claude Code plugin: stay quiet when the project's "
                           "own hooks already run crossweft")
    hook.error = _skip_hook   # type: ignore[method-assign]

    sub.add_parser("self-test", help="run the engine and runner self-tests (planted fixtures)")
    return parser


def _hook_stdin() -> bytes | str:
    """The agent's payload as bytes, so run_hook decodes it as UTF-8 whatever the
    console code page is. A stdin replaced in-process by a text stream (no
    .buffer) is already decoded; no stdin at all is an empty payload."""
    if sys.stdin is None:
        return b""
    stream = getattr(sys.stdin, "buffer", None)
    return stream.read() if stream is not None else sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if "hook" in (sys.argv[1:] if argv is None else argv):
        # a usage mistake before the subcommand (`--root` without its value)
        # is still a hook call: never exit 2
        parser.error = _skip_hook   # type: ignore[method-assign]
    args, unknown = parser.parse_known_args(argv)
    command = args.command
    if unknown:
        if command != "hook":
            parser.error(f"unrecognized arguments: {' '.join(unknown)}")
        # a hook configured by a newer crossweft must not be blocked by usage
        sys.stderr.write(f"crossweft hook: ignoring unknown argument(s) {' '.join(unknown)} "
                         "(is the hook configuration newer than crossweft?)\n")
    if command is None:
        parser.print_help()
        return 2
    if command == "self-test":
        code = engine.self_test() or runner._run_self_test()
        plugins = engine.run_joint_self_tests(Path(args.root).resolve() if args.root else None)
        return code or plugins
    if command == "hook":
        return hooks.run_hook(args.agent, args.event, _hook_stdin(),
                              from_plugin=args.from_plugin)
    if command == "init":
        root = Path(args.root).resolve() if args.root else Path.cwd()
        launcher = args.launcher.strip()
        if not launcher:
            engine.out("[ERR] --command must not be empty -- nothing written")
            return 2
        code = engine.run_init(root, args.language, agents=not args.no_agents,
                               command=launcher)
        if code == 0 and not args.no_agents:
            try:
                _declare_agent(root, launcher, False, dry_run=False)
            except OSError as exc:
                engine.out(f"[ERR] could not record agent.command in {engine.CONFIG_FILE} "
                           f"({exc}); `crossweft check` fails until it matches the hooks")
                return 2
        return code
    if command == "validators" and args.self_test:
        return runner._run_self_test()
    try:
        cfg = _config(args)
    except engine.ModelError as exc:
        if command == "check" and args.format == "sarif" and not args.json:
            print(engine.sarif_config_error(str(exc)))   # stdout stays one SARIF document
        else:
            engine.out(f"[ERR] {exc}")
        return 2
    if command == "check":
        return engine.run_check(cfg, "json" if args.json else args.format)
    if command == "render":
        return engine.run_render(cfg, check_only=args.check)
    if command == "show":
        return engine.run_show(cfg, args.id)
    if command == "impact":
        if args.base and args.paths:
            engine.out("[ERR] impact takes either --base or explicit paths, not both")
            return 2
        # paths on the command line are relative to where you are, not to the root
        paths = [p if Path(p).is_absolute() else str((Path.cwd() / p).resolve())
                 for p in args.paths]
        return engine.run_impact(cfg, args.base, paths, as_json=args.json)
    if command == "agents":
        return run_agents(cfg, args.launcher, args.dry_run)
    if command == "attest":
        return engine.run_attest(cfg, args.ids, args.reason, args.all, args.prune,
                                 args.other_side_unchanged)
    if command == "baseline":
        return engine.run_baseline(cfg, args.owner, args.next_step, args.file)
    if command == "discover":
        return discover.run_discover(cfg, args.limit, args.json)
    if command == "validators":
        directory = Path(args.dir) if args.dir else cfg.validators_dir
        if directory is None:
            engine.out("[ERR] no validators directory: pass --dir or set validators.dir in "
                       "crossweft.json")
            return 2
        if not directory.is_absolute():
            directory = cfg.root / directory
        return runner.run_validators(cfg.root, directory, cfg.validator_timeout,
                                     cfg.validator_timeouts)
    parser.print_help()
    return 2
