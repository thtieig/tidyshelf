"""The movie planner: one source candidate becomes one plan item (PLAN §7, docs/NAMING.md)."""

from __future__ import annotations

import os
import re

from tidyshelf import episodes, parse, quality, series
from tidyshelf.itemops import (PlanState, Planned, _finish, _real, _video_bytes, analyse_subs, check_quality,
                               dropped_junk, emit_ops, subtitle_timing)
from tidyshelf.naming import MOVIE_FOLDER_RE, movie_names, safe_name, subtitle_stem
from tidyshelf.plan import Item, Plan, file_stat
from tidyshelf.scan import Candidate, library_index, scan_source

def plan_candidate(cand: Candidate, cfg: dict, state: PlanState) -> Planned:
    """One item for one candidate. Its id is set by build_plan()."""
    trace: list[str] = []
    rel = cand.ref.split(":", 1)[1]
    item = Item("", "movie", "ready", cand.ref)
    left = [("info", "scan.left", f"{r.split(':', 1)[1]} left untouched ({why})") for r, why in cand.left]
    if cand.error:
        return Planned(_finish(item, left, review=f"{rel}: {cand.error}", code="scan.unreadable"), trace)
    if not cand.videos:
        return Planned(_finish(item, [("info", "movie.no_video", f"{rel}: no video file; left as it is")] + left,
                               "skipped"), trace)
    folder_p = parse.parse_name(os.path.basename(cand.path), cfg, trace) if cand.is_dir else None
    video_ps = [parse.parse_name(os.path.basename(v.split(":", 1)[1]), cfg, trace) for v in cand.videos]
    override = parse.find_override(rel, cfg)
    name = parse.decide_movie(folder_p, video_ps[0], override, cfg, trace)
    if name.ignore:
        return Planned(_finish(item, [], "skipped"), trace)
    forced_kind = override.get("kind") if override else None
    markers = any(episodes.has_marker(os.path.basename(v)) for v in cand.videos)  # S1M1: guessit says "movie"
    if forced_kind == "series" or forced_kind is None and (name.kind == "series" or markers
                                                          or any(p.kind == "series" for p in video_ps)):
        return series.plan_series(cand, cfg, state, trace, override, folder_p)
    findings = list(name.findings)
    if len(cand.videos) > 1:
        return Planned(_finish(item, findings + left, review="several videos"), trace)
    if name.review:
        return Planned(_finish(item, findings + left, review=name.review), trace)

    # Names: keep a folder that already follows the standard (tags included), else build one.
    base = os.path.basename(cand.path)
    m = MOVIE_FOLDER_RE.fullmatch(base) if cand.is_dir else None
    junk = {t.casefold() for t in cfg["parse"]["junk_tokens"] + cfg["parse"]["extra_tokens"]}
    tag_words = [w for w in re.split(r"[\s\[\]()._-]+", m["suffix"].casefold()) if w] if m else []
    if (m and m["title"].casefold() == name.title.casefold() and int(m["year"]) == name.year
            and safe_name(base) == base and not any(w in junk for w in tag_words)):
        folder = stem = base
        trace.append(f"folder {base!r} already follows the standard: kept")
    else:
        folder, stem = movie_names(cfg, name.title, name.year)
    if not folder or not stem or not safe_name(name.title):
        return Planned(_finish(item, findings + left, review="title has no usable characters"), trace)
    lib = f"movies:{folder}"
    trace.append(f"target {lib}")

    qfindings, qreview, probes = check_quality(cand.videos, cfg, state, trace)
    findings += qfindings

    # Conflicts: the library, then earlier items of this plan.
    fm = MOVIE_FOLDER_RE.fullmatch(folder)
    key = (f"{fm['title']} ({fm['year']})" if fm else folder).casefold()  # the name as it will be on disk
    existing = state.library.get(key) or (lib if os.path.lexists(_real(cfg, lib)) else None)
    if existing is not None:
        size = file_stat(_real(cfg, cand.videos[0]))[0]
        msg = (f"the library already has {existing} ({_library_quality(cfg, existing)}); "
               f"this one: {quality.summary(probes[cand.videos[0]]) if probes[cand.videos[0]] else f'{size} bytes'}")
        if cfg["conflicts"]["on_exists"] == "skip":
            return Planned(_finish(item, findings + [("info", "conflict.exists", msg)], "skipped"), trace)
        return Planned(_finish(item, findings + left, review=msg, code="conflict.exists"), trace)
    if key in state.claimed:
        msg = f"{state.claimed[key]} in this plan already goes to {lib}"
        return Planned(_finish(item, findings + left, review=msg, code="conflict.in_plan"), trace)
    if qreview:
        return Planned(_finish(item, findings + left, review=qreview[0], code=qreview[1]), trace)

    # Subtitles.
    base_dir = cand.path if cand.is_dir else os.path.dirname(cand.path)
    choice = analyse_subs(cand.subs, cfg, base_dir, trace, os.path.basename(cand.videos[0].split(":", 1)[1]))
    findings += choice.findings
    findings += subtitle_timing(choice, probes[cand.videos[0]], lambda ref: os.path.relpath(_real(cfg, ref), base_dir))
    if choice.review:
        return Planned(_finish(item, findings + left, review=choice.review), trace)

    # Operations.
    subs_dir = f"{lib}/{cfg['naming']['movie_subs_folder']}"
    video = cand.videos[0]
    moves = [(video, f"{lib}/{stem}{os.path.splitext(video)[1].lower()}")]
    for s, lang, variant in choice.keep:
        sub_stem = subtitle_stem(cfg, stem, lang, variant)
        moves.append((s.ref, f"{subs_dir}/{sub_stem}{s.ext}"))
        if s.pair:
            moves.append((s.pair, f"{subs_dir}/{sub_stem}.sub"))
    targets = [to for _f, to in moves]
    if len(set(targets)) != len(targets):
        return Planned(_finish(item, findings + left, review="two subtitles would get the same name",
                               code="subtitle.collision"), trace)
    junk = [(ref, "junk") for ref in cand.junk] + dropped_junk(choice)
    created = [lib] + ([subs_dir] if choice.keep else [])
    emit_ops(item, cand, cfg, state, moves, junk, created, trace)
    state.claimed[key] = cand.ref
    return Planned(_finish(item, findings + left), trace)


def _library_quality(cfg: dict, lib_ref: str) -> str:
    """Quality of the video already in a library folder, or its size when it can't be probed."""
    folder = _real(cfg, lib_ref)
    videos = {e.lower() for e in cfg["video"]["extensions"]}
    if os.path.isdir(folder) and not os.path.islink(folder):
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.splitext(name)[1].lower() in videos and os.path.isfile(path) and not os.path.islink(path):
                p = quality.probe(path, cfg)
                if p is not None:
                    return quality.summary(p)
    return f"{_video_bytes(cfg, lib_ref)} bytes of video"


def build_plan(cfg: dict, paths: list[str] | None = None, created: str | None = None,
               deep: bool = False) -> tuple[Plan, dict]:
    """Plan every candidate (or those in `paths`). Returns the plan and a trace per item id."""
    plan, traces, counters = Plan(created), {}, {}
    state = PlanState(library=library_index(cfg), series_library=series.series_index(cfg), deep=deep)
    for cand in scan_source(cfg, paths):
        try:
            planned = plan_candidate(cand, cfg, state)
        except OSError as exc:  # e.g. a subtitle that cannot be read: report, keep planning the rest
            what = os.path.basename(exc.filename) if isinstance(exc.filename, str) else "a file"
            item = Item("", "movie", "ready", cand.ref)
            planned = Planned(_finish(item, [], review=f"cannot read {what}: {exc.strerror or exc}",
                                      code="scan.unreadable"), [])
        item = planned.item
        counters[item.kind] = counters.get(item.kind, 0) + 1
        item.id = f"{item.kind}-{counters[item.kind]:04}"
        plan.items.append(item)
        traces[item.id] = planned.trace
    if state.quality_unchecked:
        plan.warnings.append("ffprobe is not available: video quality was not checked")
    plan.validate()
    return plan, traces
