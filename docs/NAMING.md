# Naming standard

The rules tidyshelf enforces. Copy to the new repo as `docs/NAMING.md`; this is the source of truth for the planner and its golden tests.
Base convention: the Kodi naming scheme, also read natively by Jellyfin, Plex and Emby, so the library stays portable.

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
