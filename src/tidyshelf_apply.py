#!/usr/bin/env python3
"""tidyshelf-apply: apply a tidyshelf plan on the machine that holds the files.

One file, Python standard library only, no network, no subprocesses. Copy it anywhere and run it.
The plan is treated as untrusted input. In short (docs/SAFETY.md has the full list):

* Dry-run unless --execute; --execute also needs a typed confirmation or --yes.
* Plans name paths as "root:relative/path"; roots come only from this machine's config. No "..",
  no absolute paths, no symlinks anywhere below a root, no special files, nothing in [exclude].
* Four operations only: mkdir, move, delete, set_mode. Moves are a single rename on one filesystem,
  never copy+delete, never onto an existing path. Deletes only under the source root, capped.
* Every operation is checked twice: in a full in-memory pre-flight of the whole plan, and again
  right before it runs (the file must still have the size and mtime the plan recorded).
* A lock prevents overlapping runs; an fsynced journal makes runs resumable and undoable.

Exit codes: 0 nothing to do / success, 1 error, 2 dry-run has changes, 3 refused, 4 needs review.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import errno
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import stat
import sys
import tomllib

VERSION = "0.1.0"
FORMAT = 1
ROOT_NAMES = ("source", "movies", "series")
TRASH_DIR = ".tidyshelf-trash"
LOCK_NAME = ".tidyshelf.lock"
SYSTEM_CONFIG = "/etc/tidyshelf.toml"
ENV_VAR = "TIDYSHELF_CONFIG"
EXIT_OK, EXIT_ERROR, EXIT_CHANGES, EXIT_REFUSED, EXIT_REVIEW = 0, 1, 2, 3, 4


class ReadError(Exception):
    """Plan, journal or file unreadable (exit 1)."""


class ConfigError(Exception):
    """Config missing or invalid (exit 1)."""


class PlanError(Exception):
    """Plan or journal does not follow the format (exit 3)."""


class Refusal(Exception):
    """A guardrail or precondition said no (exit 3)."""


# --- config ------------------------------------------------------------------------------------
# Only these sections are read; others belong to the planner and are ignored here.
# A test checks these values equal the planner's defaults.toml.

APPLY_SECTIONS = ("roots", "safety", "owner", "exclude")
APPLY_DEFAULTS = {
    "roots": {"source": "", "movies": "", "series": ""},
    "safety": {"max_delete_files": 200, "max_delete_bytes": 52428800, "max_age": "", "require_approve": False},
    "owner": {"user": "", "group": "", "dir_mode": "", "file_mode": ""},
    "exclude": {"patterns": []},
}
_DURATION = re.compile(r"(\d+)([smhd])")
_MODE = re.compile(r"0?[0-7]{3}")
_TYPE_NAMES = {bool: "boolean", int: "integer", str: "string", list: "list", dict: "table", float: "float"}


def check_roots(roots: dict) -> tuple[list[str], list[str]]:
    """Errors and warnings for a [roots] table: required, absolute, not /, no overlap or nesting.

    Overlap is decided on realpaths with commonpath, so /x/Movies2 is not inside /x/Movies.
    """
    errors: list[str] = []
    warnings: list[str] = []
    good: list[tuple[str, str]] = []
    for name in ROOT_NAMES:
        value = roots.get(name, "")
        if not isinstance(value, str):
            continue  # reported by the caller's type check
        if value == "":
            errors.append(f"roots.{name}: required")
            continue
        if not os.path.isabs(value):
            errors.append(f"roots.{name}: must be an absolute path")
            continue
        real = os.path.realpath(value)
        if real == "/":
            errors.append(f"roots.{name}: must not be /")
            continue
        if not os.path.isdir(real):
            warnings.append(f"roots.{name}: {value} does not exist")
        for other, other_real in good:
            if os.path.commonpath([real, other_real]) in (real, other_real):
                errors.append(f"roots.{name}: overlaps roots.{other}")
        good.append((name, real))
    return errors, warnings


def _read_toml(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror}") from None


def _user_config() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "tidyshelf", "config.toml")


def load_apply_config(path=None) -> dict:
    """Read [roots] [safety] [owner] [exclude] from the usual layers; strict; roots must exist."""
    cfg = {s: dict(v) for s, v in APPLY_DEFAULTS.items()}
    layers = [p for p in (str(SYSTEM_CONFIG), _user_config()) if os.path.isfile(p)]
    explicit = path if path is not None else os.environ.get(ENV_VAR) or None
    if explicit is not None:
        layers.append(os.fspath(explicit))
    errors: list[str] = []
    for layer in layers:
        data = _read_toml(layer)
        for section in APPLY_SECTIONS:
            if section not in data:
                continue
            if not isinstance(data[section], dict):
                errors.append(f"{section}: expected table, got {_TYPE_NAMES.get(type(data[section]), '?')}")
                continue
            cfg[section].update(data[section])
    for section in APPLY_SECTIONS:
        defaults = APPLY_DEFAULTS[section]
        for key, value in cfg[section].items():
            where = f"{section}.{key}"
            if key not in defaults:
                hint = difflib.get_close_matches(key, list(defaults), n=1, cutoff=0.6)
                errors.append(f"{where}: unknown key" + (f" (did you mean '{hint[0]}'?)" if hint else ""))
                continue
            want = type(defaults[key])
            if type(value) is not want:
                errors.append(f"{where}: expected {_TYPE_NAMES[want]}, got {_TYPE_NAMES.get(type(value), '?')}")
            elif want is int and value < 0:
                errors.append(f"{where}: must not be negative")
            elif want is list:
                for i, item in enumerate(value):
                    if type(item) is not str:
                        errors.append(f"{where}[{i}]: expected string")
                        continue
                    try:
                        re.compile(item)
                    except re.error as exc:
                        errors.append(f"{where}[{i}]: bad regex ({exc})")
    if errors:
        raise ConfigError("\n".join(errors))
    safety, owner = cfg["safety"], cfg["owner"]
    if safety["max_age"] and not _DURATION.fullmatch(safety["max_age"]):
        errors.append("safety.max_age: must be empty or a duration like 30m, 24h, 7d")
    for key in ("dir_mode", "file_mode"):
        if owner[key] and not _MODE.fullmatch(owner[key]):
            errors.append(f"owner.{key}: must be empty or an octal mode like 0775")
    root_errors, root_warnings = check_roots(cfg["roots"])
    errors += root_errors + root_warnings  # a missing root is fatal here
    cfg["_uid"] = cfg["_gid"] = None
    if owner["user"]:
        try:
            cfg["_uid"] = pwd.getpwnam(owner["user"]).pw_uid
        except KeyError:
            errors.append(f"owner.user: unknown user {owner['user']!r}")
    if owner["group"]:
        try:
            cfg["_gid"] = grp.getgrnam(owner["group"]).gr_gid
        except KeyError:
            errors.append(f"owner.group: unknown group {owner['group']!r}")
    if errors:
        raise ConfigError("\n".join(errors))
    cfg["_roots"] = {name: os.path.realpath(cfg["roots"][name]) for name in ROOT_NAMES}
    cfg["_exclude"] = [re.compile(p) for p in cfg["exclude"]["patterns"]]
    return cfg


# --- plan format -------------------------------------------------------------------------------

_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_KIND = re.compile(r"[a-z][a-z0-9_-]{0,31}")
STATUSES = ("ready", "needs_review", "skipped")
LEVELS = ("info", "warn", "error")
OP_FIELDS = {  # op -> (required, optional)
    "mkdir": ({"op", "path"}, set()),
    "move": ({"op", "from", "to", "size", "mtime"}, set()),
    "delete": ({"op", "path", "reason"}, {"size", "mtime", "dir"}),
    "set_mode": ({"op", "path"}, set()),
}


def _strict_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise PlanError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_float(text):
    raise PlanError(f"numbers must be whole (got {text})")


def _no_constant(text):
    raise PlanError(f"{text} is not allowed")


def parse_json(text: str, what: str = "plan") -> dict:
    try:
        doc = json.loads(text, object_pairs_hook=_strict_object, parse_float=_no_float,
                         parse_constant=_no_constant)
    except json.JSONDecodeError as exc:
        raise ReadError(f"{what} is not valid JSON: {exc}") from None
    except (ValueError, RecursionError) as exc:  # e.g. a 5000-digit number, or nesting too deep
        raise PlanError(f"{what} is not acceptable JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise PlanError(f"{what} must be a JSON object")
    return doc


def parse_plan(text: str) -> dict:
    return parse_json(text, "plan")


def split_ref(ref) -> tuple[str, str]:
    """Check a "root:relative/path" reference's syntax and split it."""
    if not isinstance(ref, str):
        raise PlanError("path must be a string")
    if "\x00" in ref:
        raise PlanError(f"{ref!r}: contains NUL")
    try:
        os.fsencode(ref)
    except UnicodeError:
        raise PlanError(f"{ref!r}: not a valid file name (bad surrogate)") from None
    if ":" not in ref:
        raise PlanError(f"{ref!r}: not a root:path reference")
    root, rel = ref.split(":", 1)
    if root not in ROOT_NAMES:
        raise PlanError(f"{ref!r}: unknown root {root!r}")
    if rel == "":
        raise PlanError(f"{ref!r}: empty path (a root itself is never touched)")
    if rel.startswith("/"):
        raise PlanError(f"{ref!r}: absolute path")
    for part in rel.split("/"):
        if part == "":
            raise PlanError(f"{ref!r}: empty segment")
        if part in (".", ".."):
            raise PlanError(f"{ref!r}: {part!r} segment")
    return root, rel


def _fields(obj, required: set, optional: set, where: str, errors: list) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be an object")
        return False
    for key in sorted(set(obj) - required - optional):
        errors.append(f"{where}: unknown field {key!r}")
    for key in sorted(required - set(obj)):
        errors.append(f"{where}: missing field {key!r}")
    return set(obj) <= required | optional and required <= set(obj)


def _whole(value) -> bool:
    return type(value) is int and value >= 0


def _in_trash(root: str, rel: str) -> bool:
    return root == "source" and (rel == TRASH_DIR or rel.startswith(TRASH_DIR + "/"))


def _check_op(op, where: str, errors: list) -> None:
    if not isinstance(op, dict) or not isinstance(op.get("op"), str) or op["op"] not in OP_FIELDS:
        errors.append(f"{where}: unknown op {op.get('op') if isinstance(op, dict) else op!r}")
        return
    kind = op["op"]
    if not _fields(op, *OP_FIELDS[kind], where, errors):
        return
    for key in ("size", "mtime"):
        if key in op and not _whole(op[key]):
            errors.append(f"{where}: {key!r} must be a non-negative integer")
    if kind == "delete":
        if "dir" in op:
            if op["dir"] is not True:
                errors.append(f"{where}: 'dir' must be true (omit it for a file)")
            for key in ("size", "mtime"):
                if key in op:
                    errors.append(f"{where}: unknown field {key!r} (a directory delete has no {key})")
        else:
            for key in ("size", "mtime"):
                if key not in op:
                    errors.append(f"{where}: missing field {key!r}")
        if not isinstance(op["reason"], str) or not op["reason"]:
            errors.append(f"{where}: 'reason' must be a non-empty string")
    refs = {}
    for key in ("path", "from", "to"):
        if key in op:
            if not isinstance(op[key], str):
                errors.append(f"{where}: {key!r} must be a string")
                continue
            try:
                refs[key] = split_ref(op[key])
            except PlanError as exc:
                errors.append(f"{where}: {exc}")
    if kind == "move" and len(refs) == 2:
        (src_root, src_rel), (dst_root, dst_rel) = refs["from"], refs["to"]
        if _in_trash(src_root, src_rel):
            errors.append(f"{where}: cannot move out of {TRASH_DIR}/")
        if dst_root == "source" and src_root != "source" and not _in_trash(dst_root, dst_rel):
            errors.append(f"{where}: moves into source are only allowed from source or into {TRASH_DIR}/")
    if kind == "delete" and "path" in refs:
        root, rel = refs["path"]
        if root != "source":
            errors.append(f"{where}: may delete only under source")
        elif _in_trash(root, rel):
            errors.append(f"{where}: cannot delete inside {TRASH_DIR}/")


def validate_plan(doc: dict) -> None:
    """Raise PlanError listing every problem; return None if the plan is well formed."""
    errors: list[str] = []
    if not _fields(doc, {"format", "items"}, {"tool", "created"}, "plan", errors) and "format" not in doc:
        raise PlanError("\n".join(errors))
    if "format" in doc and not (type(doc["format"]) is int and doc["format"] == FORMAT):
        errors.append(f"plan: unsupported format {doc['format']!r} (this tool reads format {FORMAT})")
    for key in ("tool", "created"):
        if key in doc and not isinstance(doc[key], str):
            errors.append(f"plan: {key!r} must be a string")
    items = doc.get("items", [])
    if not isinstance(items, list):
        raise PlanError("\n".join(errors + ["plan: 'items' must be a list"]))
    seen_ids = set()
    for i, item in enumerate(items):
        where = f"item {i}"
        if not _fields(item, {"id", "kind", "status", "ops"}, {"source", "findings"}, where, errors):
            continue
        if not isinstance(item["id"], str) or not _ITEM_ID.fullmatch(item["id"]):
            errors.append(f"{where}: bad id {item['id']!r} (letters, digits, . _ -)")
            continue
        where = f"item {item['id']!r}"
        if item["id"] in seen_ids:
            errors.append(f"duplicate item id {item['id']!r}")
        seen_ids.add(item["id"])
        if not isinstance(item["kind"], str) or not _KIND.fullmatch(item["kind"]):
            errors.append(f"{where}: bad kind {item['kind']!r} (a lowercase word)")
        if item["status"] not in STATUSES:
            errors.append(f"{where}: status must be one of {', '.join(STATUSES)}")
        if "source" in item:
            try:
                split_ref(item["source"])
            except PlanError as exc:
                errors.append(f"{where}: source {exc}")
        findings = item.get("findings", [])
        if not isinstance(findings, list):
            errors.append(f"{where}: 'findings' must be a list")
            findings = []
        for j, f in enumerate(findings):
            fw = f"{where} finding {j}"
            if _fields(f, {"level", "code", "msg"}, set(), fw, errors):
                if not all(isinstance(f[k], str) for k in f) or f["level"] not in LEVELS:
                    errors.append(f"{fw}: level must be one of {', '.join(LEVELS)}; code and msg strings")
        ops = item["ops"]
        if not isinstance(ops, list):
            errors.append(f"{where}: 'ops' must be a list")
            continue
        if item["status"] != "ready" and ops:
            errors.append(f"{where}: a {item['status']} item must have no ops")
        deleting = False
        for n, op in enumerate(ops):
            _check_op(op, f"{where} op {n}", errors)
            is_delete = isinstance(op, dict) and op.get("op") == "delete"
            if deleting and not is_delete:
                errors.append(f"{where} op {n}: deletes must come last in an item")
            deleting = deleting or is_delete
    if not errors:
        _check_whole_plan(items, errors)
    if errors:
        raise PlanError("\n".join(errors))


def _check_whole_plan(items: list, errors: list) -> None:
    """No path is the source of two ops, no two ops share a destination, no chains or swaps."""
    sources: dict[str, int] = {}
    move_dests: dict[str, int] = {}
    mkdirs: set[str] = set()
    for item in items:
        for op in item["ops"]:
            if op["op"] == "move":
                sources[op["from"]] = sources.get(op["from"], 0) + 1
                move_dests[op["to"]] = move_dests.get(op["to"], 0) + 1
            elif op["op"] == "delete":
                sources[op["path"]] = sources.get(op["path"], 0) + 1
            elif op["op"] == "mkdir":
                mkdirs.add(op["path"])
    for ref, count in sorted(sources.items()):
        if count > 1:
            errors.append(f"{ref}: is the source of more than one op")
    for ref, count in sorted(move_dests.items()):
        if count > 1 or ref in mkdirs:
            errors.append(f"{ref}: is the destination of more than one op")
    for ref in sorted((set(move_dests) | mkdirs) & set(sources)):
        errors.append(f"{ref}: is the destination of one op and the source of another op")


def plan_hash(doc: dict) -> str:
    """sha256 (hex) over canonical JSON of [id, status, ops] per item."""
    canon = [[item["id"], item["status"], item["ops"]] for item in doc["items"]]
    data = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(data.encode("ascii")).hexdigest()


def plan_id(doc: dict) -> str:
    """The short plan id: the first 12 hex chars of plan_hash()."""
    return plan_hash(doc)[:12]


# --- path guard ---------------------------------------------------------------------------------

class Guard:
    """Turns a plan reference into a real path, or refuses."""

    def __init__(self, cfg: dict, protected: list[str]):
        self.roots = cfg["_roots"]
        self.exclude = cfg["_exclude"]
        self.protected = {os.path.realpath(p) for p in protected}

    def path(self, ref: str) -> str:
        root, rel = split_ref(ref)
        for pattern in self.exclude:
            if pattern.search(ref):
                raise Refusal(f"{ref}: excluded by [exclude] pattern {pattern.pattern!r}")
        base = self.roots[root]
        full = os.path.join(base, *rel.split("/"))
        parent = os.path.dirname(full)
        # No symlink anywhere between the root and the final name, so the path cannot escape.
        if os.path.realpath(parent) != parent or os.path.commonpath([base, full]) != base:
            raise Refusal(f"{ref}: a parent folder is a symlink (or leaves the root)")
        if full in self.protected or os.path.basename(full) == LOCK_NAME and parent == self.roots["source"]:
            raise Refusal(f"{ref}: protected file (lock, journal or plan)")
        return full

    def ref(self, root: str, full: str) -> str:
        return f"{root}:{os.path.relpath(full, self.roots[root])}"

    def root_of(self, full: str) -> str:
        for name, base in self.roots.items():
            if os.path.commonpath([base, full]) == base and full != base:
                return name
        raise Refusal(f"{full}: not under a root")


_DIR_NOFOLLOW = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class At:
    """A path's parent folder, opened from its root one level at a time without following
    symlinks. Every change is made relative to it, so a folder swapped for a symlink after the
    checks cannot redirect the change outside the root."""

    def __init__(self, guard: Guard, full: str):
        root = guard.root_of(full)
        self.ref = guard.ref(root, full)
        parts = os.path.relpath(full, guard.roots[root]).split(os.sep)
        self.name = parts[-1]
        self.fd = os.open(guard.roots[root], os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for part in parts[:-1]:
                fd = os.open(part, _DIR_NOFOLLOW, dir_fd=self.fd)
                os.close(self.fd)
                self.fd = fd
        except OSError as exc:
            os.close(self.fd)
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise Refusal(f"{self.ref}: a parent folder is now a symlink (never followed)") from None
            raise

    def __enter__(self) -> "At":
        return self

    def __exit__(self, *exc) -> None:
        os.close(self.fd)

    def lstat(self):
        try:
            return os.stat(self.name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def check_file(self, op: dict) -> None:
        """Right before the change: still a regular file with the planned size and mtime."""
        st = self.lstat()
        if st is None or not stat.S_ISREG(st.st_mode):
            raise Refusal(f"{self.ref}: no longer a regular file")
        if (st.st_size, st.st_mtime_ns // 10**9) != (op["size"], op["mtime"]):
            raise Refusal(f"{self.ref}: changed since it was checked")


def _rename(guard: Guard, src: str, dst: str) -> None:
    with At(guard, src) as a, At(guard, dst) as b:
        if b.lstat() is not None:
            raise Refusal(f"{b.ref}: destination exists (never overwritten)")
        os.rename(a.name, b.name, src_dir_fd=a.fd, dst_dir_fd=b.fd)  # see SAFETY.md for the tiny race


def _rmdir(guard: Guard, path: str) -> None:
    with At(guard, path) as at:
        os.rmdir(at.name, dir_fd=at.fd)


# --- filesystem view ----------------------------------------------------------------------------

class View:
    """The filesystem as the plan sees it. In dry mode, changes are only recorded in memory, so a
    whole plan can be checked before anything happens (an emptied folder really is empty later)."""

    def __init__(self, dry: bool, added=None, removed=None):
        self.dry = dry
        self.added = dict(added or {})  # path -> (kind, size, mtime, dev)
        self.removed = set(removed or ())

    def copy(self) -> "View":
        return View(self.dry, self.added, self.removed)

    def adopt(self, other: "View") -> None:
        self.added, self.removed = other.added, other.removed

    def info(self, path: str):
        if path in self.added:
            return self.added[path]
        if path in self.removed:
            return None
        try:
            st = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            return None
        mode = st.st_mode
        kind = ("symlink" if stat.S_ISLNK(mode) else "dir" if stat.S_ISDIR(mode)
                else "file" if stat.S_ISREG(mode) else "other")
        return (kind, st.st_size, st.st_mtime_ns // 10**9, st.st_dev)

    def is_empty_dir(self, path: str) -> bool:
        names: set[str] = set()
        if path not in self.added:
            try:
                names = set(os.listdir(path))
            except OSError:
                names = set()
        names = {n for n in names if os.path.join(path, n) not in self.removed}
        names |= {os.path.basename(p) for p in self.added if os.path.dirname(p) == path}
        return not names

    def dev_near(self, path: str) -> int:
        """Device of the path, or of its nearest existing ancestor."""
        while (found := self.info(path)) is None:
            path = os.path.dirname(path)
        return found[3]

    def add(self, path: str, info) -> None:
        self.removed.discard(path)
        self.added[path] = info

    def remove(self, path: str) -> None:
        self.added.pop(path, None)
        self.removed.add(path)


def _check_file(info, ref: str, op: dict) -> None:
    kind, size, mtime, _dev = info
    if kind == "symlink":
        raise Refusal(f"{ref}: is a symlink (never followed)")
    if kind != "file":
        raise Refusal(f"{ref}: not a regular file")
    if (size, mtime) != (op["size"], op["mtime"]):
        raise Refusal(f"{ref}: changed since the plan was made "
                      f"(size {op['size']} -> {size}, mtime {op['mtime']} -> {mtime})")


# --- lock and journal ---------------------------------------------------------------------------

def _open_append(path: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o644)
    return os.fdopen(fd, "a", encoding="ascii")


class Lock:
    def __init__(self, path: str):
        self.path = path

    def __enter__(self):
        try:
            self.fh = _open_append(self.path)
        except OSError as exc:
            raise Refusal(f"cannot open lock file {self.path}: {exc.strerror}") from None
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise Refusal("another tidyshelf-apply run holds the lock in the source root") from None
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


class Journal:
    """Append-only JSONL, one fsync per line, "root:rel" references only."""

    def __init__(self, path: str, header: dict):
        self.path, self.header = path, header

    def __enter__(self):
        if _check_journal_file(self.path):
            first = _read_journal(self.path)[0]
            if first.get("plan_id") != self.header["plan_id"]:
                raise Refusal(f"{self.path} belongs to another plan ({first.get('plan_id')}); move it away")
        try:
            self.fh = _open_append(self.path)
        except OSError as exc:
            raise Refusal(f"cannot write journal {self.path}: {exc.strerror}") from None
        self.log(self.header)
        return self

    def log(self, entry: dict) -> None:
        self.fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())

    def __exit__(self, *exc):
        self.fh.close()


def _check_journal_file(path: str) -> bool:
    """True if a journal exists; refuse one that this user did not write (it could be planted)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode):
        raise Refusal(f"{path}: journal is not a regular file")
    if st.st_uid != os.geteuid():
        raise Refusal(f"{path}: journal belongs to another user; move it away")
    if st.st_mode & 0o022:
        raise Refusal(f"{path}: journal is writable by others; move it away")
    return True


def _read_journal(path: str) -> list[dict]:
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReadError(f"{path}: cannot read journal ({exc})") from None
    entries = [parse_json(line, "journal line") for line in text.splitlines() if line.strip()]
    if not entries or entries[0].get("t") != "header" or not isinstance(entries[0].get("plan_id"), str):
        raise PlanError(f"{path}: not a tidyshelf journal")
    return entries


# --- executor -----------------------------------------------------------------------------------

class Ctx:
    """Per-item bookkeeping: what this item did, so it can be rolled back."""

    def __init__(self, item_id: str):
        self.id, self.n = item_id, 0
        self.moves: list[dict] = []
        self.created: list[tuple[str, str]] = []
        self.del_files = self.del_bytes = 0


class Executor:
    def __init__(self, cfg: dict, guard: Guard, out: "Out"):
        self.guard, self.out = guard, out
        owner = cfg["owner"]
        self.dir_mode = int(owner["dir_mode"], 8) if owner["dir_mode"] else None
        self.file_mode = int(owner["file_mode"], 8) if owner["file_mode"] else None
        self.uid, self.gid = cfg["_uid"], cfg["_gid"]
        self.journal: Journal | None = None
        self.warned_owner = False
        self.prefix = ""  # "undo_" while undoing, so a later undo does not read these as plan moves
        self.io_error = False  # an OSError (not a refusal) happened: exit 1

    def log(self, entry: dict) -> None:
        if self.journal is not None:
            self.journal.log(dict(entry, t=self.prefix + entry["t"]))

    def run_op(self, view: View, op: dict, ctx: Ctx) -> str:
        """Check one op against the view and (in real mode) perform it. Returns 'done' if the
        filesystem already shows it done, else 'change'. Raises Refusal / OSError."""
        try:
            return getattr(self, "op_" + op["op"])(view, op, ctx)
        except OSError as exc:  # changes use names relative to an open folder: report plan refs
            where = op["path"] if "path" in op else f"{op['from']} -> {op['to']}"
            raise OSError(exc.errno, f"{where}: {exc.strerror}") from None

    def op_mkdir(self, view: View, op: dict, ctx: Ctx) -> str:
        ref = op["path"]
        root = ref.split(":", 1)[0]
        full = self.guard.path(ref)
        found = view.info(full)
        if found is not None:
            if found[0] != "dir":
                raise Refusal(f"{ref}: exists and is not a directory")
            return "done"
        missing, probe = [], full
        while (found := view.info(probe)) is None:
            missing.append(probe)
            probe = os.path.dirname(probe)
        if found[0] != "dir":
            raise Refusal(f"{self.guard.ref(root, probe)}: not a directory")
        for level in reversed(missing):
            level_ref = self.guard.ref(root, level)
            self.guard.path(level_ref)
            if view.dry:
                view.add(level, ("dir", 0, 0, found[3]))
            else:
                self.log({"t": "mkdir", "item": ctx.id, "path": level_ref})
                with At(self.guard, level) as at:
                    os.mkdir(at.name, dir_fd=at.fd)
                ctx.created.append((level, level_ref))
        return "change"

    def op_move(self, view: View, op: dict, ctx: Ctx) -> str:
        src_ref, dst_ref = op["from"], op["to"]
        src, dst = self.guard.path(src_ref), self.guard.path(dst_ref)
        s, d = view.info(src), view.info(dst)
        if s is None:
            if d is not None and d[0] == "file" and d[1:3] == (op["size"], op["mtime"]):
                return "done"
            raise Refusal(f"{src_ref}: source file missing")
        _check_file(s, src_ref, op)
        if d is not None:
            raise Refusal(f"{dst_ref}: destination exists (never overwritten)")
        parent = view.info(os.path.dirname(dst))
        if parent is None or parent[0] != "dir":
            raise Refusal(f"{dst_ref}: destination parent folder missing")
        if view.dev_near(os.path.dirname(dst)) != s[3]:
            raise Refusal(f"{src_ref} -> {dst_ref}: different filesystem (moves never copy)")
        if view.dry:
            view.remove(src)
            view.add(dst, s)
            return "change"
        self.log({"t": "begin", "item": ctx.id, "n": ctx.n, "op": op})
        try:
            with At(self.guard, src) as at:
                at.check_file(op)
            _rename(self.guard, src, dst)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise Refusal(f"{src_ref} -> {dst_ref}: different filesystem (moves never copy)") from None
            raise
        ctx.moves.append(op)
        self.log({"t": "done", "item": ctx.id, "n": ctx.n, "op": op})
        return "change"

    def op_delete(self, view: View, op: dict, ctx: Ctx) -> str:
        ref = op["path"]
        full = self.guard.path(ref)
        found = view.info(full)
        if found is None:
            return "done"
        if op.get("dir"):
            if found[0] == "symlink":
                raise Refusal(f"{ref}: is a symlink (never followed)")
            if found[0] != "dir":
                raise Refusal(f"{ref}: not a directory")
            if not view.is_empty_dir(full):
                raise Refusal(f"{ref}: directory not empty")
        else:
            _check_file(found, ref, op)
            ctx.del_files += 1
            ctx.del_bytes += op["size"]
        if view.dry:
            view.remove(full)
            return "change"
        self.log({"t": "begin", "item": ctx.id, "n": ctx.n, "op": op})
        with At(self.guard, full) as at:
            if op.get("dir"):
                os.rmdir(at.name, dir_fd=at.fd)
            else:
                at.check_file(op)
                os.unlink(at.name, dir_fd=at.fd)
        self.log({"t": "done", "item": ctx.id, "n": ctx.n, "op": op})
        return "change"

    def op_set_mode(self, view: View, op: dict, ctx: Ctx) -> str:
        ref = op["path"]
        full = self.guard.path(ref)
        found = view.info(full)
        if found is None:
            raise Refusal(f"{ref}: missing")
        if found[0] not in ("file", "dir"):
            raise Refusal(f"{ref}: symlink or special file")
        mode = self.dir_mode if found[0] == "dir" else self.file_mode
        if mode is None and self.uid is None and self.gid is None:
            return "done"
        if view.dry:
            if full in view.added:
                return "change"
            st = os.lstat(full)
            same = ((mode is None or stat.S_IMODE(st.st_mode) == mode)
                    and self.uid in (None, st.st_uid) and self.gid in (None, st.st_gid))
            return "done" if same else "change"
        self.log({"t": "begin", "item": ctx.id, "n": ctx.n, "op": op})
        with At(self.guard, full) as at:
            try:  # O_NONBLOCK: a FIFO swapped in cannot hang the open
                fd = os.open(at.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=at.fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise Refusal(f"{ref}: is now a symlink (never followed)") from None
                raise
        try:
            st = os.fstat(fd)
            if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise Refusal(f"{ref}: symlink or special file")
            mode = self.dir_mode if stat.S_ISDIR(st.st_mode) else self.file_mode
            if mode is not None:
                os.fchmod(fd, mode)
            if self.uid is not None or self.gid is not None:
                if os.geteuid() != 0:
                    if not self.warned_owner:
                        self.out.warn("not privileged: ownership left unchanged (modes were applied)")
                        self.warned_owner = True
                else:
                    os.fchown(fd, -1 if self.uid is None else self.uid, -1 if self.gid is None else self.gid)
        finally:
            os.close(fd)
        self.log({"t": "done", "item": ctx.id, "n": ctx.n, "op": op})
        return "change"

    def preflight(self, items: list, view: View) -> dict:
        """Check every item against an in-memory view; items that pass change the view for later ones."""
        results = {}
        for item in items:
            ctx, fork, states, error = Ctx(item["id"]), view.copy(), [], None
            try:
                for op in item["ops"]:
                    states.append(self.run_op(fork, op, ctx))
            except (Refusal, PlanError, OSError) as exc:
                error = str(exc)
                self.io_error = self.io_error or isinstance(exc, OSError)
            else:
                view.adopt(fork)
            states += ["?"] * (len(item["ops"]) - len(states))
            results[item["id"]] = {"states": states, "error": error, "ctx": ctx}
        return results

    def execute_item(self, item: dict, states: list) -> tuple[str, str]:
        """Run one item for real. Before its first delete a failure rolls it back; after, it is partial."""
        ctx, real, deleting = Ctx(item["id"]), View(dry=False), False
        try:
            for n, op in enumerate(item["ops"]):
                if states[n] == "done":
                    continue
                ctx.n = n
                deleting = deleting or op["op"] == "delete"
                self.run_op(real, op, ctx)
        except (Refusal, PlanError, OSError) as exc:
            self.io_error = self.io_error or isinstance(exc, OSError)
            if deleting:
                self.log({"t": "item", "item": ctx.id, "result": "partial"})
                return "partial", f"{exc}; item partial (deletes had started, nothing rolled back)"
            problems = self.rollback(ctx, real)
            self.log({"t": "item", "item": ctx.id, "result": "rolled_back" if not problems else "failed"})
            if problems:
                return "failed", f"{exc}; " + "; ".join(problems)
            return "rolled_back", f"{exc}; item rolled back"
        self.log({"t": "item", "item": ctx.id, "result": "ok"})
        return "ok", ""

    def rollback(self, ctx: Ctx, real: View) -> list[str]:
        problems = []
        for op in reversed(ctx.moves):
            try:
                src, dst = self.guard.path(op["from"]), self.guard.path(op["to"])
                d = real.info(dst)
                if real.info(src) is None and d and d[0] == "file" and d[1:3] == (op["size"], op["mtime"]):
                    _rename(self.guard, dst, src)
                    self.log({"t": "rollback", "item": ctx.id, "op": op, "result": "ok"})
                    continue
                problems.append(f"{op['to']} not rolled back (changed since it was moved)")
            except (Refusal, PlanError, OSError) as exc:
                problems.append(f"{op['to']} not rolled back ({exc})")
            self.log({"t": "rollback", "item": ctx.id, "op": op, "result": "skipped"})
        for path, ref in reversed(ctx.created):
            found = real.info(path)
            if found and found[0] == "dir" and real.is_empty_dir(path):
                try:
                    _rmdir(self.guard, path)
                    self.log({"t": "rollback", "item": ctx.id, "rmdir": ref, "result": "ok"})
                except (Refusal, OSError) as exc:
                    problems.append(f"{ref} not removed ({getattr(exc, 'strerror', None) or exc})")
        return problems


# --- output -------------------------------------------------------------------------------------

class Out:
    def __init__(self, args):
        self.json, self.quiet = args.json, args.quiet

    def info(self, msg: str = "") -> None:
        if not self.quiet and not self.json:
            print(msg)

    def warn(self, msg: str) -> None:
        if not self.quiet:
            print(f"warning: {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        print(f"error: {msg}", file=sys.stderr)

    def doc(self, doc: dict) -> None:
        if self.json:
            print(json.dumps(doc, indent=2, ensure_ascii=True))


def _describe(op: dict) -> str:
    if op["op"] == "move":
        return f"move     {op['from']}\n             -> {op['to']}"
    if op["op"] == "delete":
        return f"delete   {op['path']}{'/' if op.get('dir') else ''}  ({op['reason']})"
    return f"{op['op']:<8} {op['path']}"


def _print_plan(out: Out, items: list, pre: dict, pid: str, mode: str) -> None:
    counts = {s: sum(1 for it in items if it["status"] == s) for s in STATUSES}
    out.info(f"plan {pid}: {len(items)} items ({counts['ready']} ready, {counts['needs_review']} "
             f"needs review, {counts['skipped']} skipped), {mode}")
    for item in items:
        res = pre.get(item["id"])
        out.info(f"\n{item['id']}  {item['status']}" + ("  REFUSED" if res and res["error"] else ""))
        for f in item.get("findings", []):
            out.info(f"  {f['level']}: {f['code']}: {f['msg']}")
        for n, op in enumerate(item["ops"]):
            state = res["states"][n] if res else "?"
            out.info(f"  {_describe(op)}" + ("  [done]" if state == "done" else ""))


# --- commands -----------------------------------------------------------------------------------

def _isatty() -> bool:
    return sys.stdin.isatty()


def _confirm(args, pid: str, from_stdin: bool, what: str = "execute") -> None:
    if args.yes:
        return
    if from_stdin:
        raise Refusal("a plan read from stdin needs --yes (and ideally --approve PLAN_ID)")
    if not _isatty():
        raise Refusal("not running in a terminal: pass --yes (and ideally --approve PLAN_ID)")
    if input(f"Type the plan id to {what}: ").strip() != pid:
        raise Refusal("confirmation did not match the plan id; nothing was changed")


def _seconds(duration: str) -> int:
    m = _DURATION.fullmatch(duration)
    if not m:
        raise ConfigError(f"--max-age: must be a duration like 30m, 24h, 7d (got {duration!r})")
    return int(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]


FUTURE_SLACK = 300  # seconds of clock difference allowed between the two machines


def _check_age(doc: dict, max_age: str) -> None:
    limit = _seconds(max_age)
    when = None
    if isinstance(doc.get("created"), str):
        try:
            when = datetime.datetime.fromisoformat(doc["created"].replace("Z", "+00:00"))
        except ValueError:
            when = None
    if when is None or when.tzinfo is None:
        raise Refusal("plan has no valid 'created' time, so its age cannot be checked (max_age)")
    age = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds()
    if age < -FUTURE_SLACK:
        raise Refusal("plan 'created' time is in the future, so its age cannot be trusted (max_age)")
    if age > limit:
        raise Refusal(f"plan is {int(age // 60)} minutes old, older than max_age {max_age}")


def _read_plan(arg: str):
    if arg == "-":
        data = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode()
        path, mtime = None, None
    else:
        try:
            with open(arg, "rb") as fh:
                data = fh.read()
                mtime = os.fstat(fh.fileno()).st_mtime_ns // 10**9
        except OSError as exc:
            raise ReadError(f"{arg}: {exc.strerror}") from None
        path = os.path.realpath(arg)
    try:
        return data.decode("utf-8"), path, len(data), mtime
    except UnicodeDecodeError:
        raise ReadError("plan is not UTF-8") from None


def _journal_path(plan_path: str | None, pid: str) -> str:
    if plan_path is None:
        return os.path.abspath(f"tidyshelf-{pid}.journal.jsonl")
    base = os.path.basename(plan_path)
    base = base[:-5] if base.endswith(".json") else base
    return os.path.join(os.path.dirname(plan_path), base + ".journal.jsonl")


def _select(items: list, only, skip) -> list:
    ids = {it["id"] for it in items}
    unknown = [i for i in (only or []) + (skip or []) if i not in ids]
    if unknown:
        raise Refusal(f"unknown item id(s): {', '.join(unknown)}")
    return [it for it in items if (not only or it["id"] in only) and it["id"] not in (skip or [])]


def _load_plan(text: str, args) -> tuple[dict, str, str]:
    doc = parse_plan(text)
    try:
        validate_plan(doc)
    except (TypeError, ValueError, RecursionError) as exc:  # belt and braces for hostile input
        raise PlanError(f"plan is malformed: {exc}") from None
    full_hash = plan_hash(doc)
    if args.approve is not None and not (len(args.approve) >= 12 and full_hash.startswith(args.approve)):
        raise Refusal(f"plan id {full_hash[:12]} does not match --approve {args.approve}")
    return doc, full_hash[:12], full_hash


def cmd_apply(args, cfg: dict, out: Out) -> int:
    if args.plan is None:
        raise ReadError("no plan given (use - to read it from stdin)")
    text, plan_path, plan_size, plan_mtime = _read_plan(args.plan)
    doc, pid, full_hash = _load_plan(text, args)
    if args.execute and cfg["safety"]["require_approve"] and args.approve is None:
        raise Refusal("config requires --approve PLAN_ID with --execute")
    limits = [m for m in (args.max_age, cfg["safety"]["max_age"]) if m]
    if limits:
        _check_age(doc, min(limits, key=_seconds))  # the stricter of --max-age and the config
    items = _select(doc["items"], args.only, args.skip)
    journal_path = _journal_path(plan_path, pid)
    lock_path = os.path.join(cfg["_roots"]["source"], LOCK_NAME)
    guard = Guard(cfg, [lock_path, journal_path] + ([plan_path] if plan_path else []))
    ex = Executor(cfg, guard, out)
    ready = [it for it in items if it["status"] == "ready"]
    review = [it for it in items if it["status"] == "needs_review"]
    pre = ex.preflight(ready, View(dry=True))
    errors = [f"item {i}: {r['error']}" for i, r in pre.items() if r["error"]]
    good = [it for it in ready if not pre[it["id"]]["error"]]
    files = sum(pre[it["id"]]["ctx"].del_files for it in good)
    nbytes = sum(pre[it["id"]]["ctx"].del_bytes for it in good)
    safety = cfg["safety"]
    capped = not args.allow_large_delete and (files > safety["max_delete_files"]
                                              or nbytes > safety["max_delete_bytes"])
    if capped:
        errors.append(f"delete cap exceeded: {files} files / {nbytes} bytes (limits {safety['max_delete_files']} "
                      f"files / {safety['max_delete_bytes']} bytes); use --allow-large-delete")
    todo = [it for it in good if "change" in pre[it["id"]]["states"]]
    mode = "execute" if args.execute else "dry-run"
    _print_plan(out, items, pre, pid, mode)
    results = {it["id"]: ("failed" if pre[it["id"]]["error"] else "todo" if it in todo else "done")
               for it in ready}
    if args.execute and todo and not (errors and (args.on_error == "stop" or capped)):
        _confirm(args, pid, plan_path is None)
        header = {"t": "header", "plan_id": pid, "plan_size": plan_size, "plan_mtime": plan_mtime,
                  "started": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "tool": f"tidyshelf-apply {VERSION}"}
        with Lock(lock_path), Journal(journal_path, header) as journal:
            ex.journal = journal
            for item in todo:
                result, msg = ex.execute_item(item, pre[item["id"]]["states"])
                results[item["id"]] = result
                if result != "ok":
                    errors.append(f"item {item['id']}: {msg}")
                    if args.on_error == "stop":
                        break
    elif args.execute and errors:
        errors.append("nothing was changed")
    for msg in errors:
        out.error(msg)
    code = (EXIT_ERROR if ex.io_error else EXIT_REFUSED if errors else EXIT_REVIEW if review
            else EXIT_CHANGES if todo and not args.execute else EXIT_OK)
    out.info()
    if not args.execute:
        out.info(f"dry-run: nothing was changed. Plan id {pid} (sha256 {full_hash}).")
        out.info("To apply: tidyshelf-apply PLAN --execute (--approve takes the id or a longer prefix)")
    elif not errors:
        out.info(f"applied plan {pid}: {sum(1 for r in results.values() if r == 'ok')} items changed")
    out.doc({"plan_id": pid, "plan_hash": full_hash, "mode": mode, "exit": code, "errors": errors,
             "items": [{"id": it["id"], "status": it["status"], "result": results.get(it["id"], it["status"]),
                        "ops": [dict(op, state=pre[it["id"]]["states"][n]) if it["id"] in pre else op
                                for n, op in enumerate(it["ops"])]} for it in items]})
    return code


def _undo_records(doc: dict, entries: list) -> tuple[list[dict], list[str]]:
    """The moves and created folders in a journal, each checked against the plan: a journal line
    for an op the plan does not have is refused, so a planted journal cannot steer undo."""
    ops = {(it["id"], n): op for it in doc["items"] for n, op in enumerate(it["ops"])}
    allowed_dirs: dict[str, set] = {}
    for it in doc["items"]:
        for op in it["ops"]:
            if op["op"] == "mkdir":
                root, rel = split_ref(op["path"])
                parts = rel.split("/")
                allowed_dirs.setdefault(it["id"], set()).update(
                    f"{root}:{'/'.join(parts[:i])}" for i in range(1, len(parts) + 1))
    moves: dict[tuple, dict] = {}
    created: list[str] = []
    for e in entries[1:]:
        t, item = e.get("t"), e.get("item")
        if t in ("begin", "done", "mkdir") and not isinstance(item, str):
            raise PlanError("journal: a line has no valid 'item'")
        if t in ("begin", "done"):
            op = e.get("op")
            if type(e.get("n")) is not int or not isinstance(op, dict):
                raise PlanError("journal: a begin/done line has no valid 'n' or 'op'")
            if op.get("op") != "move":
                continue
            problems: list[str] = []
            _check_op(op, "journal", problems)
            if problems:
                raise PlanError("\n".join(problems))
            if ops.get((item, e["n"])) != op:
                raise Refusal(f"journal move {op['from']} -> {op['to']} is not in the plan")
            moves[(item, e["n"])] = op
        elif t == "mkdir":
            split_ref(e.get("path"))
            if e["path"] not in allowed_dirs.get(item, ()):
                raise Refusal(f"journal mkdir {e['path']} is not in the plan")
            if e["path"] not in created:
                created.append(e["path"])
    return list(moves.values()), created


def cmd_undo(args, cfg: dict, out: Out) -> int:
    """Reverse the moves a run of this plan recorded in its journal. Deletes are never undone."""
    if args.plan is None:
        raise ReadError("--undo needs the plan (its journal is found next to it)")
    if args.plan.endswith(".journal.jsonl"):
        raise ReadError("--undo takes the plan file, not the journal (the journal is found next to it)")
    text, plan_path, _size, _mtime = _read_plan(args.plan)
    doc, pid, _hash = _load_plan(text, args)
    journal_path = _journal_path(plan_path, pid)
    if not _check_journal_file(journal_path):
        raise ReadError(f"{journal_path}: no journal for this plan")
    entries = _read_journal(journal_path)
    if entries[0]["plan_id"] != pid:
        raise Refusal(f"{journal_path} belongs to another plan ({entries[0]['plan_id']})")
    moves, created = _undo_records(doc, entries)
    lock_path = os.path.join(cfg["_roots"]["source"], LOCK_NAME)
    guard = Guard(cfg, [lock_path, journal_path] + ([plan_path] if plan_path else []))
    view, todo, problems = View(dry=True), [], []
    for op in reversed(moves):
        src, dst = guard.path(op["from"]), guard.path(op["to"])
        s, d = view.info(src), view.info(dst)
        if s is None and d and d[0] == "file" and d[1:3] == (op["size"], op["mtime"]):
            probe = os.path.dirname(src)
            while view.info(probe) is None:
                view.add(probe, ("dir", 0, 0, d[3]))
                probe = os.path.dirname(probe)
            if view.info(probe)[0] != "dir":
                problems.append(f"{op['from']}: a parent is not a directory; cannot undo")
            view.remove(dst)
            view.add(src, d)
            todo.append(op)
        elif not (d is None and s and s[0] == "file" and s[1:3] == (op["size"], op["mtime"])):
            problems.append(f"{op['to']}: changed or missing since it was moved; cannot undo")
    for ref in created:
        guard.path(ref)
    mode = "execute" if args.execute else "dry-run"
    out.info(f"undo plan {pid}: {len(todo)} moves to reverse, {mode}")
    for op in todo:
        out.info(f"  move     {op['to']}\n             -> {op['from']}")
    code, done = EXIT_OK, 0
    if problems:
        problems.append("nothing was undone")
        code = EXIT_REFUSED
    elif not args.execute:
        code = EXIT_CHANGES if todo else EXIT_OK
    else:
        if todo:
            _confirm(args, pid, plan_path is None, "undo")
        ex = Executor(cfg, guard, out)
        ex.prefix = "undo_"
        with Lock(lock_path), Journal(journal_path, {"t": "undo_header", "plan_id": pid}) as journal:
            ex.journal = journal
            try:
                for op in todo:
                    root = op["from"].split(":", 1)[0]
                    parent = os.path.dirname(guard.path(op["from"]))
                    if parent != cfg["_roots"][root]:
                        ex.op_mkdir(View(dry=False), {"op": "mkdir", "path": guard.ref(root, parent)}, Ctx("undo"))
                    ex.op_move(View(dry=False), {**op, "from": op["to"], "to": op["from"]}, Ctx("undo"))
                    done += 1
                for ref in reversed(created):
                    path, real = guard.path(ref), View(dry=False)
                    found = real.info(path)
                    if found and found[0] == "dir" and real.is_empty_dir(path):
                        _rmdir(guard, path)
                        ex.log({"t": "rmdir", "path": ref})
            except (Refusal, OSError) as exc:
                ex.log({"t": "item", "item": "undo", "result": "partial"})
                problems.append(f"undo partial: {done} of {len(todo)} moves reversed ({exc}); "
                                "run --undo again to retry")
                code = EXIT_ERROR if isinstance(exc, OSError) else EXIT_REFUSED
        if not problems:
            out.info(f"undone: {done} moves reversed")
    for msg in problems:
        out.error(msg)
    out.doc({"plan_id": pid, "mode": mode, "exit": code, "errors": problems,
             "moves": [{"from": op["to"], "to": op["from"]} for op in todo]})
    return code


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors are exit 1; exit 2 means "plan has changes"
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="tidyshelf-apply", description="Apply a tidyshelf plan. Dry-run unless --execute.")
    p.add_argument("plan", nargs="?", help="plan file, or - for stdin")
    p.add_argument("--execute", action="store_true", help="really do it (default: dry-run)")
    p.add_argument("--yes", action="store_true", help="do not ask for the typed confirmation")
    p.add_argument("--approve", metavar="PLAN_ID", help="refuse unless the plan id matches")
    p.add_argument("--only", metavar="ITEM_ID", action="append", help="apply only this item (repeatable)")
    p.add_argument("--skip", metavar="ITEM_ID", action="append", help="skip this item (repeatable)")
    p.add_argument("--max-age", metavar="AGE", help="refuse plans older than this, e.g. 24h")
    p.add_argument("--allow-large-delete", action="store_true", help="lift the delete caps")
    p.add_argument("--on-error", choices=("stop", "continue"), default="stop",
                   help="stop: run nothing if any item fails pre-flight (default); continue: skip it")
    p.add_argument("--config", metavar="FILE", help="config file (default: $TIDYSHELF_CONFIG, usual places)")
    p.add_argument("--undo", action="store_true",
                   help="reverse the moves a run of PLAN made (read from the journal next to it)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--quiet", action="store_true", help="only print errors")
    p.add_argument("--no-color", action="store_true", help="no colour (output has none anyway)")
    p.add_argument("--version", action="version", version=f"tidyshelf-apply {VERSION}")
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # --help / --version (0) or a usage error (1)
        return exc.code if isinstance(exc.code, int) else EXIT_ERROR
    out = Out(args)
    try:
        cfg = load_apply_config(args.config)
        return cmd_undo(args, cfg, out) if args.undo else cmd_apply(args, cfg, out)
    except (ReadError, ConfigError) as exc:
        code = EXIT_ERROR
        msg = str(exc)
    except (PlanError, Refusal) as exc:
        code = EXIT_REFUSED
        msg = str(exc)
    except OSError as exc:
        code = EXIT_ERROR
        msg = f"{exc.filename or ''}: {exc.strerror or exc}"
    for line in msg.splitlines():
        out.error(line)
    out.doc({"exit": code, "errors": msg.splitlines()})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
