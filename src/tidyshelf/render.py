"""Other views of a plan: a readable listing, and a shell script (secondary, less safe).

The JSON plan applied by tidyshelf-apply is the authoritative form (PLAN §2). The shell script is
for reading, or as a no-install fallback; it keeps every check shell can express.
"""

from __future__ import annotations

import os
import re
import shlex

import tidyshelf_apply

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
VARS = {"source": "SOURCE", "movies": "MOVIES", "series": "SERIES"}


def _describe(op: dict) -> list[str]:
    if op["op"] == "move":
        return [f"  move     {op['from']}", f"             -> {op['to']}"]
    if op["op"] == "delete":
        return [f"  delete   {op['path']}{'/' if op.get('dir') else ''}  ({op['reason']})"]
    return [f"  {op['op']:<8} {op['path']}"]


def finding_lines(findings: list, verbose: bool) -> list[str]:
    shown, note = tidyshelf_apply.visible_findings(findings, verbose)
    return [f"    {f['level']}: {f['code']}: {f['msg']}" for f in shown] + ([f"    {note}"] if note else [])


def to_text(doc: dict, verbose: bool = False) -> str:
    items = doc["items"]
    counts = {s: sum(1 for it in items if it["status"] == s) for s in ("ready", "needs_review", "skipped")}
    lines = [f"plan {tidyshelf_apply.plan_id(doc)}: {len(items)} items ({counts['ready']} ready, "
             f"{counts['needs_review']} needs review, {counts['skipped']} skipped)"]
    for it in items:
        lines += ["", f"{it['id']}  {it['status']}  {it.get('source', '')}"]
        lines += finding_lines(it.get("findings", []), verbose)
        for op in it["ops"]:
            lines += _describe(op)
    return "\n".join(lines) + "\n"


def _comment(text: str) -> str:
    return _CONTROL.sub("?", text)  # a newline in a file name must never end a comment


def _path(ref: str) -> str:
    root, rel = ref.split(":", 1)
    return f'"${VARS[root]}"/{shlex.quote(rel)}'


HEADER = r'''#!/usr/bin/env bash
# tidyshelf plan {pid} as a shell script ({tool}, plan made {created}).
#
# LESS SAFE THAN tidyshelf-apply: no journal, no rollback, no lock, and a wider gap between a check
# and the step it checks. Prefer `tidyshelf-apply plan.json`. Read the whole script before running it.
#
# Dry-run unless run with --execute. Every step is checked before the first one runs. A step is
# refused if its file changed (size or mtime), is or sits under a symlink, its destination exists, or
# source and destination are on different filesystems. Deletes only under SOURCE, capped.
# Needs bash and GNU coreutils (stat, realpath, mv -n). Root folders must be real paths (no symlinks).
set -euo pipefail

SOURCE={source}
MOVIES={movies}
SERIES={series}

# Owner and modes for the set_mode steps (from the planning machine's [owner]); empty = unchanged.
OWNER_USER={user}
OWNER_GROUP={group}
DIR_MODE={dir_mode}
FILE_MODE={file_mode}

# This plan deletes {files} files / {nbytes} bytes; refused above these limits unless ALLOW_LARGE_DELETE=1.
DELETE_FILES={files}
DELETE_BYTES={nbytes}
MAX_DELETE_FILES={max_files}
MAX_DELETE_BYTES={max_bytes}

EXECUTE=0
case "${{1:-}}" in --execute) EXECUTE=1 ;; "") ;; *) echo "usage: $0 [--execute]" >&2; exit 1 ;; esac
PASS=check

refuse() {{ echo "refused: $*" >&2; exit 3; }}
step() {{ if [ "$PASS" = run ]; then echo "$*"; elif [ "$EXECUTE" = 0 ]; then echo "would: $*"; fi; }}
inside() {{
  [ "$(realpath -m -- "$1")" = "$1" ] || refuse "$1: a folder on the way is a symlink"
  case "$1" in "$SOURCE"/*|"$MOVIES"/*|"$SERIES"/*) ;; *) refuse "$1: not under a root" ;; esac
}}
existing_up() {{ local p="$1"; while [ ! -e "$p" ]; do p=$(dirname -- "$p"); done; printf '%s' "$p"; }}
same_fs() {{
  [ "$(stat -c %d -- "$(existing_up "$(dirname -- "$1")")")" = "$(stat -c %d -- "$(existing_up "$(dirname -- "$2")")")" ] \
    || refuse "$1 -> $2: different filesystems (never copied)"
}}
is_file() {{ [ -f "$1" ] && [ ! -L "$1" ] && [ "$(stat -c '%s %Y' -- "$1")" = "$2 $3" ]; }}
gone() {{ [ ! -e "$1" ] && [ ! -L "$1" ]; }}
in_source() {{ case "$1" in "$SOURCE"/*) ;; *) refuse "$1: deletes only under the source root" ;; esac; }}

do_mkdir() {{
  inside "$1"
  if [ -d "$1" ] && [ ! -L "$1" ]; then return 0; fi
  gone "$1" || refuse "$1: exists and is not a folder"
  step "mkdir $1"
  if [ "$PASS" = run ]; then mkdir -p -- "$1"; fi
  return 0
}}
do_move() {{
  inside "$1"; inside "$2"
  if gone "$1" && is_file "$2" "$3" "$4"; then return 0; fi
  if [ -L "$1" ]; then refuse "$1: is a symlink (never followed)"; fi
  is_file "$1" "$3" "$4" || refuse "$1: missing, not a regular file, or changed since the plan was made"
  gone "$2" || refuse "$2: exists (never overwritten)"
  same_fs "$1" "$2"
  step "mv $1 -> $2"
  if [ "$PASS" = run ]; then
    mv -n -- "$1" "$2"
    gone "$1" || refuse "$1: not moved (its destination appeared)"
  fi
  return 0
}}
do_delete() {{
  inside "$1"; in_source "$1"
  if gone "$1"; then return 0; fi
  is_file "$1" "$2" "$3" || refuse "$1: not a regular file, or changed since the plan was made"
  step "rm $1"
  if [ "$PASS" = run ]; then rm -- "$1"; fi
  return 0
}}
do_rmdir() {{
  inside "$1"; in_source "$1"
  if gone "$1"; then return 0; fi
  {{ [ -d "$1" ] && [ ! -L "$1" ]; }} || refuse "$1: not a folder"
  step "rmdir $1"
  if [ "$PASS" = run ]; then rmdir -- "$1" || refuse "$1: not empty"; fi
  return 0
}}
do_mode() {{
  inside "$1"
  if [ -L "$1" ]; then refuse "$1: is a symlink (never followed)"; fi
  if [ "$PASS" != run ]; then return 0; fi
  local mode="$FILE_MODE"
  if [ -d "$1" ]; then mode="$DIR_MODE"; fi
  if [ -n "$mode" ]; then chmod -- "$mode" "$1"; fi
  if [ -n "$OWNER_USER$OWNER_GROUP" ]; then
    if [ "$(id -u)" != 0 ]; then echo "warning: not root, owner of $1 left unchanged" >&2
    elif [ -n "$OWNER_USER" ] && [ -n "$OWNER_GROUP" ]; then chown -h -- "$OWNER_USER:$OWNER_GROUP" "$1"
    elif [ -n "$OWNER_USER" ]; then chown -h -- "$OWNER_USER" "$1"
    else chgrp -h -- "$OWNER_GROUP" "$1"; fi
  fi
  return 0
}}

if [ "$DELETE_FILES" -gt "$MAX_DELETE_FILES" ] || [ "$DELETE_BYTES" -gt "$MAX_DELETE_BYTES" ]; then
  [ "${{ALLOW_LARGE_DELETE:-0}}" = 1 ] || refuse "delete cap: $DELETE_FILES files / $DELETE_BYTES bytes (set ALLOW_LARGE_DELETE=1)"
fi

steps() {{
:
'''

FOOTER = '''}

steps
if [ "$EXECUTE" = 1 ]; then
  PASS=run
  steps
  echo "applied plan {pid}"
else
  echo "dry-run: nothing was changed. Run with --execute to apply."
fi
'''


def to_sh(doc: dict, cfg: dict) -> bytes:
    """The plan as a bash script; bytes, so names that are not UTF-8 survive exactly."""
    pid = tidyshelf_apply.plan_id(doc)
    deletes = [op for it in doc["items"] for op in it["ops"] if op["op"] == "delete" and not op.get("dir")]
    owner, safety = cfg["owner"], cfg["safety"]
    q = shlex.quote
    out = [HEADER.format(
        pid=pid, tool=_comment(doc.get("tool", "tidyshelf")), created=_comment(doc.get("created", "?")),
        **{name: q(os.path.realpath(cfg["roots"][name])) for name in VARS},
        user=q(owner["user"]), group=q(owner["group"]), dir_mode=q(owner["dir_mode"]), file_mode=q(owner["file_mode"]),
        files=len(deletes), nbytes=sum(op["size"] for op in deletes),
        max_files=safety["max_delete_files"], max_bytes=safety["max_delete_bytes"])]
    for it in doc["items"]:
        out.append(_comment(f"# {it['id']} {it['status']} {it.get('source', '')}") + "\n")
        if it["status"] != "ready":
            reasons = "; ".join(f["msg"] for f in it.get("findings", []) if f["level"] != "info") or "no steps"
            out.append(_comment(f"#   (not applied: {reasons})") + "\n")
        for op in it["ops"]:
            if op["op"] == "mkdir":
                out.append(f"do_mkdir {_path(op['path'])}\n")
            elif op["op"] == "move":
                out.append(f"do_move {_path(op['from'])} {_path(op['to'])} {op['size']} {op['mtime']}\n")
            elif op["op"] == "delete" and op.get("dir"):
                out.append(f"do_rmdir {_path(op['path'])}\n")
            elif op["op"] == "delete":
                out.append(f"do_delete {_path(op['path'])} {op['size']} {op['mtime']}\n")
            else:
                out.append(f"do_mode {_path(op['path'])}\n")
    out.append(FOOTER.replace("{pid}", pid))
    return "".join(out).encode("utf-8", "surrogateescape")
