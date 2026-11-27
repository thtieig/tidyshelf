"""tidyshelf command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import tidyshelf_apply
from tidyshelf import __version__, config, movies, pyz, render

INIT_TEMPLATE = """\
# tidyshelf config. Only the three folders below are needed; every other setting has a default.
# `tidyshelf config show` prints the full effective config; docs/ADVANCED.md explains each option.
# Paths are as seen from the machine this config is on (your PC and the NAS can differ).

[roots]
# The old collection, to be tidied.
source = "/mnt/nas/to-sort"
# The movie library.
movies = "/mnt/nas/Videos/Movies"
# The series library.
series = "/mnt/nas/Videos/Series"
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors exit 1: exit 2 means "plan has changes"
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(1)


class _Out:
    def __init__(self, args):
        self.json = args.json
        self.quiet = args.quiet
        self.verbose = args.verbose

    def info(self, msg: str) -> None:
        if not self.quiet and not self.json:
            print(msg)

    def warn(self, msg: str) -> None:
        if not self.quiet:
            print(f"warning: {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        print(f"error: {msg}", file=sys.stderr)


def _common(parser: argparse.ArgumentParser, top: bool) -> None:
    # Sub-parsers default to SUPPRESS so a flag given before the subcommand is not reset by it.
    d = (lambda v: v) if top else (lambda v: argparse.SUPPRESS)
    parser.add_argument("--config", metavar="FILE", default=d(None),
                        help="config file (default: $TIDYSHELF_CONFIG, then the usual locations)")
    parser.add_argument("--json", action="store_true", default=d(False), help="machine-readable output")
    parser.add_argument("--quiet", action="store_true", default=d(False), help="only print errors")
    parser.add_argument("--verbose", "-v", action="store_true", default=d(False),
                        help="show every finding (plan and run hide info findings by default)")
    parser.add_argument("--no-color", action="store_true", default=d(False), help="no colour in output")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="tidyshelf", description="Tidy video files into a library.")
    parser.add_argument("--version", action="version", version=f"tidyshelf {__version__}")
    _common(parser, top=True)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_init = sub.add_parser("init", help="write a minimal config (paths only)")
    _common(p_init, top=False)
    p_init.add_argument("--out", metavar="FILE", help="where to write (default: user config file)")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing file")

    p_cfg = sub.add_parser("config", help="check or show the configuration")
    cfg_sub = p_cfg.add_subparsers(dest="config_command", metavar="ACTION")
    for name, text in (("check", "validate the effective config"), ("show", "print the effective config as JSON")):
        _common(cfg_sub.add_parser(name, help=text), top=False)

    p_plan = sub.add_parser("plan", help="plan how to tidy the source folder (changes nothing)")
    _common(p_plan, top=False)
    p_plan.add_argument("paths", nargs="*", metavar="PATH", help="what to plan (default: the whole source root)")
    p_plan.add_argument("--out", metavar="FILE", help="write the plan to FILE, or - for stdout")
    p_plan.add_argument("--deep", action="store_true", help="also decode every video to find damaged files (slow)")
    p_plan.add_argument("--emit", choices=("json", "sh", "text"), default="json",
                        help="json: the plan (default); sh: a shell script (less safe); text: a readable listing")
    p_plan.add_argument("--audit", action="store_true",
                        help="check the movie and series libraries instead of the source (empty plan = consistent)")

    p_plan.add_argument("--kind", choices=("movie", "series"),
                        help="treat every item in this run as a movie (or a series), whatever its name says")

    p_run = sub.add_parser("run", help="plan and apply in one go on the machine that holds the files (dry-run unless --execute)")
    _common(p_run, top=False)
    p_run.add_argument("paths", nargs="*", metavar="PATH", help="what to plan (default: the whole source root)")
    p_run.add_argument("--out", metavar="FILE", help="where to keep the plan (default: the tidyshelf state folder)")
    p_run.add_argument("--audit", action="store_true", help="check the libraries instead of the source")
    p_run.add_argument("--deep", action="store_true", help="also decode every video to find damaged files (slow)")
    for flag, text in (("--execute", "really do it (default: dry-run)"), ("--yes", "do not ask for the typed confirmation"),
                       ("--allow-large-delete", "lift the delete caps")):
        p_run.add_argument(flag, action="store_true", help=text)
    p_run.add_argument("--approve", metavar="PLAN_ID", help="refuse unless the plan id matches")
    p_run.add_argument("--on-error", choices=("stop", "continue"), default="stop", help="as for tidyshelf-apply")

    p_run.add_argument("--kind", choices=("movie", "series"),
                       help="treat every item in this run as a movie (or a series), whatever its name says")

    p_undo = sub.add_parser("undo", help="reverse the moves of a previous apply (dry-run unless --execute)")
    _common(p_undo, top=False)
    p_undo.add_argument("plan", metavar="PLAN", help="the plan file that was applied (its journal is next to it)")
    p_undo.add_argument("--execute", action="store_true", help="really do it (default: dry-run)")
    p_undo.add_argument("--yes", action="store_true", help="do not ask for the typed confirmation")

    p_build = sub.add_parser("build-apply", help="write tidyshelf-apply.pyz, the one-file executor for the NAS")
    _common(p_build, top=False)
    p_build.add_argument("--out", metavar="FILE", default="tidyshelf-apply.pyz", help="(default: %(default)s)")

    p_explain = sub.add_parser("explain", help="show why one item is planned the way it is")
    _common(p_explain, top=False)
    p_explain.add_argument("path", metavar="PATH", help="a folder or file in the source root")
    p_explain.add_argument("--kind", choices=("movie", "series"),
                           help="treat every item in this run as a movie (or a series), whatever its name says")
    return parser


def cmd_init(args, out: _Out) -> int:
    target = Path(args.out) if args.out else config.user_config_path()
    if target.exists() and not args.force:
        out.error(f"{target} exists (use --force to overwrite)")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(INIT_TEMPLATE, encoding="utf-8")
    os.replace(tmp, target)
    out.info(f"wrote {target}; set the three paths, then run: tidyshelf config check")
    return 0


def _load(args):
    try:
        cfg, sources = config.load(args.config)
    except config.ConfigError as exc:
        return None, ["defaults"], config.Report(errors=[str(exc)])
    return cfg, sources, config.validate(cfg)


def cmd_config_check(args, out: _Out) -> int:
    _cfg, sources, report = _load(args)
    if args.json:
        print(json.dumps({"ok": report.ok, "errors": report.errors, "warnings": report.warnings,
                          "sources": sources}, indent=2))
        return 0 if report.ok else 1
    for msg in report.errors:
        out.error(msg)
    for msg in report.warnings:
        out.warn(msg)
    if report.ok:
        out.info(f"config OK ({', '.join(sources)})")
        return 0
    return 1


def cmd_config_show(args, out: _Out) -> int:
    cfg, _sources, report = _load(args)
    if not report.ok:
        for msg in report.errors:
            out.error(msg)
        return 1
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
    return 0


def _planning_config(args, out: _Out):
    """The effective config for planning: valid, and all three roots must exist."""
    cfg, _sources, report = _load(args)
    errors = list(report.errors)
    if cfg is not None and not errors:
        errors += [f"roots.{name}: {path} does not exist" for name, path in cfg["roots"].items()
                   if not os.path.isdir(path)]
    for msg in errors:
        out.error(msg)
    return None if errors else cfg


def _target(item: dict) -> str | None:
    for op in item["ops"]:
        if op["op"] == "mkdir" and op["path"].startswith(("movies:", "series:")):
            return op["path"].split("/", 1)[0]
    return None


def _exit_code(items: list[dict]) -> int:
    if any(it["status"] == "needs_review" for it in items):
        return 4
    return 2 if any(it["ops"] for it in items) else 0


def _summary_lines(items: list[dict], pid: str, out_path: str | None, verbose: bool = False) -> list[str]:
    lines = []
    for it in items:
        target = _target(it)
        lines.append(f"{it['id']}  {it['status']}  {it['source']}" + (f" -> {target}" if target else ""))
        lines += render.finding_lines(it["findings"], verbose)
    counts = {s: sum(1 for it in items if it["status"] == s) for s in ("ready", "needs_review", "skipped")}
    lines.append(f"plan {pid}: {len(items)} items ({counts['ready']} ready, {counts['needs_review']} needs review, "
                 f"{counts['skipped']} skipped)")
    if out_path and out_path != "-":
        lines.append(f"To apply: tidyshelf-apply {out_path}")
    return lines


def _write_atomic(path: Path, data) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    os.replace(tmp, path)


def cmd_plan(args, out: _Out) -> int:
    cfg = _planning_config(args, out)
    if cfg is None:
        return 1
    if args.out and args.out != "-" and not _outside_roots(cfg, args.out, out):
        return 1
    try:
        plan, _traces = movies.build_plan(cfg, args.paths or None, deep=args.deep, audit=args.audit, kind=args.kind)
    except ValueError as exc:
        out.error(str(exc))
        return 1
    for msg in plan.warnings:
        out.warn(msg)
    doc = plan.to_dict()
    pid, code = plan.plan_id, _exit_code(doc["items"])
    data = {"json": lambda: plan.dumps().encode("ascii"), "sh": lambda: render.to_sh(doc, cfg),
            "text": lambda: render.to_text(doc, args.verbose).encode("utf-8", "backslashreplace")}[args.emit]()
    to_stdout = args.out == "-" or (args.out is None and args.emit != "json")
    if to_stdout:
        sys.stdout.flush()
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    elif args.out:
        _write_atomic(Path(args.out), data)
        if args.emit == "sh":
            os.chmod(args.out, 0o755)
    if args.json:
        summary = {"plan_id": pid, "exit": code, "items": [
            {"id": it["id"], "status": it["status"], "kind": it["kind"], "source": it["source"],
             "target": _target(it), "findings": it["findings"]} for it in doc["items"]]}
        print(json.dumps(summary, indent=2, ensure_ascii=True), file=sys.stderr if to_stdout else sys.stdout)
    elif not args.quiet:
        stream = sys.stderr if to_stdout else sys.stdout
        for line in _summary_lines(doc["items"], pid, args.out, args.verbose):
            print(line, file=stream)
    return code


def state_dir() -> Path:
    """Where `run` keeps its plans (and their journals, for `undo`)."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "tidyshelf" / "plans"


def _outside_roots(cfg: dict, path: str, out: _Out) -> bool:
    real = os.path.realpath(path)
    for name, root in cfg["roots"].items():
        base = os.path.realpath(root)
        if os.path.commonpath([real, base]) == base:
            out.error(f"--out {path}: is inside roots.{name}; keep the plan somewhere else")
            return False
    return True


def _passthrough(args) -> list[str]:
    argv = []
    for name in ("config",):
        if getattr(args, name, None):
            argv += [f"--{name}", getattr(args, name)]
    for flag in ("execute", "yes", "allow_large_delete", "json", "quiet", "verbose"):
        if getattr(args, flag, False):
            argv.append("--" + flag.replace("_", "-"))
    if getattr(args, "approve", None):
        argv += ["--approve", args.approve]
    if getattr(args, "on_error", None):
        argv += ["--on-error", args.on_error]
    return argv


def cmd_run(args, out: _Out) -> int:
    cfg = _planning_config(args, out)
    if cfg is None:
        return 1
    try:
        plan, _traces = movies.build_plan(cfg, args.paths or None, deep=args.deep, audit=args.audit, kind=args.kind)
    except ValueError as exc:
        out.error(str(exc))
        return 1
    for msg in plan.warnings:
        out.warn(msg)
    if args.out == "-":
        out.error("run needs a plan file (the journal and undo live next to it); --out - is not possible")
        return 1
    target = Path(args.out) if args.out else state_dir() / f"plan-{plan.plan_id}.json"
    if not _outside_roots(cfg, str(target), out):
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(target, plan.dumps())
    out.info(f"plan {plan.plan_id} kept in {target} (for `tidyshelf undo {target}`)")
    return tidyshelf_apply.main([str(target)] + _passthrough(args))


def cmd_undo(args, out: _Out) -> int:
    return tidyshelf_apply.main([args.plan, "--undo"] + _passthrough(args))


def cmd_explain(args, out: _Out) -> int:
    cfg = _planning_config(args, out)
    if cfg is None:
        return 1
    try:
        plan, traces = movies.build_plan(cfg, [args.path], kind=args.kind)
    except ValueError as exc:
        out.error(str(exc))
        return 1
    for msg in plan.warnings:
        out.warn(msg)
    (item,) = plan.to_dict()["items"]
    trace = traces[item["id"]]
    if args.json:
        print(json.dumps({"source": item["source"], "trace": trace, "item": item}, indent=2, ensure_ascii=True))
        return 0
    out.info(f"{item['source']}")
    for line in trace:
        out.info(f"  {line}")
    out.info(f"status: {item['status']}")
    for f in item["findings"]:
        out.info(f"  {f['level']}: {f['code']}: {f['msg']}")
    for op in item["ops"]:
        where = op.get("path") or f"{op['from']} -> {op['to']}"
        out.info(f"  {op['op']:<8} {where}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # names that are not valid UTF-8 print as \udcxx, never crash
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help / --version (0) or a usage error (1)
        return exc.code if isinstance(exc.code, int) else 1
    out = _Out(args)
    if args.command == "init":
        return cmd_init(args, out)
    if args.command == "config" and args.config_command == "check":
        return cmd_config_check(args, out)
    if args.command == "config" and args.config_command == "show":
        return cmd_config_show(args, out)
    if args.command == "plan":
        return cmd_plan(args, out)
    if args.command == "explain":
        return cmd_explain(args, out)
    if args.command == "build-apply":
        target = pyz.build(Path(args.out))
        out.info(f"wrote {target}; copy it to the NAS and run: python3 {target.name} plan.json")
        return 0
    if args.command == "run":
        return cmd_run(args, out)
    if args.command == "undo":
        return cmd_undo(args, out)
    parser.print_usage(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
