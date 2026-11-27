#!/usr/bin/env bash
# tidyshelf plan e52a9b40a2d8 as a shell script (tidyshelf 0.2.0, plan made 2026-11-26T20:15:00Z).
#
# LESS SAFE THAN tidyshelf-apply: no journal, no rollback, no lock, and a wider gap between a check
# and the step it checks. Prefer `tidyshelf-apply plan.json`. Read the whole script before running it.
#
# Dry-run unless run with --execute. Every step is checked before the first one runs. A step is
# refused if its file changed (size or mtime), is or sits under a symlink, its destination exists, or
# source and destination are on different filesystems. Deletes only under SOURCE, capped.
# Needs bash and GNU coreutils (stat, realpath, mv -n, sha256sum, base64). Root folders must be real paths (no symlinks).
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
do_write() {  # to src size mtime src_sha256 data_sha256 data_base64
  inside "$1"; inside "$2"
  case "$1" in "$MOVIES"/*|"$SERIES"/*) ;; *) refuse "$1: a write goes only into the movies or series folder" ;; esac
  case "$2" in "$SOURCE"/*) ;; *) refuse "$2: a write is made from a file under the source root" ;; esac
  [ "${#7}" -le 6990514 ] || refuse "$1: data is larger than 5242880 bytes"
  [ "$(printf '%s' "$7" | base64 -d | sha256sum | cut -d' ' -f1)" = "$6" ] || refuse "$1: data does not match its sha256"
  if [ -f "$1" ] && [ ! -L "$1" ] && [ "$(sha256sum -- "$1" | cut -d' ' -f1)" = "$6" ]; then return 0; fi
  if [ -L "$2" ]; then refuse "$2: is a symlink (never followed)"; fi
  is_file "$2" "$3" "$4" || refuse "$2: missing, not a regular file, or changed since the plan was made"
  [ "$(sha256sum -- "$2" | cut -d' ' -f1)" = "$5" ] || refuse "$2: content changed since the plan was made"
  gone "$1" || refuse "$1: exists (never overwritten)"
  step "write $1 (cleaned from $2)"
  if [ "$PASS" = run ]; then
    local tmp
    tmp=$(mktemp -- "$(dirname -- "$1")/.tidyshelf-tmp.XXXXXX") || refuse "$1: cannot create a temporary file"
    printf '%s' "$7" | base64 -d > "$tmp" || { rm -f -- "$tmp"; refuse "$1: cannot write"; }
    if [ "$(sha256sum -- "$tmp" | cut -d' ' -f1)" != "$6" ]; then rm -f -- "$tmp"; refuse "$1: written data differs"; fi
    chmod 0644 -- "$tmp"
    if ! ln -- "$tmp" "$1"; then rm -f -- "$tmp"; refuse "$1: its destination appeared (never overwritten)"; fi
    rm -f -- "$tmp"
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
do_write "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).en.sdh.srt' "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English SDH.eng.srt' 3215 1723000000 343c218710f7372a41f98c0f715bc63f80aea645f5675be2235810b3d65e2c7c 02cc6c497fe8d9d53d489ef45015e14227f3502dbd2f1586da28b05c4fab9c31 MQowMDowMDowMywwMDAgLS0+IDAwOjAwOjA1LDUwMApXaGVyZSB3ZXJlIHlvdSBsYXN0IG5pZ2h0PyBJIHdhcyB3YWl0aW5nIGZvciB5b3UuCgoyCjAwOjAwOjA2LDAwMCAtLT4gMDA6MDA6MDgsNTAwCltXSU5EIEhPV0xJTkddCgozCjAwOjAwOjA5LDAwMCAtLT4gMDA6MDA6MTEsNTAwCkkga25vdywgSSdtIHNvcnJ5LiBUaGUgdHJhaW4gd2FzIGxhdGUgYW5kIG15IHBob25lIHdhcyBkZWFkLgoKNAowMDowMDoxMiwwMDAgLS0+IDAwOjAwOjE0LDUwMApZb3UgY291bGQgaGF2ZSBjYWxsZWQgZnJvbSB0aGUgc3RhdGlvbi4KCjUKMDA6MDA6MTUsMDAwIC0tPiAwMDowMDoxNyw1MDAKVGhlcmUgd2FzIG5vYm9keSB0aGVyZSwgYW5kIGl0IHdhcyBzbm93aW5nIHNvIGhhcmQgSSBjb3VsZCBub3Qgc2VlIHRoZSByb2FkLgoKNgowMDowMDoxOCwwMDAgLS0+IDAwOjAwOjIwLDUwMAooRE9PUiBDTE9TRVMpCgo3CjAwOjAwOjIxLDAwMCAtLT4gMDA6MDA6MjMsNTAwCldlbGwsIHlvdSBhcmUgaGVyZSBub3cuIENvbWUgaW4gYW5kIHNpdCBieSB0aGUgZmlyZS4KCjgKMDA6MDA6MjQsMDAwIC0tPiAwMDowMDoyNiw1MDAKVGhhbmsgeW91LiBJcyB5b3VyIG1vdGhlciBzdGlsbCBhd2FrZT8KCjkKMDA6MDA6MjcsMDAwIC0tPiAwMDowMDoyOSw1MDAKU2hlIHdlbnQgdG8gYmVkIGFuIGhvdXIgYWdvLiBTaGUgc2FpZCB0byB0ZWxsIHlvdSB0aGF0IHNoZSBpcyBoYXBweSB5b3UgY2FtZS4KCjEwCjAwOjAwOjMwLDAwMCAtLT4gMDA6MDA6MzIsNTAwCuKZqiBTT0ZUIE1VU0lDIFBMQVlJTkcg4pmqCgoxMQowMDowMDozMywwMDAgLS0+IDAwOjAwOjM1LDUwMApJIGhhdmUgc29tZXRoaW5nIGZvciBoZXIuIEl0IGlzIGluIG15IGJhZywgYnV0IGl0IGNhbiB3YWl0IHVudGlsIHRoZSBtb3JuaW5nLgoKMTIKMDA6MDA6MzYsMDAwIC0tPiAwMDowMDozOCw1MDAKRG8geW91IHdhbnQgc29tZSB0ZWE/IFRoZXJlIGlzIHNvdXAgYXMgd2VsbCwgaWYgeW91IGFyZSBodW5ncnkuCgoxMwowMDowMDozOSwwMDAgLS0+IDAwOjAwOjQxLDUwMApKdXN0IHRlYSwgcGxlYXNlLiBJIGF0ZSBvbiB0aGUgd2F5LCBidXQgaXQgd2FzIG5vdCB2ZXJ5IGdvb2QuCgoxNAowMDowMDo0MiwwMDAgLS0+IDAwOjAwOjQ0LDUwMApbRklSRSBDUkFDS0xJTkddCgoxNQowMDowMDo0NSwwMDAgLS0+IDAwOjAwOjQ3LDUwMApXZSBzaG91bGQgdGFsayBhYm91dCB3aGF0IGhhcHBlbmVkIGF0IHRoZSBob3VzZS4KCjE2CjAwOjAwOjQ4LDAwMCAtLT4gMDA6MDA6NTAsNTAwCkkga25vdyB3ZSBzaG91bGQuIEJ1dCBub3QgdG9uaWdodC4gVG9uaWdodCBJIGp1c3Qgd2FudCB0byBiZSBoZXJlIHdpdGggeW91LgoKMTcKMDA6MDA6NTEsMDAwIC0tPiAwMDowMDo1Myw1MDAKQWxsIHJpZ2h0LiBUb21vcnJvdywgdGhlbi4gQW5kIHRoaXMgdGltZSB5b3Ugd2lsbCB0ZWxsIG1lIGV2ZXJ5dGhpbmcuCgoxOAowMDowMDo1NCwwMDAgLS0+IDAwOjAwOjU2LDUwMAooU0lHSFMpCgoxOQowMDowMDo1NywwMDAgLS0+IDAwOjAwOjU5LDUwMApJIHdpbGwuIEkgcHJvbWlzZS4gTm93LCB3aGVyZSBkaWQgeW91IHB1dCB0aGUgZ29vZCBjdXBzPwoKMjAKMDA6MDE6MDAsMDAwIC0tPiAwMDowMTowMiw1MDAKV2hlcmUgd2VyZSB5b3UgbGFzdCBuaWdodD8gSSB3YXMgd2FpdGluZyBmb3IgeW91LgoKMjEKMDA6MDE6MDMsMDAwIC0tPiAwMDowMTowNSw1MDAKSSBrbm93LCBJJ20gc29ycnkuIFRoZSB0cmFpbiB3YXMgbGF0ZSBhbmQgbXkgcGhvbmUgd2FzIGRlYWQuCgoyMgowMDowMTowNiwwMDAgLS0+IDAwOjAxOjA4LDUwMApbV0lORCBIT1dMSU5HXQoKMjMKMDA6MDE6MDksMDAwIC0tPiAwMDowMToxMSw1MDAKWW91IGNvdWxkIGhhdmUgY2FsbGVkIGZyb20gdGhlIHN0YXRpb24uCgoyNAowMDowMToxMiwwMDAgLS0+IDAwOjAxOjE0LDUwMApUaGVyZSB3YXMgbm9ib2R5IHRoZXJlLCBhbmQgaXQgd2FzIHNub3dpbmcgc28gaGFyZCBJIGNvdWxkIG5vdCBzZWUgdGhlIHJvYWQuCgoyNQowMDowMToxNSwwMDAgLS0+IDAwOjAxOjE3LDUwMApXZWxsLCB5b3UgYXJlIGhlcmUgbm93LiBDb21lIGluIGFuZCBzaXQgYnkgdGhlIGZpcmUuCgoyNgowMDowMToxOCwwMDAgLS0+IDAwOjAxOjIwLDUwMAooRE9PUiBDTE9TRVMpCgoyNwowMDowMToyMSwwMDAgLS0+IDAwOjAxOjIzLDUwMApUaGFuayB5b3UuIElzIHlvdXIgbW90aGVyIHN0aWxsIGF3YWtlPwoKMjgKMDA6MDE6MjQsMDAwIC0tPiAwMDowMToyNiw1MDAKU2hlIHdlbnQgdG8gYmVkIGFuIGhvdXIgYWdvLiBTaGUgc2FpZCB0byB0ZWxsIHlvdSB0aGF0IHNoZSBpcyBoYXBweSB5b3UgY2FtZS4KCjI5CjAwOjAxOjI3LDAwMCAtLT4gMDA6MDE6MjksNTAwCkkgaGF2ZSBzb21ldGhpbmcgZm9yIGhlci4gSXQgaXMgaW4gbXkgYmFnLCBidXQgaXQgY2FuIHdhaXQgdW50aWwgdGhlIG1vcm5pbmcuCgozMAowMDowMTozMCwwMDAgLS0+IDAwOjAxOjMyLDUwMArimaogU09GVCBNVVNJQyBQTEFZSU5HIOKZqgoKMzEKMDA6MDE6MzMsMDAwIC0tPiAwMDowMTozNSw1MDAKRG8geW91IHdhbnQgc29tZSB0ZWE/IFRoZXJlIGlzIHNvdXAgYXMgd2VsbCwgaWYgeW91IGFyZSBodW5ncnkuCgozMgowMDowMTozNiwwMDAgLS0+IDAwOjAxOjM4LDUwMApKdXN0IHRlYSwgcGxlYXNlLiBJIGF0ZSBvbiB0aGUgd2F5LCBidXQgaXQgd2FzIG5vdCB2ZXJ5IGdvb2QuCgozMwowMDowMTozOSwwMDAgLS0+IDAwOjAxOjQxLDUwMApXZSBzaG91bGQgdGFsayBhYm91dCB3aGF0IGhhcHBlbmVkIGF0IHRoZSBob3VzZS4KCjM0CjAwOjAxOjQyLDAwMCAtLT4gMDA6MDE6NDQsNTAwCltGSVJFIENSQUNLTElOR10KCjM1CjAwOjAxOjQ1LDAwMCAtLT4gMDA6MDE6NDcsNTAwCkkga25vdyB3ZSBzaG91bGQuIEJ1dCBub3QgdG9uaWdodC4gVG9uaWdodCBJIGp1c3Qgd2FudCB0byBiZSBoZXJlIHdpdGggeW91LgoKMzYKMDA6MDE6NDgsMDAwIC0tPiAwMDowMTo1MCw1MDAKQWxsIHJpZ2h0LiBUb21vcnJvdywgdGhlbi4gQW5kIHRoaXMgdGltZSB5b3Ugd2lsbCB0ZWxsIG1lIGV2ZXJ5dGhpbmcuCgozNwowMDowMTo1MSwwMDAgLS0+IDAwOjAxOjUzLDUwMApJIHdpbGwuIEkgcHJvbWlzZS4gTm93LCB3aGVyZSBkaWQgeW91IHB1dCB0aGUgZ29vZCBjdXBzPwoKMzgKMDA6MDE6NTQsMDAwIC0tPiAwMDowMTo1Niw1MDAKKFNJR0hTKQo=
do_write "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).es.srt' "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/Spanish.spa.srt' 2652 1723000000 276008c37d52f6bb8849cb9a2166777ecec84704f332318542f615cf3f3f8a26 f05b4394c136ed4a344fe5c450d62d56fb8f017ce6777711b49b97f3d25511ce MQowMDowMDowMywwMDAgLS0+IDAwOjAwOjA1LDUwMArCv0TDs25kZSBlc3RhYmFzIGFub2NoZT8gVGUgZXN0dXZlIGVzcGVyYW5kby4KCjIKMDA6MDA6MDYsMDAwIC0tPiAwMDowMDowOCw1MDAKTG8gc8OpLCBsbyBzaWVudG8uIEVsIHRyZW4gbGxlZ8OzIHRhcmRlIHkgbWkgdGVsw6lmb25vIG5vIHRlbsOtYSBiYXRlcsOtYS4KCjMKMDA6MDA6MDksMDAwIC0tPiAwMDowMDoxMSw1MDAKUG9kw61hcyBoYWJlciBsbGFtYWRvIGRlc2RlIGxhIGVzdGFjacOzbi4KCjQKMDA6MDA6MTIsMDAwIC0tPiAwMDowMDoxNCw1MDAKTm8gaGFiw61hIG5hZGllIGFsbMOtLCB5IG5ldmFiYSB0YW50byBxdWUgbm8gc2UgdmXDrWEgZWwgY2FtaW5vLgoKNQowMDowMDoxNSwwMDAgLS0+IDAwOjAwOjE3LDUwMApCdWVubywgYWhvcmEgZXN0w6FzIGFxdcOtLiBQYXNhIHkgc2nDqW50YXRlIGp1bnRvIGFsIGZ1ZWdvLgoKNgowMDowMDoxOCwwMDAgLS0+IDAwOjAwOjIwLDUwMApHcmFjaWFzLiDCv1R1IG1hZHJlIHRvZGF2w61hIGVzdMOhIGRlc3BpZXJ0YT8KCjcKMDA6MDA6MjEsMDAwIC0tPiAwMDowMDoyMyw1MDAKU2UgZnVlIGEgbGEgY2FtYSBoYWNlIHVuYSBob3JhLiBNZSBkaWpvIHF1ZSB0ZSBkaWplcmEgcXVlIGVzdMOhIGNvbnRlbnRhIGRlIHF1ZSB2aW5pZXJhcy4KCjgKMDA6MDA6MjQsMDAwIC0tPiAwMDowMDoyNiw1MDAKVGVuZ28gYWxnbyBwYXJhIGVsbGEuIEVzdMOhIGVuIG1pIGJvbHNhLCBwZXJvIHB1ZWRlIGVzcGVyYXIgaGFzdGEgbWHDsWFuYS4KCjkKMDA6MDA6MjcsMDAwIC0tPiAwMDowMDoyOSw1MDAKwr9RdWllcmVzIHVuIHTDqT8gVGFtYmnDqW4gaGF5IHNvcGEsIHNpIHRpZW5lcyBoYW1icmUuCgoxMAowMDowMDozMCwwMDAgLS0+IDAwOjAwOjMyLDUwMApTb2xvIHTDqSwgcG9yIGZhdm9yLiBDb23DrSBwb3IgZWwgY2FtaW5vLCBwZXJvIG5vIGVzdGFiYSBtdXkgYnVlbm8uCgoxMQowMDowMDozMywwMDAgLS0+IDAwOjAwOjM1LDUwMApUZW5lbW9zIHF1ZSBoYWJsYXIgZGUgbG8gcXVlIHBhc8OzIGVuIGxhIGNhc2EuCgoxMgowMDowMDozNiwwMDAgLS0+IDAwOjAwOjM4LDUwMApMbyBzw6kuIFBlcm8gbm8gZXN0YSBub2NoZS4gRXN0YSBub2NoZSBzb2xvIHF1aWVybyBlc3RhciBhcXXDrSBjb250aWdvLCBzZcOxb3IgbcOtby4KCjEzCjAwOjAwOjM5LDAwMCAtLT4gMDA6MDA6NDEsNTAwCk11eSBiaWVuLiBNYcOxYW5hLCBlbnRvbmNlcy4gWSBlc3RhIHZleiBtZSBsbyB2YXMgYSBjb250YXIgdG9kby4KCjE0CjAwOjAwOjQyLDAwMCAtLT4gMDA6MDA6NDQsNTAwClRlIGxvIGNvbnRhcsOpIHRvZG8sIHRlIGxvIHByb21ldG8uIMK/RMOzbmRlIHB1c2lzdGUgbGFzIHRhemFzIGJ1ZW5hcz8KCjE1CjAwOjAwOjQ1LDAwMCAtLT4gMDA6MDA6NDcsNTAwCsK/RMOzbmRlIGVzdGFiYXMgYW5vY2hlPyBUZSBlc3R1dmUgZXNwZXJhbmRvLgoKMTYKMDA6MDA6NDgsMDAwIC0tPiAwMDowMDo1MCw1MDAKTG8gc8OpLCBsbyBzaWVudG8uIEVsIHRyZW4gbGxlZ8OzIHRhcmRlIHkgbWkgdGVsw6lmb25vIG5vIHRlbsOtYSBiYXRlcsOtYS4KCjE3CjAwOjAwOjUxLDAwMCAtLT4gMDA6MDA6NTMsNTAwClBvZMOtYXMgaGFiZXIgbGxhbWFkbyBkZXNkZSBsYSBlc3RhY2nDs24uCgoxOAowMDowMDo1NCwwMDAgLS0+IDAwOjAwOjU2LDUwMApObyBoYWLDrWEgbmFkaWUgYWxsw60sIHkgbmV2YWJhIHRhbnRvIHF1ZSBubyBzZSB2ZcOtYSBlbCBjYW1pbm8uCgoxOQowMDowMDo1NywwMDAgLS0+IDAwOjAwOjU5LDUwMApCdWVubywgYWhvcmEgZXN0w6FzIGFxdcOtLiBQYXNhIHkgc2nDqW50YXRlIGp1bnRvIGFsIGZ1ZWdvLgoKMjAKMDA6MDE6MDAsMDAwIC0tPiAwMDowMTowMiw1MDAKR3JhY2lhcy4gwr9UdSBtYWRyZSB0b2RhdsOtYSBlc3TDoSBkZXNwaWVydGE/CgoyMQowMDowMTowMywwMDAgLS0+IDAwOjAxOjA1LDUwMApTZSBmdWUgYSBsYSBjYW1hIGhhY2UgdW5hIGhvcmEuIE1lIGRpam8gcXVlIHRlIGRpamVyYSBxdWUgZXN0w6EgY29udGVudGEgZGUgcXVlIHZpbmllcmFzLgoKMjIKMDA6MDE6MDYsMDAwIC0tPiAwMDowMTowOCw1MDAKVGVuZ28gYWxnbyBwYXJhIGVsbGEuIEVzdMOhIGVuIG1pIGJvbHNhLCBwZXJvIHB1ZWRlIGVzcGVyYXIgaGFzdGEgbWHDsWFuYS4KCjIzCjAwOjAxOjA5LDAwMCAtLT4gMDA6MDE6MTEsNTAwCsK/UXVpZXJlcyB1biB0w6k/IFRhbWJpw6luIGhheSBzb3BhLCBzaSB0aWVuZXMgaGFtYnJlLgoKMjQKMDA6MDE6MTIsMDAwIC0tPiAwMDowMToxNCw1MDAKU29sbyB0w6ksIHBvciBmYXZvci4gQ29tw60gcG9yIGVsIGNhbWlubywgcGVybyBubyBlc3RhYmEgbXV5IGJ1ZW5vLgoKMjUKMDA6MDE6MTUsMDAwIC0tPiAwMDowMToxNyw1MDAKVGVuZW1vcyBxdWUgaGFibGFyIGRlIGxvIHF1ZSBwYXPDsyBlbiBsYSBjYXNhLgoKMjYKMDA6MDE6MTgsMDAwIC0tPiAwMDowMToyMCw1MDAKTG8gc8OpLiBQZXJvIG5vIGVzdGEgbm9jaGUuIEVzdGEgbm9jaGUgc29sbyBxdWllcm8gZXN0YXIgYXF1w60gY29udGlnbywgc2XDsW9yIG3DrW8uCgoyNwowMDowMToyMSwwMDAgLS0+IDAwOjAxOjIzLDUwMApNdXkgYmllbi4gTWHDsWFuYSwgZW50b25jZXMuIFkgZXN0YSB2ZXogbWUgbG8gdmFzIGEgY29udGFyIHRvZG8uCgoyOAowMDowMToyNCwwMDAgLS0+IDAwOjAxOjI2LDUwMApUZSBsbyBjb250YXLDqSB0b2RvLCB0ZSBsbyBwcm9tZXRvLiDCv0TDs25kZSBwdXNpc3RlIGxhcyB0YXphcyBidWVuYXM/Cg==
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP.mp4' "$MOVIES"/'Winter Lights (2004)/Winter Lights (2004).mp4' 1000 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English SDH.eng.srt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English SDH.eng.srt' 3215 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English.eng.srt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/English.eng.srt' 2572 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/Spanish.spa.srt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs/Spanish.spa.srt' 2652 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP.srt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP.srt' 2572 1723000000
do_move "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/readme.txt' "$SOURCE"/'.tidyshelf-trash/source/www.example.com - Winter Lights (2004) 1080p BluRay-GRP/readme.txt' 86 1723000000
do_mode "$MOVIES"/'Winter Lights (2004)'
do_mode "$MOVIES"/'Winter Lights (2004)/Subs'
do_mode "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).en.sdh.srt'
do_mode "$MOVIES"/'Winter Lights (2004)/Subs/Winter Lights (2004).es.srt'
do_mode "$MOVIES"/'Winter Lights (2004)/Winter Lights (2004).mp4'
do_rmdir "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP/Subs'
do_rmdir "$SOURCE"/'www.example.com - Winter Lights (2004) 1080p BluRay-GRP'
}

steps
if [ "$EXECUTE" = 1 ]; then
  PASS=run
  steps
  echo "applied plan e52a9b40a2d8"
else
  echo "dry-run: nothing was changed. Run with --execute to apply."
fi
