"""The series planner: one source candidate (season pack, show folder or loose episode) -> one item."""

from __future__ import annotations

import os
import re
import stat
from collections import Counter
from dataclasses import dataclass

from guessit import guessit

from tidyshelf import parse
from tidyshelf.episodes import Episode, parse_episode, resolve_numbering, season_from_folder
from tidyshelf.itemops import (PlanState, Planned, _finish, _real, analyse_subs, check_quality, dropped_junk,
                               emit_ops, subtitle_timing)
from tidyshelf.naming import episodes_text, render, safe_name, subtitle_stem
from tidyshelf.plan import Item, file_stat
from tidyshelf.scan import Candidate

_HAVE = re.compile(r"(?i)S(\d{1,3})E(\d{1,3})(?:-?E(\d{1,3}))?")
# A show folder: "<Title>", "<Title> (<Year>)", either followed by bracket tags ("[ITA]").
_SHOW_NAME = re.compile(r"(?P<title>\S.*?)(?: \((?P<year>\d{4})\))?(?P<tags>(?: \[[^\]]+\])*)")
_TAG = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class Show:
    name: str  # the folder name
    year: int | None
    tags: tuple[str, ...]  # casefolded


def _show(name: str) -> tuple[str, Show]:
    m = _SHOW_NAME.fullmatch(name)
    return m["title"].casefold(), Show(name, int(m["year"]) if m["year"] else None,
                                       tuple(t.casefold() for t in _TAG.findall(m["tags"])))


def series_index(cfg: dict) -> dict[str, list[Show]]:
    """casefold(show title without year and tags) -> the library's show folders with that title."""
    root, index = cfg["roots"]["series"], {}
    for name in sorted(os.listdir(root)):
        if stat.S_ISDIR(os.lstat(os.path.join(root, name)).st_mode):
            key, show = _show(name)
            index.setdefault(key, []).append(show)
    return index


def find_show(index: dict[str, list[Show]], title: str, year: int | None,
              tags: list[str]) -> tuple[str | None, list[str]]:
    """The library folder for this show: (folder, []) or, when several fit, (None, their names).
    A year or a tag on only one side is no reason to differ; on both sides they must agree."""
    key, written = _show(safe_name(title))  # an [[override]] title may carry its own year or tags
    year = year if year is not None else written.year
    want = tuple(t.casefold() for t in tags) or written.tags
    fits = [s for s in index.get(key, [])
            if (year is None or s.year is None or s.year == year) and (not want or not s.tags or s.tags == want)]
    exact = [s for s in fits if (s.year, s.tags) == (year, want)]
    if exact or len(fits) == 1:
        return (exact or fits)[0].name, []
    return None, sorted(s.name for s in fits)


def _is_language(tag: str) -> bool:
    guess = guessit(f"Show [{tag}]")
    return bool(guess.get("language") or guess.get("subtitle_language"))


def show_tags(name: str, title: str, cfg: dict) -> list[str]:
    """Bracket tags of a source name to keep in the show folder. A name already shaped like a show
    folder ("<Title> [ITA]", "<Title> (<Year>) [Studio Cut]") keeps its tags as they are, as a movie
    folder does; any other name keeps only its language tags ("Show.[ITA].S01" -> "[ITA]")."""
    junk = {t.casefold() for t in cfg["parse"]["junk_tokens"] + cfg["parse"]["extra_tokens"]}
    m = _SHOW_NAME.fullmatch(name)
    if m and m["tags"] and m["title"].casefold() == title.casefold():
        tags = _TAG.findall(m["tags"])
        if not any(w in junk for t in tags for w in re.split(r"[\s._-]+", t.casefold()) if w):
            return tags
    return [t for t in _TAG.findall(name) if _is_language(t)]


def show_folder(title: str, year: int | None, tags: list[str]) -> str:
    return safe_name(title + (f" ({year})" if year else "") + "".join(f" [{t}]" for t in tags))


def _existing_episodes(cfg: dict, season_ref: str) -> set[tuple[int, int]]:
    folder, have = _real(cfg, season_ref), set()
    if not os.path.isdir(folder) or os.path.islink(folder):
        return have
    for name in os.listdir(folder):
        m = _HAVE.search(name)
        if m:
            first = int(m[2])
            have.update((int(m[1]), e) for e in range(first, int(m[3] or first) + 1))
    return have


def _season_dir(cfg: dict, season: int) -> str:
    naming = cfg["naming"]
    return naming["specials_folder"] if season == 0 else safe_name(render(naming["season_folder"], {"season": season}))


def _label(e: Episode) -> str:
    return f"S{e.season:02}" + "".join(f"E{n:02}" for n in e.episodes)


def plan_series(cand: Candidate, cfg: dict, state: PlanState, trace: list[str], override: dict | None,
                folder_p: parse.Parsed | None) -> Planned:
    item = Item("", "series", "ready", cand.ref)
    left = [("info", "scan.left", f"{r.split(':', 1)[1]} left untouched ({why})") for r, why in cand.left]
    findings: list = []
    review = lambda msg, code: Planned(_finish(item, findings + left, review=msg, code=code), trace)  # noqa: E731

    # Episodes.
    default_season = override.get("season") if override else None
    if default_season is None and folder_p is not None:
        default_season = folder_p.season
    eps = []
    for ref in cand.videos:
        rel = ref.split(":", 1)[1]
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        folder_season = season_from_folder(os.path.basename(parent)) if parent and parent != cand.ref.split(":", 1)[1] else None
        eps.append(parse_episode(os.path.basename(rel), ref, file_stat(_real(cfg, ref))[0], folder_season, cfg, trace))
    eps, more, numbering_review = resolve_numbering(eps, default_season)
    findings += more
    missing = [e.ref.rsplit("/", 1)[-1] for e in eps if not e.episodes]
    if missing:
        return review(f"videos without an episode number: {', '.join(missing)}", "series.no_episode")
    seen: dict[tuple, str] = {}
    for e in eps:
        for n in e.episodes:
            other = seen.setdefault((e.season, n), e.ref)
            if other != e.ref:
                return review(f"two videos claim S{e.season:02}E{n:02}: {other.rsplit('/', 1)[-1]} and "
                              f"{e.ref.rsplit('/', 1)[-1]}", "series.duplicate")
    if numbering_review:
        return review(numbering_review, "series.numbering")
    qfindings, qreview, probes = check_quality([e.ref for e in eps], cfg, state, trace)
    findings += qfindings

    # The show: title, year and tags from the name the title comes from (folder, else the files).
    title = (override or {}).get("title") or (folder_p.title if folder_p and folder_p.title else None)
    if title is None:
        shows = Counter(e.show for e in eps if e.show)
        title = shows.most_common(1)[0][0] if shows else None
    if not title or not safe_name(title):
        return review("no show title found", "parse.no_title")
    same = [e for e in eps if e.show and e.show.casefold() == title.casefold()]
    year = (override or {}).get("year") or (folder_p.year if folder_p else None)
    if year is None:
        years = {e.year for e in same}
        year = years.pop() if len(years) == 1 else None
    folder_name = os.path.basename(cand.path)
    if folder_p is not None and folder_p.title and not (override or {}).get("title"):
        tags = show_tags(folder_name, title, cfg)
    else:
        file_tags = {tuple(show_tags(os.path.splitext(e.ref.rsplit("/", 1)[-1])[0], title, cfg)) for e in same}
        tags = list(file_tags.pop()) if len(file_tags) == 1 else []
    index = state.series_library
    show, several = find_show(index, title, year, tags)
    if several:
        return review(f"several library shows fit {title!r}: {', '.join(several)}; name the show folder "
                      "with its year or tag, or set it with an [[override]]", "series.ambiguous_show")
    show = show or show_folder(title, year, tags)
    show_ref = f"series:{show}"
    if os.path.isdir(_real(cfg, show_ref)) and not (state.audit and show_ref == cand.ref):
        findings.append(("info", "series.joins_existing", f"joins the existing {show_ref} in the library"))
    trace.append(f"show {show_ref}" + (" (already in the library)" if os.path.isdir(_real(cfg, show_ref)) else ""))

    # Library and in-plan clashes.
    clashes, claimed_by = [], None
    for e in eps:
        season_ref = f"{show_ref}/{_season_dir(cfg, e.season)}"
        own = state.audit and (season_ref + "/").startswith(cand.ref + "/")  # auditing: those files are ours
        have = set() if own else _existing_episodes(cfg, season_ref)
        clashes += [f"S{e.season:02}E{n:02}" for n in e.episodes if (e.season, n) in have]
        for n in e.episodes:
            claimed_by = claimed_by or state.claimed.get(f"{show_ref.casefold()}|{e.season}|{n}")
    if clashes:
        msg = f"{show_ref} already has {', '.join(sorted(set(clashes)))}"
        if cfg["conflicts"]["on_exists"] == "skip":
            return Planned(_finish(item, findings + [("info", "conflict.exists", msg)], "skipped"), trace)
        return review(msg, "conflict.exists")
    if claimed_by:
        return review(f"{claimed_by} in this plan already brings these episodes to {show_ref}", "conflict.in_plan")
    if qreview:
        return review(*qreview)

    # Names, then subtitles per episode.
    moves, targets_dir, stems = [], {}, {}
    for e in eps:
        season_ref = f"{show_ref}/{_season_dir(cfg, e.season)}"
        stem = safe_name(render(cfg["naming"]["episode_file"], {
            "title": title, "season": e.season, "episode": e.episodes[0],
            "episodes": episodes_text(list(e.episodes), cfg["naming"]["episode_join"]),
            "episode_title": safe_name(e.title) if e.title else None}))
        targets_dir[e.ref], stems[e.ref] = season_ref, stem
        moves.append((e.ref, f"{season_ref}/{stem}{os.path.splitext(e.ref)[1].lower()}"))
        trace.append(f"episode {e.ref.rsplit('/', 1)[-1]} -> {_label(e)} {stem!r}")
    by_ep: dict[str, list[str]] = {e.ref: [] for e in eps}
    junk = [(ref, "junk") for ref in cand.junk]
    for sub in cand.subs:
        name = sub.rsplit("/", 1)[-1]
        rel = sub.split(":", 1)[1]
        parent = os.path.basename(rel.rsplit("/", 1)[0]) if "/" in rel else ""
        s = parse_episode(name, sub, 0, season_from_folder(parent), cfg, [])
        season = s.season if s.season is not None else (s.folder_season if s.folder_season is not None
                                                         else default_season)
        owner = next((e.ref for e in eps if s.episodes and e.episodes[0] == s.episodes[0]
                      and season in (None, e.season)), None)
        owner = owner or next((e.ref for e in eps
                               if name.startswith(os.path.splitext(e.ref.rsplit("/", 1)[-1])[0] + ".")), None)
        owner = owner or (eps[0].ref if len(eps) == 1 else None)
        if owner is None:
            junk.append((sub, "subtitle without episode"))
            findings.append(("info", "subtitle.no_episode", f"{rel} matches no episode"))
        else:
            by_ep[owner].append(sub)
    base_dir = cand.path if cand.is_dir else os.path.dirname(cand.path)
    for e in eps:
        if not by_ep[e.ref]:
            continue
        choice = analyse_subs(sorted(by_ep[e.ref]), cfg, base_dir, trace, e.ref.rsplit("/", 1)[-1])
        findings += choice.findings
        findings += subtitle_timing(choice, probes[e.ref], lambda ref: os.path.relpath(_real(cfg, ref), base_dir))
        if choice.review:
            return review(f"{_label(e)}: {choice.review}", "subtitle.unknown_language")
        for s, lang, variant in choice.keep:
            sub_stem = subtitle_stem(cfg, stems[e.ref], lang, variant)
            moves.append((s.ref, f"{targets_dir[e.ref]}/{sub_stem}{s.ext}"))
            if s.pair:
                moves.append((s.pair, f"{targets_dir[e.ref]}/{sub_stem}.sub"))
        junk += dropped_junk(choice)
    targets = [to for _f, to in moves]
    if len(set(t.casefold() for t in targets)) != len(targets):
        return review("two files would get the same name", "name.collision")

    created = [] if os.path.isdir(_real(cfg, show_ref)) else [show_ref]
    for season_ref in sorted(set(targets_dir.values())):
        if not os.path.isdir(_real(cfg, season_ref)):
            created.append(season_ref)
    findings += emit_ops(item, cand, cfg, state, moves, junk, created, trace)
    for e in eps:
        for n in e.episodes:
            state.claimed[f"{show_ref.casefold()}|{e.season}|{n}"] = cand.ref
    key, new = _show(show)
    if new not in index.get(key, []):  # later items of this plan join the show this one creates
        index.setdefault(key, []).append(new)
    return Planned(_finish(item, findings + left), trace)
