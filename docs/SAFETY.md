# Safety

tidyshelf moves files and can delete them. It is built so that a mistake in planning, a stale
plan, or a tampered plan file cannot do damage outside what you reviewed. Still:

- **Read the plan** before you apply it. `tidyshelf-apply plan.json` (no flags) shows every step.
- **Dry-run is the default.** Nothing changes without `--execute`.
- **Keep backups.** No tool replaces them.

## How a plan is applied

`tidyshelf-apply` is one Python file using only the standard library: no install, no network,
no subprocesses, no `eval`. It treats the plan as untrusted input, even though tidyshelf wrote it.

1. **Config.** Root folders (`source`, `movies`, `series`) come only from the config on the machine
   that applies the plan. They must be absolute, must exist, must not be `/` and must not overlap
   or contain each other. Unknown keys or wrong types are errors.
2. **Plan check.** The plan must be format 1, with no unknown or missing fields, no duplicate JSON
   keys, whole non-negative numbers only. Only five operations exist: `mkdir`, `move`, `write`,
   `delete`, `set_mode`. Items that need review or are skipped carry no operations.
3. **Plan id.** A 12-character id is computed from the exact operations (the first characters of
   a sha256, which the dry-run also prints in full). `--approve ID` refuses to run anything else,
   so what you reviewed is what runs. Editing a plan changes its id. For unattended runs, pass a
   longer prefix of the full hash (any length from 12 to 64 characters).
4. **Pre-flight.** The whole plan is first played against an in-memory picture of the folders.
   With `--on-error stop` (the default) nothing runs if any item fails; with `continue`
   failing items are skipped.
5. **Run.** Every operation is checked again right before it runs.

## The guardrails

**Paths**
- Plans name paths as `root:relative/path`, never absolute paths. `..`, `.`, empty segments,
  NUL characters, absolute paths and unknown roots are refused.
- No symlink is followed anywhere below a root: a symlinked parent folder, a symlink as a move
  source or delete target, all refused. Devices, sockets and FIFOs are refused.
- Every change is made inside folders opened one level at a time from the root without following
  symlinks, so a folder swapped for a symlink after the checks (by another user of a share) is
  refused at that moment rather than followed. Modes are set on the opened file, never by name.
- Paths matching `[exclude] patterns` are refused, as are the lock file, the journal and the plan file.

**Moves**
- A move is a single `rename` on one filesystem. Across filesystems it is refused (checked in
  pre-flight and again if the kernel says `EXDEV`); it is never turned into copy + delete.
- A move never overwrites: if anything exists at the destination (file, folder, even a broken
  symlink), it is refused.
- The source must still have exactly the size and modification time recorded in the plan, so a
  file that changed or is still being written is left alone.
- Nothing is moved from a library into `source`, except into `source:.tidyshelf-trash/`.
  Nothing is moved out of the trash by a plan.
- No path is the source of two operations, and no operation's destination is another's source
  (no chains, no swaps), so a re-run can always tell what is done.

**Writes** (a cleaned subtitle, made from a file under `source`)
- Only into the `movies` or `series` roots, only to a path that does not exist, with exactly the
  bytes in the plan: the plan id covers them, and `sha256(data)` must equal the plan's `sha256`.
  The data is at most 5 MiB (larger is refused when the plan is read).
- Never over anything: the bytes go to a temporary file in the destination folder (opened with
  `O_EXCL`, no symlink followed, fsynced) which is hard-linked to the final name; a link cannot
  replace an existing path, even if one appears in the last moment. Where hard links are
  unsupported it falls back to the same check + `rename` a move uses (see Known limits). The folder
  is fsynced afterwards. A leftover `.tidyshelf-tmp-*` file only exists if the machine died mid-write.
- The file it is made from (`src`, only under `source`, never in the trash) is read through its
  opened folder without following symlinks, and must still have the planned size, modification time
  and sha256, checked in pre-flight and again right before the write.
- The journal records every write (without the data). Resume skips a write whose destination
  already holds exactly the planned bytes.
- A write never deletes or changes anything else; the original goes to the trash by an ordinary move.
- Undo (and rollback of a failed item) never deletes from a library: it moves the written file into
  `source:.tidyshelf-trash/<root>/<path>`, and only if it still has exactly the bytes that were
  written. A file edited since is left where it is and the undo is refused, with nothing undone.

**Deletes**
- Only under `source`, never the root itself, never inside the trash, never in a library.
- Only regular files (with matching size and mtime) and empty folders.
- At most 200 files / 50 MB per run by default (`[safety]`); `--allow-large-delete` lifts it.
  Removing empty folders does not count.
- By default junk is not deleted at all but moved to `source:.tidyshelf-trash/<root>/<path>`
  (`junk_action = "trash"`), which you can inspect and empty yourself.

**Ownership and modes**
- Only from `[owner]` in the applying machine's config, only on explicit `set_mode` operations (the
  written file is created with the usual umask permissions, like any new file),
  and ownership only when running as root. The plan cannot choose a mode or an owner.

**Running**
- `--execute` asks you to type the plan id, unless `--yes` is given. A plan read from stdin, or
  a run without a terminal, needs `--yes`.
- `--max-age 24h` (or `[safety] max_age`; if both are set, the stricter wins) refuses old plans,
  and plans dated more than 5 minutes in the future. `[safety] require_approve = true` makes
  `--approve` mandatory.
- A lock file (`source:.tidyshelf.lock`) stops two runs from overlapping.

## Journal, resume, rollback, undo

With `--execute`, every step is written to a journal next to the plan (`plan.journal.jsonl`, or
`./tidyshelf-<id>.journal.jsonl` for a plan from stdin), flushed to disk *before* the step runs.
An existing journal is only used if it belongs to the plan, to the user running the tool, and is
not writable by group or others; otherwise the run is refused (move the file away).

- **Resume.** Re-running the same plan skips what is already done (a moved file is at its
  destination with the planned size and mtime; a deleted file is gone; a folder exists).
- **Rollback.** If an item fails before it starts deleting, its moves are put back, files it wrote
  go to the trash, and the folders it created are removed (only those, only if empty). A file that changed after it was moved is
  left where it is and reported. If an item fails while deleting, it is reported as `partial`.
- **Undo.** `tidyshelf-apply --undo plan.json` (dry-run first, then `--execute`) reads the journal
  next to the plan, moves files back where they came from, moves written files into the trash and removes folders the run created, if
  empty. Every journal line must match an operation in the plan, so a tampered journal is refused.
  It checks every move first and does nothing unless all of them can be reversed. If a step still
  fails while running (a disk error, say), it stops, says how many moves were reversed, and can
  simply be run again. Deleted files are not restored (that is what the trash is for).

## Running in one go, auditing, and the shell script

- `tidyshelf run` writes the plan to a file and applies it with the same executor and checks;
  there is no second code path. `--execute` still asks for the plan id.
- `tidyshelf plan --audit` only plans moves inside a library or into the trash. Nothing is ever
  deleted in a library: a folder a fix leaves empty is reported for you to remove by hand. Moving
  library junk to `source:.tidyshelf-trash/` is refused if the library and the source are on
  different filesystems (it is never copied).
- Planning reads files only. With quality checks on, `ffprobe` (and `ffmpeg` for `--deep`) run on
  each video: no shell, an absolute path, a time limit, read-only. Nothing is re-encoded.
- `tidyshelf plan --emit sh` is **less safe** than `tidyshelf-apply`: no journal, no rollback, no
  lock, and a wider gap between a check and its step. It still refuses, before the first step runs,
  a file that changed (size, mtime), a symlink (as the file or any folder on the way), an existing
  destination, a move across filesystems, a delete outside the source root, and more deletes than
  `[safety]` allows (`ALLOW_LARGE_DELETE=1` lifts that); moves use `mv -n`. Names are quoted so
  that no file name can run a command. It needs bash and GNU coreutils.
- The script supports `write` with the same checks: data hash, source size, mtime and sha256 (via
  `sha256sum`), writes only into the movies and series folders, the 5 MiB cap, a temporary file
  (`mktemp`, same folder) verified by hash and put in place with `ln` (which, like the executor's
  link, cannot replace anything). It has no journal, so no undo for it. The data is inside the script
  as base64, so the script is bigger than the plan's other steps.

## Known limits

- Between checking that a destination does not exist and the `rename`, another program could
  create that path; `rename` would then replace it. The window is microseconds and needs another
  program writing the same new name at the same moment. The standard library offers no
  "rename without replace" call; this is accepted.
- The size + mtime check cannot detect a change that keeps both (rare, and needs deliberate effort).
- A move source is checked once more through its opened folder right before the `rename`; if it is
  swapped for a symlink in the instant between, the symlink itself is moved (a rename never follows it).
- Undo after the library has been changed by hand will refuse rather than guess.
