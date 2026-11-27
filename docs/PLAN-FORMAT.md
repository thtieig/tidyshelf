# Plan file format (version 1)

A plan is a UTF-8 JSON object. `tidyshelf plan` writes it; `tidyshelf-apply` reads it and is
strict: unknown fields, missing fields, duplicate keys, floats, booleans where numbers are
expected, and negative numbers are all errors. The authoritative implementation is
`validate_plan()` in `src/tidyshelf_apply.py`.

```json
{
  "format": 1,
  "tool": "tidyshelf 0.2.0",
  "created": "2026-11-26T20:15:00Z",
  "items": [
    {
      "id": "movie-0001",
      "kind": "movie",
      "status": "ready",
      "source": "source:Some.Film.1999.1080p",
      "findings": [{"level": "warn", "code": "subtitle.language_mismatch", "msg": "..."}],
      "ops": [
        {"op": "mkdir", "path": "movies:Some Film (1999)"},
        {"op": "move", "from": "source:Some.Film.1999.1080p/film.mkv",
         "to": "movies:Some Film (1999)/Some Film (1999).mkv", "size": 1727951821, "mtime": 1723000000},
        {"op": "set_mode", "path": "movies:Some Film (1999)/Some Film (1999).mkv"},
        {"op": "delete", "path": "source:Some.Film.1999.1080p/readme.txt", "size": 86, "mtime": 1723000000,
         "reason": "junk"},
        {"op": "delete", "path": "source:Some.Film.1999.1080p", "dir": true, "reason": "empty"}
      ]
    }
  ]
}
```

## Top level

| Field | Required | Type | Meaning |
|---|---|---|---|
| `format` | yes | integer `1` | Format version. Anything else is refused. |
| `items` | yes | list | The items, in the order they are applied. |
| `tool` | no | string | Who wrote the plan. Informational. |
| `created` | no | string | ISO 8601 UTC time, e.g. `2026-11-26T20:15:00Z`. Needed for `--max-age`. |

## Items

| Field | Required | Type | Meaning |
|---|---|---|---|
| `id` | yes | string | Unique in the plan; letters, digits, `.` `_` `-`, at most 64 characters. |
| `kind` | yes | string | A lowercase word (`movie`, `series`, ...). Informational. |
| `status` | yes | string | `ready`, `needs_review` or `skipped`. Only `ready` items may have ops. |
| `ops` | yes | list | The operations, applied in order. Deletes come last. |
| `source` | no | path reference | What the item was made from. Informational. |
| `findings` | no | list | `{level, code, msg}`, level `info`/`warn`/`error`. Informational. |

## Path references

Every path is `root:relative/path`, where `root` is `source`, `movies` or `series` and is mapped
to a real folder by the applying machine's config. The relative part uses `/`, and must not be
empty, start with `/`, contain empty segments, `.` or `..`, or NUL.

## Operations

| `op` | Fields | Meaning |
|---|---|---|
| `mkdir` | `path` | Create the folder and any missing parents (`mkdir -p`), never a root itself. |
| `move` | `from`, `to`, `size`, `mtime` | Rename a regular file. `size` (bytes) and `mtime` (whole seconds, `st_mtime_ns // 10**9`) must still match. |
| `write` | `to`, `data_b64`, `sha256`, `src`, `src_sha256`, `size`, `mtime` | Create a new file in a library with exactly these bytes (see below). |
| `delete` | `path`, `size`, `mtime`, `reason` | Remove a regular file under `source`. |
| `delete` | `path`, `dir: true`, `reason` | Remove an empty folder under `source`. |
| `set_mode` | `path` | Apply `[owner]` from the applying machine's config. The plan carries no mode. |

### `write`

A cleaned copy of a subtitle (ad cues removed, converted to UTF-8). Added in tidyshelf 0.2.0; the
format number stays 1, since it only adds an operation (an older `tidyshelf-apply` refuses a plan
with it as an unknown op, and runs nothing).

| Field | Meaning |
|---|---|
| `to` | Where the new file goes. Only under `movies` or `series`; the destination must not exist. |
| `data_b64` | The file's bytes, base64 (standard alphabet, padded, no line breaks). At most 5 MiB decoded. |
| `sha256` | Lowercase hex sha256 of the decoded data; must match. |
| `src` | The file it was made from, under `source` (not in the trash). It is only read. |
| `src_sha256` | sha256 of that file; it must still match when the step runs. |
| `size`, `mtime` | Size and modification time (whole seconds) of **`src`**, as for `move`; they must still match. |

The planner puts the `write` before the `move` of `src` into `source:.tidyshelf-trash/` (or the
`delete` of it), and a `set_mode` on `to`. The plan id covers `data_b64`, so approving a plan
approves the exact bytes. `tidyshelf-apply` writes them to a temporary file next to `to`
(`.tidyshelf-tmp-*`, fsynced) and hard-links it to the final name, which cannot replace anything.
The journal records the op without `data_b64`.

Rules across the whole plan:

- A move into `source` must come from `source`, unless it goes into `source:.tidyshelf-trash/`.
  Junk is trashed as a move to `source:.tidyshelf-trash/<root>/<original relative path>`.
- Nothing is moved out of, or deleted inside, `.tidyshelf-trash/`.
- No path is the source (`move.from`, `delete.path`) of more than one op; no two ops share a
  destination (`move.to`, or a `move.to` equal to a `mkdir.path`); no destination is also a source.
- `write.to` counts as a destination (no other op may use it); `write.src` is read, not consumed:
  it may be the source of one write only, and must not be the destination of another op.
- Within an item, once a `delete` appears only deletes follow. Nested empty folders are deleted
  deepest first.

## Plan id

The plan id is not stored in the file; both sides compute it:

```python
canon = [[item["id"], item["status"], item["ops"]] for item in plan["items"]]
data = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
plan_hash = hashlib.sha256(data.encode("ascii")).hexdigest()
plan_id = plan_hash[:12]
```

`--approve` accepts the plan id or any longer prefix of `plan_hash`.

`created`, `tool`, `kind`, `source` and `findings` do not affect it; any change to an op, to the
order of ops or items, to an item id or status does.

## File names that are not valid UTF-8

Names are written with `ensure_ascii`, so a byte that is not valid UTF-8 appears as a lone
surrogate escape (`\udce9`) and round-trips back to the same byte on a Linux filesystem.

## Journal

`tidyshelf-apply --execute` appends JSON lines (`ensure_ascii`) to the journal:

| `t` | Fields | Written |
|---|---|---|
| `header` | `plan_id`, `plan_size`, `plan_mtime`, `started`, `tool` | at the start of every run |
| `mkdir` | `item`, `path` | before each folder level is created |
| `begin` / `done` | `item`, `n` (op index), `op` | before / after each operation (a `write` without `data_b64`) |
| `rollback` | `item`, `op` or `rmdir`, `result` | when a failed item is rolled back |
| `item` | `item`, `result` (`ok`, `rolled_back`, `failed`, `partial`) | at the end of each item |
| `trashdir` | `item`, `path` | a trash folder made to take back a written file (not a plan folder; undo ignores it) |
| `undo_*` | as above | written by `--undo`, which ignores them when it reads the journal |
