"""Names the tool generates: template rendering and filesystem-safe names (docs/NAMING.md)."""

from __future__ import annotations

import re
import string

# A movie folder that already follows the standard: "<Title> (<Year>)" plus kept tags/suffixes.
MOVIE_FOLDER_RE = re.compile(r"(?P<title>\S.*?) \((?P<year>\d{4})\)(?P<suffix>(?: \[[^\]]+\])*(?: - .+)?)")

_ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_SEGMENT = re.compile(r"<([^<>]*)>|([^<>]+)")
_FORMATTER = string.Formatter()


def _empty(value) -> bool:
    return value is None or value == ""


def _fill(text: str, values: dict) -> tuple[str, bool]:
    """Fill the placeholders of one segment. Returns (text, any placeholder was empty)."""
    out, any_empty = [], False
    for literal, name, spec, _conv in _FORMATTER.parse(text):
        out.append(literal)
        if name is None:
            continue
        value = values.get(name)
        if _empty(value):
            any_empty = True
            continue
        out.append(format(value, spec or ""))
    return "".join(out), any_empty


def render(template: str, values: dict[str, object]) -> str:
    """Fill {name} / {name:spec} fields. A <...> segment is dropped when any placeholder in it is
    empty. Segments are split before values go in, so a value is always plain text."""
    out = []
    for optional, plain in _SEGMENT.findall(template):
        if plain:
            out.append(_fill(plain, values)[0])
            continue
        text, any_empty = _fill(optional, values)
        if not any_empty:
            out.append(text)
    return "".join(out)


def safe_name(text: str) -> str:
    """A name safe on common filesystems and Samba; "" when nothing usable is left."""
    text = _ILLEGAL.sub("", text.replace(": ", " - "))
    text = re.sub(r" {2,}", " ", text).strip(" .")
    return "" if text in ("", ".", "..") else text


def episodes_text(episodes: list[int], join: str) -> str:
    """[2] -> "E02"; [23, 24] with join "-E" -> "E23-E24"."""
    text = f"E{episodes[0]:02}"
    if len(episodes) > 1:
        text += f"{join}{episodes[-1]:02}"
    return text


def movie_names(cfg: dict, title: str, year: int) -> tuple[str, str]:
    """(folder, file stem) for a movie, from [naming] movie_folder and movie_file."""
    naming, values = cfg["naming"], {"title": title, "year": year}
    return safe_name(render(naming["movie_folder"], values)), safe_name(render(naming["movie_file"], values))


def subtitle_stem(cfg: dict, video_stem: str, lang: str, variant: str | None) -> str:
    return render(cfg["naming"]["subtitle_file"], {"video": video_stem, "lang": lang, "variant": variant})
