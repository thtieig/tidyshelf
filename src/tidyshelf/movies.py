"""The movie planner: one source candidate becomes one plan item (PLAN §7, docs/NAMING.md)."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field

from tidyshelf import parse, subtitles
from tidyshelf.naming import MOVIE_FOLDER_RE, movie_names, safe_name, subtitle_stem
from tidyshelf.plan import Item, Plan, file_stat, trash_ref
from tidyshelf.scan import Candidate, library_index, scan_source

REVIEW_CODES = {
    "no title found": "parse.no_title",
    "no year found": "parse.no_year",
    "several videos": "movie.several_videos",
    "title has no usable characters": "name.unusable",
    "several subtitles with no recognisable language": "subtitle.unknown_language",
}


@dataclass
class PlanState:
    """What earlier items of the same plan already took."""
    library: dict[str, str]
    claimed: dict[str, str] = field(default_factory=dict)  # library key -> source ref
    trash_used: set[str] = field(default_factory=set)


@dataclass
class Planned:
    item: Item
    trace: list[str]


def _real(cfg: dict, ref: str) -> str:
    root, rel = ref.split(":", 1)
    return os.path.join(cfg["roots"][root], rel)


def _video_bytes(cfg: dict, lib_ref: str) -> int:
    folder, total = _real(cfg, lib_ref), 0
    if not stat.S_ISDIR(os.lstat(folder).st_mode):
        return 0
    videos = {e.lower() for e in cfg["video"]["extensions"]}
    for name in sorted(os.listdir(folder)):
        st = os.lstat(os.path.join(folder, name))
        if os.path.splitext(name)[1].lower() in videos and not os.path.islink(os.path.join(folder, name)):
            total += st.st_size
    return total


def _trash_target(cfg: dict, ref: str, state: PlanState) -> str:
    target = trash_ref(ref)
    stem, ext = os.path.splitext(target)
    n = 0
    while target in state.trash_used or os.path.lexists(_real(cfg, target)):
        n += 1
        target = f"{stem}.{n}{ext}"
    state.trash_used.add(target)
    return target


def _finish(item: Item, findings: list, status: str | None = None, review: str | None = None,
            code: str | None = None) -> Item:
    for level, c, msg in findings:
        item.finding(level, c, msg)
    if review is not None:
        item.finding("warn", code or REVIEW_CODES.get(review, "review"), review)
        item.status = "needs_review"
    elif status is not None:
        item.status = status
    return item


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
    if forced_kind == "series" or forced_kind is None and (name.kind == "series"
                                                          or any(p.kind == "series" for p in video_ps)):
        item.kind = "series"
        return Planned(_finish(item, [("info", "series.not_yet", f"{rel}: looks like a series; series "
                                       "planning is not available yet, left as it is")], "skipped"), trace)
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

    # Conflicts: the library, then earlier items of this plan.
    fm = MOVIE_FOLDER_RE.fullmatch(folder)
    key = (f"{fm['title']} ({fm['year']})" if fm else folder).casefold()  # the name as it will be on disk
    existing = state.library.get(key) or (lib if os.path.lexists(_real(cfg, lib)) else None)
    if existing is not None:
        size = file_stat(_real(cfg, cand.videos[0]))[0]
        msg = (f"the library already has {existing} ({_video_bytes(cfg, existing)} bytes of video); "
               f"this one is {size} bytes")
        if cfg["conflicts"]["on_exists"] == "skip":
            return Planned(_finish(item, findings + [("info", "conflict.exists", msg)], "skipped"), trace)
        return Planned(_finish(item, findings + left, review=msg, code="conflict.exists"), trace)
    if key in state.claimed:
        msg = f"{state.claimed[key]} in this plan already goes to {lib}"
        return Planned(_finish(item, findings + left, review=msg, code="conflict.in_plan"), trace)

    # Subtitles.
    base_dir = cand.path if cand.is_dir else os.path.dirname(cand.path)
    rel_of = lambda ref: os.path.relpath(_real(cfg, ref), base_dir)  # noqa: E731
    infos = [subtitles.analyse(_real(cfg, ref), ref, cfg) for ref in cand.subs]
    choice = subtitles.choose(infos, cfg, rel_of)
    findings += choice.findings
    kept_refs = {s.ref: (lang, variant) for s, lang, variant in choice.keep}
    for s in infos:
        det = s.detection
        seen = f"detected={det.lang} ({det.confidence:.2f}, {det.encoding})" if det else "image subtitle"
        outcome = (f"keep as {'/'.join(x for x in kept_refs[s.ref] if x)}" if s.ref in kept_refs
                   else next((f"drop ({why})" for d, why in choice.drop if d.ref == s.ref), "moves with its .idx"))
        trace.append(f"subtitle {rel_of(s.ref)}: claimed={s.claimed} {seen} variant={s.variant} -> {outcome}")
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
    junk = [(ref, "junk") for ref in cand.junk]
    for s, why in choice.drop:
        junk.append((s.ref, f"subtitle {why}"))
        if s.pair and why != "orphan":
            junk.append((s.pair, f"subtitle {why}"))
    junk.sort()
    trash = cfg["junk"]["junk_action"] == "trash"
    trash_moves = [(ref, _trash_target(cfg, ref, state)) for ref, _why in junk] if trash else []
    created = [lib] + ([subs_dir] if choice.keep else [])
    item.mkdir(created[-1])
    parents = sorted({to.rsplit("/", 1)[0] for _f, to in trash_moves})
    for parent in parents:
        if not any(other.startswith(parent + "/") for other in parents):
            item.mkdir(parent)
    for src, dst in moves + trash_moves:
        item.move(src, dst, *file_stat(_real(cfg, src)))
        trace.append(f"move {src.split(':', 1)[1]} -> {dst}")
    for path in created + targets:
        item.set_mode(path)
    if not trash:
        for ref, why in junk:
            item.delete_file(ref, *file_stat(_real(cfg, ref)), reason=why)
    left_refs = [r for r, _why in cand.left]
    if cand.is_dir:
        for d in cand.dirs + [cand.ref]:
            if not any(r == d or r.startswith(d + "/") for r in left_refs):
                item.delete_dir(d, reason="empty")
    state.claimed[key] = cand.ref
    return Planned(_finish(item, findings + left), trace)


def build_plan(cfg: dict, paths: list[str] | None = None, created: str | None = None) -> tuple[Plan, dict]:
    """Plan every candidate (or those in `paths`). Returns the plan and a trace per item id."""
    plan, traces, counters = Plan(created), {}, {}
    state = PlanState(library=library_index(cfg))
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
    plan.validate()
    return plan, traces
