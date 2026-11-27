"""Reading title, year and kind from names: guessit first, then the rules in [parse] and [[override]]."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from guessit import guessit

_TOKEN_SPLIT = re.compile(r"[\s._-]+")
_DOMAIN = re.compile(r"(?i)(www\.)?[\w-]+(\.[\w-]+)*\.(com|net|org|info)")


@dataclass(frozen=True)
class Parsed:
    title: str | None
    year: int | None
    kind: str  # "movie" | "series"
    season: int | None
    episodes: tuple[int, ...]
    episode_title: str | None


@dataclass
class MovieName:
    title: str | None
    year: int | None
    kind: str
    findings: list = field(default_factory=list)  # (level, code, msg)
    review: str | None = None
    ignore: bool = False


def _ints(value) -> tuple[int, ...]:
    if value is None:
        return ()
    return tuple(v for v in (value if isinstance(value, list) else [value]) if isinstance(v, int))


_ACRONYM = re.compile(r"(?:[^\W\d_]\.)+[^\W\d_]?")  # w.l. / s.w.a.t / a.i.


def _title_word(word: str) -> str:
    """Title case for one word; a dotted acronym keeps every letter upper case (W.L., S.W.A.T.)."""
    return word.upper() if _ACRONYM.fullmatch(word) else word[:1].upper() + word[1:]


def _is_junk(title: str, junk: set[str]) -> bool:
    tokens = [t for t in _TOKEN_SPLIT.split(title.casefold()) if t]
    return not tokens or all(t in junk for t in tokens) or bool(_DOMAIN.fullmatch(title.strip()))


def apply_name_rules(name: str, cfg: dict, trace: list[str]) -> str:
    for rule in cfg["parse"]["name_rules"]:
        new = re.sub(rule["match"], rule["replace"], name)
        if new != name:
            trace.append(f"name rule {rule['match']!r}: {name!r} -> {new!r}")
            name = new
    return name


def parse_name(name: str, cfg: dict, trace: list[str]) -> Parsed:
    """Parse one folder or file name. Every step that changes something adds a line to `trace`."""
    name = apply_name_rules(name, cfg, trace)
    guess = guessit(name)
    title = guess.get("title")
    title = title if isinstance(title, str) else None
    kind = "series" if guess.get("type") == "episode" else "movie"
    year = guess.get("year") if isinstance(guess.get("year"), int) else None
    trace.append(f"guessit {name!r}: title={title!r} year={year} type={guess.get('type')}")
    junk = {t.casefold() for t in cfg["parse"]["junk_tokens"] + cfg["parse"]["extra_tokens"]}
    if title is not None and _is_junk(title, junk):
        trace.append(f"title {title!r} is only release words or a web address: no title")
        title = None
    if title is not None:
        for rule in cfg["parse"]["title_rules"]:
            new = re.sub(rule["match"], rule["replace"], title)
            if new != title:
                trace.append(f"title rule {rule['match']!r}: {title!r} -> {new!r}")
                title = new
        if title in (title.lower(), title.upper()):
            new = " ".join(_title_word(w) for w in title.lower().split(" "))
            if new != title:
                trace.append(f"title case: {title!r} -> {new!r}")
                title = new
        title = " ".join(title.split()) or None
    episode_title = guess.get("episode_title") if isinstance(guess.get("episode_title"), str) else None
    seasons = _ints(guess.get("season"))
    return Parsed(title=title, year=year, kind=kind, season=seasons[0] if seasons else None,
                  episodes=_ints(guess.get("episode")), episode_title=episode_title)


def find_override(rel: str, cfg: dict) -> dict | None:
    """The first [[override]] whose match hits `rel` (glob, or regex with a "re:" prefix)."""
    for override in cfg["override"]:
        match = override["match"]
        hit = re.search(match[3:], rel) if match.startswith("re:") else fnmatch.fnmatchcase(rel, match)
        if hit:
            return override
    return None


def decide_movie(folder: Parsed | None, file: Parsed, override: dict | None, cfg: dict,
                 trace: list[str]) -> MovieName:
    """Combine the folder's and the file's reading, then apply an override."""
    first, second = (folder, file) if folder is not None and cfg["parse"]["folder_beats_file"] else (file, folder)
    second = second or Parsed(None, None, "movie", None, (), None)
    kind = "series" if "series" in (first.kind, second.kind) else "movie"
    full = [p for p in (first, second) if p.title and p.year is not None]
    if full:  # title and year always come together from one name: the first that has both
        title, year = full[0].title, full[0].year
        if len(full) == 2 and (first.title.casefold(), first.year) != (second.title.casefold(), second.year):
            which = "folder" if full[0] is folder else "file"
            msg = (f"folder and file names disagree ({first.title!r} {first.year} vs "
                   f"{second.title!r} {second.year}); using the {which}")
            findings = [("info", "parse.folder_file_disagree", msg)]
            trace.append(f"folder and file disagree; {which} wins")
        else:
            findings = []
    else:
        title, year = first.title or second.title, first.year if first.year is not None else second.year
        findings = []
        if title and year is not None:
            findings = [("warn", "parse.mixed_names", f"title {title!r} and year {year} come from different "
                                                     "names; check them")]
    out = MovieName(title=title, year=year, kind=kind, findings=findings)
    if override is not None:
        if override.get("ignore"):
            trace.append(f"override {override['match']!r}: ignore")
            out.ignore = True
            return out
        changed = {k: override[k] for k in ("title", "year", "kind") if k in override}
        out.title, out.year, out.kind = (changed.get("title", out.title), changed.get("year", out.year),
                                         changed.get("kind", out.kind))
        detail = ", ".join(f"{k}={v!r}" for k, v in changed.items())
        out.findings.append(("info", "parse.override", f"override {override['match']!r}: {detail}"))
        trace.append(f"override {override['match']!r}: {detail}")
    if out.title is None:
        out.review = "no title found"
    elif out.year is None:
        out.review = "no year found"
    return out
