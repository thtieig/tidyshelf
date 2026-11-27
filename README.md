# tidyshelf

Tidy a folder of video files in any naming state into a library with one consistent,
Kodi/Jellyfin/Plex/Emby-portable naming standard, and check the library you already have against
it. No AI, no network, no cloud: the same files and the same config always give the same plan.

> **Status:** v0.2. Movies and series: naming, subtitles chosen by their content (ad cues removed and
> converted to UTF-8 in the `.srt` files kept), quality checks with ffprobe, junk to a trash folder,
> and an audit of the existing library.

**It moves and can delete files.** Read the plan before applying it. Dry-run is the default.
Keep backups. See [docs/SAFETY.md](docs/SAFETY.md).

## How it works

Everything is a **plan**: a small JSON file listing every step. Making a plan changes nothing;
applying it is a separate step, done by one small stdlib-only program that checks each step again.

| your PC | the NAS |
|---|---|
| `tidyshelf plan --out plan.json` looks at the files and writes a plan. | `tidyshelf-apply plan.json` shows what would happen; `--execute` does it. |

Or, on the machine that holds the files, both in one go: `tidyshelf run` (dry-run), then
`tidyshelf run --execute`.

## Install (from a checkout)

```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/tidyshelf init
```

`init` writes a config with only three paths to fill in:

```toml
[roots]
source = "/mnt/nas/to-sort"          # the old collection
movies = "/mnt/nas/Videos/Movies"
series = "/mnt/nas/Videos/Series"
```

Everything else has defaults. Check it with `tidyshelf config check`. For quality checks install
FFmpeg (`ffprobe`); without it the plan is made anyway and says quality was not checked.

## Plan, read, apply

```
tidyshelf plan --out plan.json         # changes nothing; prints each item and the plan id
tidyshelf explain "source:Some.Folder" # why one item is planned the way it is
tidyshelf plan --verbose               # also print the info findings (hidden by default)
tidyshelf plan --kind series PATH      # treat the item as a series (or movie) whatever its name says
tidyshelf-apply plan.json              # dry-run: shows every step
tidyshelf-apply plan.json --execute    # does it, after you type the plan id
tidyshelf undo plan.json --execute     # puts moved files back (deleted ones are not restored)
tidyshelf plan --audit --out fix.json  # check the library itself; empty plan = consistent
```

Items the tool cannot decide (no year, several videos, a clash with the library, a file that fails
the quality policy) are marked *needs review* and left alone. Fix the cause (an `[[override]]` for a
missing year or a wrong title, or by hand for the rest) and plan again. Junk is moved to
`.tidyshelf-trash/` in the source folder, never deleted unless you ask for it.

## Two machines

`tidyshelf build-apply` writes `tidyshelf-apply.pyz`, the executor as one file. Copy it and the
plan to the NAS, give the NAS a config with its own `[roots]` (plans name folders as
`source:` / `movies:` / `series:`, never as paths), and run `python3 tidyshelf-apply.pyz plan.json`.
Nothing else needs installing there. `tidyshelf plan --emit sh` writes the plan as a shell script
instead, for reading or as a fallback; it is less safe (see SAFETY.md).

See [docs/ADVANCED.md](docs/ADVANCED.md) to customise, and [docs/NAMING.md](docs/NAMING.md) for
the naming standard.

## Licence

MIT. See [LICENSE](LICENSE).
