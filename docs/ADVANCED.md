# Advanced configuration

> Movies and series are covered. Quality (M4), audit (M5) and running plan and apply on
> different machines (M6) are filled in as they land.

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

## Junk

Everything that is not a video or a subtitle, plus files matching `[junk] patterns` (samples,
`.DISABLE.` files), is junk. With `junk_action = "trash"` (the default) it is moved to
`source:.tidyshelf-trash/<root>/<original path>`, where you can check it and empty it yourself;
`"delete"` deletes it instead (capped by `[safety]`). Symlinks, special files, excluded paths and
extras folders (`[parse] extras_folders`) are left untouched, and so is the folder that holds them.

## I want ...

- *Plex-style names* — later (only the `kodi` preset exists today)
- *to keep SDH subtitles as well* — `keep_sdh_alongside = true` in `[subtitles]`
- *SDH rather than plain* — `prefer = "sdh"` in `[subtitles]`
- *junk deleted, not trashed* — `junk_action = "delete"` in `[junk]`
- *to skip a folder* — add a regex to `[exclude] patterns`, e.g. `'^movies:_keep-out(/|$)'`
- *to fix one item by hand* — an `[[override]]` block (see above)
- *to see why something was named that way* — `tidyshelf explain PATH`
- *a show that numbers episodes oddly* — (M3)
- *a different quality policy* — `[video.quality]` (M4)
- *to run plan and apply on different machines* — (M6)
