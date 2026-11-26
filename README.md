# tidyshelf

Tidy a folder of video files in any naming state into a library with one consistent,
Kodi/Jellyfin/Plex/Emby-portable naming standard. No AI, no network, no cloud: the same input
and the same config always give the same plan.

> **Status:** early development. Not usable yet.

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

Everything else has defaults. See [docs/ADVANCED.md](docs/ADVANCED.md) to customise,
and [docs/NAMING.md](docs/NAMING.md) for the naming standard.

## Licence

MIT. See [LICENSE](LICENSE).
