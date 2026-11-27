"""tidyshelf command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tidyshelf import __version__, config, movies

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
    p_plan.add_argument("--audit", action="store_true",
                        help="check the movie and series libraries instead of the source (empty plan = consistent)")

    p_explain = sub.add_parser("explain", help="show why one item is planned the way it is")
    _common(p_explain, top=False)
    p_explain.add_argument("path", metavar="PATH", help="a folder or file in the source root")
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


def _summary_lines(items: list[dict], pid: str, out_path: str | None) -> list[str]:
    lines = []
    for it in items:
        target = _target(it)
        lines.append(f"{it['id']}  {it['status']}  {it['source']}" + (f" -> {target}" if target else ""))
        lines += [f"    {f['level']}: {f['code']}: {f['msg']}" for f in it["findings"]]
    counts = {s: sum(1 for it in items if it["status"] == s) for s in ("ready", "needs_review", "skipped")}
    lines.append(f"plan {pid}: {len(items)} items ({counts['ready']} ready, {counts['needs_review']} needs review, "
                 f"{counts['skipped']} skipped)")
    if out_path and out_path != "-":
        lines.append(f"To apply: tidyshelf-apply {out_path}")
    return lines


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def cmd_plan(args, out: _Out) -> int:
    cfg = _planning_config(args, out)
    if cfg is None:
        return 1
    if args.out and args.out != "-":
        real = os.path.realpath(args.out)
        for name, root in cfg["roots"].items():
            base = os.path.realpath(root)
            if os.path.commonpath([real, base]) == base:
                out.error(f"--out {args.out}: is inside roots.{name}; write the plan somewhere else")
                return 1
    try:
        plan, _traces = movies.build_plan(cfg, args.paths or None, deep=args.deep, audit=args.audit)
    except ValueError as exc:
        out.error(str(exc))
        return 1
    for msg in plan.warnings:
        out.warn(msg)
    doc = plan.to_dict()
    pid, code = plan.plan_id, _exit_code(doc["items"])
    if args.out == "-":
        sys.stdout.write(plan.dumps())
    elif args.out:
        _write_atomic(Path(args.out), plan.dumps())
    if args.json:
        summary = {"plan_id": pid, "exit": code, "items": [
            {"id": it["id"], "status": it["status"], "kind": it["kind"], "source": it["source"],
             "target": _target(it), "findings": it["findings"]} for it in doc["items"]]}
        print(json.dumps(summary, indent=2, ensure_ascii=True), file=sys.stderr if args.out == "-" else sys.stdout)
    elif not args.quiet:
        stream = sys.stderr if args.out == "-" else sys.stdout
        for line in _summary_lines(doc["items"], pid, args.out):
            print(line, file=stream)
    return code


def cmd_explain(args, out: _Out) -> int:
    cfg = _planning_config(args, out)
    if cfg is None:
        return 1
    try:
        plan, traces = movies.build_plan(cfg, [args.path])
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
    parser.print_usage(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
