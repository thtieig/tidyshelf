# Advanced configuration

> Skeleton. Sections are filled in as the features land (M2–M6).

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

`<` and `>` cannot appear in real file names, so they are free to use as markers.
Templates must not contain `/` or `\`; folders come from the separate folder templates.

## I want ...

- *Plex-style names* — (M2)
- *to keep SDH subtitles as well* — `keep_sdh_alongside = true` in `[subtitles]` (M2)
- *to skip a folder* — add a regex to `[exclude] patterns`, e.g. `'^movies:_keep-out(/|$)'`
- *to fix one item by hand* — an `[[override]]` block (M2)
- *a show that numbers episodes oddly* — (M3)
- *a different quality policy* — `[video.quality]` (M4)
- *to run plan and apply on different machines* — (M6)
