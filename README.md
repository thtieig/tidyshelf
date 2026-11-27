# tidyshelf

Tidy a folder of video files in any naming state into a library with one consistent,
Kodi/Jellyfin/Plex/Emby-portable naming standard. No AI, no network, no cloud: the same input
and the same config always give the same plan.

> **Status:** early development. Movies and series can be planned, checked for quality and
> applied; `--audit` is still to come.

**It moves and can delete files.** Read the plan before applying it. Dry-run is the default.
Keep backups. See [docs/SAFETY.md](docs/SAFETY.md).

## How it works

| your PC | the NAS |
|---|---|
| `tidyshelf plan --out plan.json` looks at the files and writes a plan. Changes nothing. | `tidyshelf-apply plan.json` shows what would happen. `--execute` does it. |

## Install (from a checkout)

```
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/tidyshelf init
```

`init` writes a config with only three paths to fill in:

```toml
[roots]
source = "/mnt/nas/to-sort"
movies = "/mnt/nas/Videos/Movies"
series = "/mnt/nas/Videos/Series"
```

Everything else has defaults. Check it with `tidyshelf config check`.

## Plan, read, apply

```
tidyshelf plan --out plan.json        # changes nothing; prints each item and the plan id
tidyshelf explain "source:Some.Folder" # why one item is planned the way it is
tidyshelf-apply plan.json             # dry-run: shows every step
tidyshelf-apply plan.json --execute   # does it, after you type the plan id
```

Items the tool cannot decide (no year, several videos, a clash with the library) are marked
*needs review* and left alone. Fix the cause (an `[[override]]` for a missing year or a wrong
title, or by hand for the rest) and plan again. Junk is moved to
`.tidyshelf-trash/` in the source folder, never deleted unless you ask for it.

See [docs/ADVANCED.md](docs/ADVANCED.md) to customise, and [docs/NAMING.md](docs/NAMING.md) for
the naming standard.

## Licence

MIT. See [LICENSE](LICENSE).
