# Naming standard

The rules tidyshelf enforces. Copy to the new repo as `docs/NAMING.md`; this is the source of truth for the planner and its golden tests.
Base convention: the Kodi naming scheme, also read natively by Jellyfin, Plex and Emby, so the library stays portable.
A second preset, `plex`, follows Plex's own layout (see "Plex preset" at the end).

## Movies

```
Movies/<Title> (<Year>)/<Title> (<Year>).<ext>
Movies/<Title> (<Year>)/Subs/<Title> (<Year>).<lang>.<ext>
```

- The folder name is authoritative once it matches `<Title> (<Year>)`. The video is renamed to match it, extension kept.
- Bracket tags (`[ES]`, `[FR]`) and suffixes (`- (Studio 54)`) already in a folder name are kept as they are.
- Subtitles always live in `Subs/`, including ones found loose in the movie folder.
- VobSub pairs (`.idx` + `.sub`) stay together and share one basename.

## Series

```
Series/<Show>/Season <N>/S<NN>E<NN>[-E<NN>][ - <Episode Title>].<ext>
Series/<Show>/Season 00/          specials
Series/<Show>/Featurettes/        extras, left untouched
```

- Show folder: `<Title>`, plus ` (<Year>)` when the source name has a year, plus bracket tags (`[ITA]`) kept as for movies. An existing library show with the same title is reused when its year and tags don't contradict the source (`Show [ITA]` takes a new `Show` episode); never a second folder for the same show.
- Season folder: no zero padding (`Season 1`, `Season 11`), always with a space.
- Filename: `S<NN>E<NN>`, two digits each, whatever the folder padding is.
- No show name in the episode filename. Players take the show from the folder and the numbers from `SxxEyy`.
- Keep the episode title whenever the source name has one. Fall back to bare `SxxEyy.ext` only when there is no title text.
- Multi-episode files use one form only: `SxxEyy-Eyy`. Inputs like `S01E23-E24`, `S03E16E17` and `1x08-09` all become that.
- Accepted input styles: `S01E02`, `1x02`, `102`, `Show - 06 - Title`, bare absolute numbers (`096`).
- Shows with episodes loose in the show folder get a `Season 1/` created (single-season assumption, flagged `needs_review` if numbering suggests more).

### Anomalous numbering

- Unrecognised in-season markers (e.g. `S1M1`, `S1M2`) map in original order to `E01`, `E02`, ...
- An absolute-numbered file far outside the show's normal range (e.g. `999` next to `001`-`104`) and much smaller than the rest goes to `Season 00/`. It is always flagged `needs_review`.

## Subtitles

- Keep only the configured languages (default `en`, `es`, `it`). Everything else is junk.
- Name: `<video basename>.<lang>[.sdh|.forced].<ext>`, with a lowercase ISO 639-1 code.
- Language comes from the content first (stopword counting), then the filename tag. When they disagree, the content wins and the item is reported.
- No language at all and a single track: default language from config (`en`).
- Duplicates are removed by content hash.
- Plain is preferred over SDH. SDH is kept when it is the only valid candidate.
- Orphans are dropped: a lone `.sub` without `.idx`, and `.DISABLE.*` files.
- Series subtitles sit next to the episode (`S01E02.en.srt`), with no `Subs/` folder.

## What is kept and what is junk

Keep only video and subtitle files. Everything else is junk: artwork, `.nfo`, `.txt` notices, `Thumbs.db`, `.vsmeta`, sample clips.
Junk goes to a trash folder by default (`junk_action = "trash"`). Deleting is opt-in and capped.

- Video: `.mkv .mp4 .avi .m4v .wmv .ts .mpg .mpeg .mov .flv`
- Subtitles: `.srt .ass .ssa .sub .idx .vtt`

## Filesystem-safe names

Characters illegal on common filesystems and Samba (`: * ? " < > |`) are replaced or stripped in every name the tool generates.

## Excluded areas

Config can list folders the tool never touches (for example a legacy folder of old rips). They are skipped by the planner and refused by the executor.

## Video size

The tool never re-encodes. It reports files whose size or bitrate exceeds the configured policy and suggests a re-encode command. Typical outliers are Blu-ray-rip bitrates with lossless audio.

## Plex preset

`[naming] preset = "plex"` switches the templates to the layout Plex documents. Everything else is
unchanged: subtitle choice, junk, guardrails, show matching (`Show [ITA]`, `Show (2015)`), `--audit`.
The library root folders (`Movies`, `TV Shows`, ...) are yours, set in `[roots]`.

```
<movies root>/<Title> (<Year>)/<Title> (<Year>).<ext>
<movies root>/<Title> (<Year>)/<Title> (<Year>).<lang>[.sdh|.forced].<ext>
<series root>/<Show> (<Year>)/Season 01/<Show> (<Year>) - s01e01[-e02][ - <Episode Title>].<ext>
<series root>/<Show> (<Year>)/Season 00/<Show> (<Year>) - s00e01 - <Title>.<ext>
```

- Subtitles sit next to their video with the same base name; the movie `Subs/` folder is not used
  (`movie_subs_folder = ""`). Plex also reads a subtitle subfolder, but next to the video is the form
  every Plex client and its docs agree on, and it is the one that reads `sdh` and `forced` tags reliably.
- Season folders are zero padded (`Season 01`); specials go in `Season 00`.
- The file name carries the show name and year (`{show}`: the show folder without bracket tags), lowercase
  `s01e01`; a multi-episode file is `s01e01-e02`.
- Auditing a Kodi-style tree with this preset renames it into Plex style (moves only); a correct
  Plex tree gives an empty plan.
- Any template you set yourself in `[naming]` wins over the preset.
