# Advanced configuration

> For v0.1. Every option is also documented, with its default, in `defaults.toml`.

Every setting, with its default and a comment, is in
[`src/tidyshelf/data/defaults.toml`](../src/tidyshelf/data/defaults.toml).
`tidyshelf config show` prints the effective config; `tidyshelf config check` catches typos
(unknown keys are errors, with a "did you mean" hint).

## Where config comes from

Layers, last wins:

1. built-in defaults
2. `/etc/tidyshelf.toml`
3. `~/.config/tidyshelf/config.toml` (or `$XDG_CONFIG_HOME/tidyshelf/config.toml`)
4. `--config FILE`, or the file named by `TIDYSHELF_CONFIG` when `--config` is not given

Tables merge key by key. A list replaces the default list completely
(so `languages = ["en"]` means only English, not "English as well").

## Naming templates

Templates in `[naming]` use `{placeholder}` fields. Numbers take a width, e.g. `{season:02}` → `05`.
A segment in `<angle brackets>` is dropped when any placeholder inside it is empty:

```toml
episode_file = "S{season:02}{episodes}< - {episode_title}>"
# S01E02 - Pilot     (with a title)
# S01E02             (without)
# S01E23-E24 - Finale  (multi-episode: {episodes} = "E23-E24")
```

The brackets are read from the template before any value is filled in, so a title that itself
contains `<` or `>` (allowed on Linux) is copied as it is and never mistaken for a marker.
Templates must not contain `/` or `\`; folders come from the separate folder templates.

## How a movie is read

Each entry directly inside the source folder is one item: a folder, or a loose video together
with the subtitles named after it (`Film.mkv` + `Film.en.srt`). guessit reads title and year from
the folder name and from the video's name. Title and year are always taken together from one
name: the folder's if it has both (`[parse] folder_beats_file = false` prefers the file), else the
one that has both; a disagreement is reported. A "title" made only of release words (`1080p`,
`x264`, ... from `junk_tokens` and your `extra_tokens`) counts as no title. A folder already named
`Title (Year)` keeps its name, including tags like `[ES]`, unless a tag holds release words
(`[1080p]`), in which case the name is built fresh.

`tidyshelf explain PATH` prints every step: what guessit read, which rules hit, what each
subtitle turned out to be, and the resulting operations.

### Fixing names with rules and overrides

Name rules change each folder and file name *before* it is read, in order (Python regex, `\1`
for groups). Use them for a prefix the parser would otherwise take as the title:

```toml
[[parse.name_rules]]
match = '^Example\.Site - '
replace = ''
```

Title rules change the title *after* it is read, the same way (`[[parse.title_rules]]`).

An override decides one item. `match` is a glob on the item's path inside the source folder, or a
regex after `re:`. The first matching block wins.

```toml
[[override]]
match = "Winter.Lights*"
title = "Winter Lights"
year = 2004

[[override]]
match = "re:^Old Rips"
ignore = true
```

To keep a whole folder out of every plan, `[exclude] patterns` is the better tool.

## How a series is read

A season pack (`Show.S01.720p/`), a show folder (`Show/Season 2/`, `Show/Specials/`) or a loose
episode file is one item. Each video needs an episode number; these styles are understood:
`S01E02`, `1x02`, `102`, `Show - 06 - Title`, bare absolute numbers (`096`), and multi-episode
files (`S01E23-E24`, `S03E16E17`, `1x08-09` all become `S01E23-E24`).

- A file without a season takes the season of the folder it sits in (`Season 4`, `S04`, `Specials`
  = 0), then the season in the item's folder name, then an `[[override]]` `season`, then 1. With
  season 1 only assumed and more than 26 episodes, the item needs review.
- Bare three-digit numbers: if any starts with `0` (`001`, `096`), the show counts as absolutely
  numbered (season 1, episode 96); otherwise `102` means S01E02. A file far outside the numbering
  and much smaller than the rest (`999` next to `001`–`104`) goes to `Season 00` and the item
  needs review.
- Markers like `S1M1`, `S1M2` are numbered E01, E02, ... in their order (reported).
- Names: `Series/<Show>/Season <N>/S01E02 - Title.ext` (`[naming] season_folder`, `episode_file`,
  `specials_folder`); the episode title is kept when the name has one. An existing show folder in
  the library (also `Show (Year)`) is reused, so new episodes join it.
- Subtitles go next to their episode (`S01E02 - Title.en.srt`). A subtitle belongs to the episode
  with the same numbers in its name, or the video whose name it starts with; a subtitle that matches
  no episode goes to the trash.
- Two videos for one episode, a video without an episode number, or an episode the library already
  has: the item needs review (with `[conflicts] on_exists = "skip"`, an existing episode skips it).

## Subtitles

Subtitles are judged by what is inside them, not by their names:

1. Identical files (same content) are kept once.
2. The language is detected by counting common words; word lists ship for English, Spanish,
   Italian, French, German, Portuguese, Romanian and Dutch. A confident result (`min_confidence`,
   default 0.6) wins over the file name; a disagreement is reported. Old 8-bit encodings
   (Windows-1252, Windows-1250) are recognised.
3. Languages not in `[subtitles] languages` go to the trash. Text in a language without a word
   list is "unknown" rather than mistaken for a similar one; its name tag (`hrv`, `Croatian`)
   still counts. Such a language can be kept, recognised by the tag only (`config check` warns).
   One subtitle with no recognisable language at all is taken as `default_language`; several
   make the item *needs review*.
4. SDH is recognised by name markers (`sdh_markers`) or by many `[MUSIC]`-style cues; forced by
   `forced_markers`.
5. Per language one subtitle is kept: plain over SDH (`prefer = "sdh"` reverses it), then the one
   with more cues. A forced track is kept as well. `keep_sdh_alongside = true` keeps the SDH next
   to the plain one. An SDH that is the only good one is kept and named `.sdh`.
6. VobSub pairs (`.idx` + `.sub`) stay together; a lone `.sub` or `.idx` is junk.

Advertising cues at the start or end (`ad_patterns`, plus your own `ad_patterns_extra`) and
subtitles with very few cues are reported, never edited.

## Video quality

With `ffprobe` installed (part of FFmpeg), every video in the plan is read: codec, resolution,
duration, bitrate, audio tracks. Without it the plan is made anyway and `plan` warns once that
quality was not checked (`[video] ffprobe` names the program; empty switches the check off).

Each file is put in a class by its height (wide "scope" files count by their width: 1920×816 is
1080p): `sd` below 720, `hd`, `fhd` from 1080, `uhd` from 2160. `[video.quality]` then checks:

| Check | Rule | Action key (default) |
|---|---|---|
| large | above the class's `max_gb_per_hour` or `max_video_kbps` | `on_large` (warn) |
| tiny | below the class's `min_gb_per_hour` | `on_tiny` (warn) |
| codec | video codec not in `allowed_codecs` | `on_codec` (warn) |
| low_res | effective height below `min_height` | `on_low_res` (warn) |
| no_audio | no audio track and `audio_required` | `on_no_audio` (reject) |

`warn` adds a finding; `reject` makes the item *needs review* (nothing of it is applied); `ok`
ignores the check. A file ffprobe cannot read is reported as *suspect*. tidyshelf never
re-encodes; for a large file `explain` shows a suggested `ffmpeg` command you can run yourself.
Embedded subtitle streams are listed, and a subtitle that runs more than 5 minutes past the end of
its video is reported (it probably belongs to another cut). `plan --deep` also decodes every frame
with `ffmpeg` to find damaged files (slow); a file with errors needs review.

When the library already has the movie, the finding shows both qualities so you can choose.

## Auditing the library

`tidyshelf plan --audit` looks at the movie and series libraries instead of the source and plans
whatever is needed to make them follow the same standard: misnamed videos and episodes renamed,
subtitles moved to `Subs/` (movies) or next to their episode (series) and named by language,
`Season 01` merged into `Season 1`, junk and unwanted subtitles moved to
`source:.tidyshelf-trash/movies/...` or `.../series/...`. It is a normal plan: read it, apply it
with `tidyshelf-apply`.

- A consistent library gives an **empty plan**. Folders with nothing to fix are left out; folders
  with only a warning (a large file, a mislabelled subtitle) are listed as *skipped* so you see them.
- tidyshelf never deletes anything in a library. When a fix leaves a folder empty (a renamed movie
  folder, an old `Season 01`), the plan says so and you remove that folder by hand.
- Two editions of one film (`Title (2004)` and `Title (2004) [ES]`) are not a clash.
- The audit takes no PATH; it always checks both libraries.

## Junk

Everything that is not a video or a subtitle, plus files matching `[junk] patterns` (samples,
`.DISABLE.` files), is junk. With `junk_action = "trash"` (the default) it is moved to
`source:.tidyshelf-trash/<root>/<original path>`, where you can check it and empty it yourself;
`"delete"` deletes it instead (capped by `[safety]`). Symlinks, special files, excluded paths and
extras folders (`[parse] extras_folders`) are left untouched, and so is the folder that holds them.

## Running it

**On one machine** (the one that holds the files): `tidyshelf run [PATH...]` plans and then hands
the plan to the same executor as `tidyshelf-apply`, as a dry-run. `tidyshelf run --execute` asks
for the plan id and applies it. The plan is kept in `~/.local/state/tidyshelf/plans/` (or
`$XDG_STATE_HOME`), with its journal next to it, so `tidyshelf undo <that plan>` works later.
`--audit`, `--deep`, `--approve`, `--allow-large-delete` and `--on-error` work as for `plan` and
`tidyshelf-apply`.

**On two machines** (plan on your PC, apply on the NAS):

```
pc$  tidyshelf plan --out plan.json            # summary + plan id, e.g. a1b2c3d4e5f6
pc$  tidyshelf build-apply                     # once: writes tidyshelf-apply.pyz
pc$  scp plan.json tidyshelf-apply.pyz nas:
nas$ python3 tidyshelf-apply.pyz plan.json     # dry-run, same summary and id
nas$ python3 tidyshelf-apply.pyz plan.json --execute --approve a1b2c3d4e5f6
```

The NAS needs Python 3.11+ and a config of its own (`/etc/tidyshelf.toml`,
`~/.config/tidyshelf/config.toml` or `--config`) with its `[roots]` as the NAS sees them, and, if
you want them, `[owner]`, `[safety]` and `[exclude]`. The plan itself names folders only as
`source:`, `movies:`, `series:`. Unattended: `tidyshelf plan --out - | ssh nas python3
tidyshelf-apply.pyz - --execute --yes --approve <id>`.

**As a shell script:** `tidyshelf plan --emit sh --out plan.sh` writes the plan as a bash script
(root folders and owner/modes as variables at the top; dry-run unless `--execute`). It is for
reading, or for a machine without Python. It keeps the checks shell can do (see SAFETY.md) but
has no journal, no rollback and no lock. `--emit text` writes a readable listing.

**Undo:** `tidyshelf undo plan.json` (or `tidyshelf-apply --undo plan.json`) puts moved files back
and removes folders the run created, if empty; dry-run first, then `--execute`. Deleted files are
not restored; with the default `junk_action = "trash"`, junk is in `.tidyshelf-trash/`.

## Exit codes

`0` nothing to do / done, `1` error, `2` the plan has changes (plan, dry-run), `3` a guardrail
refused, `4` some items need review. With several, the first of 1, 3, 4, 2 wins.

## I want ...

- *Plex-style names* — later (only the `kodi` preset exists today)
- *to keep SDH subtitles as well* — `keep_sdh_alongside = true` in `[subtitles]`
- *SDH rather than plain* — `prefer = "sdh"` in `[subtitles]`
- *junk deleted, not trashed* — `junk_action = "delete"` in `[junk]`
- *to skip a folder* — add a regex to `[exclude] patterns`, e.g. `'^movies:_keep-out(/|$)'`
- *to fix one item by hand* — an `[[override]]` block (see above)
- *to see why something was named that way* — `tidyshelf explain PATH`
- *to check my existing library* — `tidyshelf plan --audit`
- *a show that numbers episodes oddly* — see "How a series is read"; an `[[override]]` `season` fixes loose numbers
- *a different quality policy* — `[video.quality]` (see above)
- *to find damaged files* — `tidyshelf plan --deep`
- *to run plan and apply on different machines* — see "Running it"
- *a script instead of the executor* — `tidyshelf plan --emit sh` (less safe)
