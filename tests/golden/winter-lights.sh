#!/usr/bin/env bash
# tidyshelf plan d57acbf27942 as a shell script (tidyshelf 0.1.0.dev0, plan made 2026-11-26T20:15:00Z).
#
# LESS SAFE THAN tidyshelf-apply: no journal, no rollback, no lock, and a wider gap between a check
# and the step it checks. Prefer `tidyshelf-apply plan.json`. Read the whole script before running it.
#
# Dry-run unless run with --execute. Every step is checked before the first one runs. A step is
# refused if its file changed (size or mtime), is or sits under a symlink, its destination exists, or
# source and destination are on different filesystems. Deletes only under SOURCE, capped.
# Needs bash and GNU coreutils (stat, realpath, mv -n). Root folders must be real paths (no symlinks).
set -euo pipefail

SOURCE=/srv/example/to-sort
MOVIES=/srv/example/Movies
SERIES=/srv/example/Series

# Owner and modes for the set_mode steps (from the planning machine's [owner]); empty = unchanged.
OWNER_USER=''
OWNER_GROUP=''
DIR_MODE=''
FILE_MODE=''

# This plan deletes 0 files / 0 bytes; refused above these limits unless ALLOW_LARGE_DELETE=1.
DELETE_FILES=0
DELETE_BYTES=0
MAX_DELETE_FILES=200
MAX_DELETE_BYTES=52428800

EXECUTE=0
case "${1:-}" in --execute) EXECUTE=1 ;; "") ;; *) echo "usage: $0 [--execute]" >&2; exit 1 ;; esac
PASS=check

refuse() { echo "refused: $*" >&2; exit 3; }
step() { if [ "$PASS" = run ]; then echo "$*"; elif [ "$EXECUTE" = 0 ]; then echo "would: $*"; fi; }
inside() {
  [ "$(realpath -m -- "$1")" = "$1" ] || refuse "$1: a folder on the way is a symlink"
  case "$1" in "$SOURCE"/*|"$MOVIES"/*|"$SERIES"/*) ;; *) refuse "$1: not under a root" ;; esac
}
existing_up() { local p="$1"; while [ ! -e "$p" ]; do p=$(dirname -- "$p"); done; printf '%s' "$p"; }
same_fs() {
  [ "$(stat -c %d -- "$(existing_up "$(dirname -- "$1")")")" = "$(stat -c %d -- "$(existing_up "$(dirname -- "$2")")")" ] \
    || refuse "$1 -> $2: different filesystems (never copied)"
}
is_file() { [ -f "$1" ] && [ ! -L "$1" ] && [ "$(stat -c '%s %Y' -- "$1")" = "$2 $3" ]; }
gone() { [ ! -e "$1" ] && [ ! -L "$1" ]; }
in_source() { case "$1" in "$SOURCE"/*) ;; *) refuse "$1: deletes only under the source root" ;; esac; }

do_mkdir() {
  inside "$1"
  if [ -d "$1" ] && [ ! -L "$1" ]; then return 0; fi
  gone "$1" || refuse "$1: exists and is not a folder"
  step "mkdir $1"
  if [ "$PASS" = run ]; then mkdir -p -- "$1"; fi
  return 0
}
do_move() {
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
}
do_delete() {
  inside "$1"; in_source "$1"
  if gone "$1"; then return 0; fi
  is_file "$1" "$2" "$3" || refuse "$1: not a regular file, or changed since the plan was made"
  step "rm $1"
  if [ "$PASS" = run ]; then rm -- "$1"; fi
  return 0
}
do_rmdir() {
  inside "$1"; in_source "$1"
  if gone "$1"; then return 0; fi
  { [ -d "$1" ] && [ ! -L "$1" ]; } || refuse "$1: not a folder"
  step "rmdir $1"
  if [ "$PASS" = run ]; then rmdir -- "$1" || refuse "$1: not empty"; fi
  return 0
}
do_mode() {
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
}

if [ "$DELETE_FILES" -gt "$MAX_DELETE_FILES" ] || [ "$DELETE_BYTES" -gt "$MAX_DELETE_BYTES" ]; then
  [ "${ALLOW_LARGE_DELETE:-0}" = 1 ] || refuse "delete cap: $DELETE_FILES files / $DELETE_BYTES bytes (set ALLOW_LARGE_DELETE=1)"
fi

steps() {
:
# movie-0001 ready source:www.example.com - Winter Lights (2004) 1080p BluRay-GRP
do_mkdir "$MOVIES"/'Winter Lights (2004)/Subs'
do_mkdir "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs'
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP.mp4' "$MOVIES"/'Winter Lights (2004)/Winter Lights (2004).mp4' 1000 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English SDH.eng.srt' "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).en.sdh.srt' 3215 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/Spanish.spa.srt' "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).es.srt' 2652 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English.eng.srt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English.eng.srt' 2572 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP.srt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP.srt' 2572 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/readme.txt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/readme.txt' 86 1723000000
do_mode "$MOVIES"/'Winter Lights (2004)'
do_mode "$MOVIES"/'Winter Lights (2004)/Subs'
do_mode "$MOVIES"/'Winter Lights (2004)/Winter Lights (2004).mp4'
do_mode "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).en.sdh.srt'
do_mode "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).es.srt'
do_rmdir "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs'
do_rmdir "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP'
}

steps
if [ "$EXECUTE" = 1 ]; then
  PASS=run
  steps
  echo "applied plan d57acbf27942"
else
  echo "dry-run: nothing was changed. Run with --execute to apply."
fi
