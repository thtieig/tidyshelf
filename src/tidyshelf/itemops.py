"""Building one plan item's operations; shared by the movie and series planners."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field

from tidyshelf import subtitles
from tidyshelf.plan import Item, file_stat, trash_ref

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


def leaves(paths: list[str]) -> list[str]:
    """The paths that are not a parent of another one (mkdir -p creates the rest)."""
    paths = sorted(set(paths))
    return [p for p in paths if not any(o.startswith(p + "/") for o in paths)]


def analyse_subs(cand_subs: list[str], cfg: dict, base_dir: str, trace: list[str]):
    """Analyse and choose the subtitles in `cand_subs`; adds one trace line per file."""
    rel_of = lambda ref: os.path.relpath(_real(cfg, ref), base_dir)  # noqa: E731
    infos = [subtitles.analyse(_real(cfg, ref), ref, cfg) for ref in cand_subs]
    choice = subtitles.choose(infos, cfg, rel_of)
    kept_refs = {s.ref: (lang, variant) for s, lang, variant in choice.keep}
    for s in infos:
        det = s.detection
        seen = f"detected={det.lang} ({det.confidence:.2f}, {det.encoding})" if det else "image subtitle"
        outcome = (f"keep as {'/'.join(x for x in kept_refs[s.ref] if x)}" if s.ref in kept_refs
                   else next((f"drop ({why})" for d, why in choice.drop if d.ref == s.ref), "moves with its .idx"))
        trace.append(f"subtitle {rel_of(s.ref)}: claimed={s.claimed} {seen} variant={s.variant} -> {outcome}")
    return choice


def dropped_junk(choice) -> list[tuple[str, str]]:
    """(ref, reason) for every subtitle the choice dropped, VobSub partners included."""
    junk = []
    for s, why in choice.drop:
        junk.append((s.ref, f"subtitle {why}"))
        if s.pair and why != "orphan":
            junk.append((s.pair, f"subtitle {why}"))
    return junk


def emit_ops(item: Item, cand, cfg: dict, state: PlanState, moves: list[tuple[str, str]],
             junk: list[tuple[str, str]], created: list[str], trace: list[str]) -> None:
    """mkdir, moves, junk (trash or delete), set_mode, then the emptied source folders, deepest first."""
    junk = sorted(junk)
    trash = cfg["junk"]["junk_action"] == "trash"
    trash_moves = [(ref, _trash_target(cfg, ref, state)) for ref, _why in junk] if trash else []
    for path in leaves(created):
        item.mkdir(path)
    for parent in leaves([to.rsplit("/", 1)[0] for _f, to in trash_moves]):
        item.mkdir(parent)
    for src, dst in moves + trash_moves:
        item.move(src, dst, *file_stat(_real(cfg, src)))
        trace.append(f"move {src.split(':', 1)[1]} -> {dst}")
    for path in created + [to for _f, to in moves]:
        item.set_mode(path)
    if not trash:
        for ref, why in junk:
            item.delete_file(ref, *file_stat(_real(cfg, ref)), reason=why)
    left_refs = [r for r, _why in cand.left]
    if cand.is_dir:
        for d in cand.dirs + [cand.ref]:
            if not any(r == d or r.startswith(d + "/") for r in left_refs):
                item.delete_dir(d, reason="empty")
