"""tidyshelf command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tidyshelf import __version__, config

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


def main(argv: list[str] | None = None) -> int:
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
    parser.print_usage(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
