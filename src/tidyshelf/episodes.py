"""Season and episode numbers from names, including the odd styles in docs/NAMING.md."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from tidyshelf import parse

ASSUMED_SEASON_MAX = 26  # more episodes than this with season 1 only assumed: ask
_MARKER = re.compile(r"(?i)(?:^|(?<=[ ._-]))S(\d{1,2})([A-DF-Z])(\d{1,3})(?=[ ._-]|$)")
_BARE = re.compile(r"(?:^|(?<=[ ._-]))(\d{3})(?=[ ._-]|$)")
_EXPLICIT = re.compile(r"(?i)S\d{1,3}[ ._-]?E\d|(?:^|[^\d])\d{1,2}x\d{1,3}")
_SEASON_DIR = re.compile(r"(?i)(?:season|series|staffel|temporada|stagione|saison)[ ._-]*(\d{1,3})|s(\d{1,3})")
_SPECIALS = {"specials", "special", "extras specials"}


@dataclass
class Episode:
    ref: str
    season: int | None
    episodes: tuple[int, ...]
    title: str | None  # the episode's own title
    show: str | None  # the show's title, as read from this file's name
    size: int
    folder_season: int | None = None  # from the season folder the file sits in
    marker: int | None = None  # S1M1-style marker number
    bare: str | None = None  # a bare 3-digit number as written ("102", "096")


def season_from_folder(name: str) -> int | None:
    """Season of a folder named like "Season 4", "S04" or "Specials"; None for anything else."""
    text = name.strip()
    if text.casefold() in _SPECIALS:
        return 0
    m = _SEASON_DIR.fullmatch(text)
    return int(m[1] or m[2]) if m else None


def parse_episode(name: str, ref: str, size: int, folder_season: int | None, cfg: dict,
                  trace: list[str]) -> Episode:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = parse.apply_name_rules(stem, cfg, trace)
    marker = _MARKER.search(stem) if not _EXPLICIT.search(stem) else None
    if marker:
        rest = (stem[:marker.start()] + stem[marker.end():]).strip(" ._-")
        p = parse.parse_name(rest, cfg, trace) if rest else None
        trace.append(f"marker {marker[0]!r}: season {int(marker[1])}, position {int(marker[3])}")
        return Episode(ref, int(marker[1]), (), p.episode_title if p else None, p.title if p else None, size,
                       folder_season, marker=int(marker[3]))
    p = parse.parse_name(name, cfg, trace)
    bare = _BARE.search(stem) if not _EXPLICIT.search(stem) else None
    return Episode(ref, p.season, p.episodes, p.episode_title, p.title, size, folder_season,
                   bare=bare[1] if bare else None)


def resolve_numbering(eps: list[Episode], default_season: int | None) -> tuple[list[Episode], list, str | None]:
    """Settle seasons and episode numbers for one item. Returns (episodes, findings, review reason)."""
    findings, review = [], None
    bare = [e for e in eps if e.bare]
    absolute = any(e.bare.startswith("0") for e in bare)
    for e in bare:  # one rule per item: guessit alone splits 096 into S00E96
        if absolute:
            e.season, e.episodes = None, (int(e.bare),)
        else:
            e.season, e.episodes = int(e.bare[0]), (int(e.bare[1:]),)
    if absolute and len(bare) >= 3:
        mid_n = statistics.median(int(e.bare) for e in bare)
        mid_size = statistics.median(e.size for e in bare)
        for e in bare:
            if int(e.bare) > 3 * mid_n and e.size < 0.5 * mid_size:
                e.season = 0
                name = e.ref.rsplit("/", 1)[-1]
                review = f"{name}: episode {int(e.bare)} is far outside the show's numbering; planned as a special"
    by_season: dict[int, list[Episode]] = {}
    for e in eps:
        if e.marker is not None:
            by_season.setdefault(e.season, []).append(e)
    for season, group in by_season.items():
        for n, e in enumerate(sorted(group, key=lambda x: x.marker), 1):
            e.episodes = (n,)
        findings.append(("info", "series.renumbered",
                         f"season {season}: {len(group)} files with unrecognised markers numbered in their order"))
    assumed = 0
    for e in eps:
        if e.season is None:
            e.season = e.folder_season if e.folder_season is not None else default_season
            if e.season is None:
                e.season, assumed = 1, assumed + 1
    if assumed and len(eps) > ASSUMED_SEASON_MAX and review is None:
        review = "numbering suggests more than one season"
    return eps, findings, review
