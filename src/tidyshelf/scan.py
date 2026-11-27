"""Finding what to plan: candidates in the source root, and the movies already in the library.

Read-only, lstat only: a symlink is never followed, a special file never opened.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field

import tidyshelf_apply
from tidyshelf.naming import MOVIE_FOLDER_RE

SKIP_TOP = (tidyshelf_apply.TRASH_DIR, tidyshelf_apply.LOCK_NAME)


@dataclass
class Candidate:
    ref: str
    path: str
    is_dir: bool
    videos: list[str] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    junk: list[str] = field(default_factory=list)
    left: list[tuple[str, str]] = field(default_factory=list)  # (ref, reason): never touched
    dirs: list[str] = field(default_factory=list)  # sub-folders, deepest first
    error: str | None = None  # set when part of it could not be read
    samples: list = field(default_factory=list, repr=False)  # (ref, size) of sample-named videos, during the scan


class _Rules:
    def __init__(self, cfg: dict):
        self.videos = {e.lower() for e in cfg["video"]["extensions"]}
        self.subs = {e.lower() for e in cfg["subtitles"]["extensions"]}
        self.junk = [re.compile(p) for p in cfg["junk"]["patterns"]]
        self.exclude = [re.compile(p) for p in cfg["exclude"]["patterns"]]
        self.extras = {name.casefold() for name in cfg["parse"]["extras_folders"]}

    def excluded(self, ref: str) -> bool:
        return any(p.search(ref) for p in self.exclude)

    def kind(self, name: str) -> str:
        ext = os.path.splitext(name)[1].lower()
        if ext in self.videos:
            return "sample" if any(p.search(name) for p in self.junk) else "video"
        return "sub" if ext in self.subs else "junk"


def _skip_top(name: str) -> bool:
    return name in SKIP_TOP or name.endswith(".journal.jsonl")


def _walk(cand: Candidate, path: str, ref: str, rules: _Rules) -> None:
    for name in sorted(os.listdir(path)):
        full, child = os.path.join(path, name), f"{ref}/{name}"
        mode = os.lstat(full).st_mode
        if rules.excluded(child):
            cand.left.append((child, "excluded"))
        elif stat.S_ISLNK(mode):
            cand.left.append((child, "symlink"))
        elif stat.S_ISDIR(mode):
            if name.casefold() in rules.extras:
                cand.left.append((child, "extras folder"))
                continue
            cand.dirs.append(child)
            _walk(cand, full, child, rules)
        elif not stat.S_ISREG(mode):
            cand.left.append((child, "special file"))
        else:
            _add(cand, rules.kind(name), child, full)


def _add(cand: Candidate, kind: str, ref: str, path: str) -> None:
    if kind == "sample":
        cand.samples.append((ref, os.lstat(path).st_size))
    else:
        {"video": cand.videos, "sub": cand.subs, "junk": cand.junk}[kind].append(ref)


def _settle_samples(cand: Candidate, root: str) -> None:
    """A video matching a junk pattern ("sample") is junk only next to a video at least as large;
    otherwise it is the movie (a title can contain the word)."""
    sizes = [os.lstat(os.path.join(root, v.split(":", 1)[1])).st_size for v in cand.videos]
    for ref, size in cand.samples:
        (cand.junk if any(s >= size for s in sizes) else cand.videos).append(ref)


def _candidate(root: str, rel: str, rules: _Rules, root_name: str = "source") -> Candidate:
    try:
        return _candidate_unchecked(root, rel, rules, root_name)
    except OSError as exc:
        name = os.path.relpath(exc.filename, root) if isinstance(exc.filename, str) else rel
        return Candidate(f"{root_name}:{rel}", os.path.join(root, rel), True,
                         error=f"cannot read {name}: {exc.strerror or exc}")


def _candidate_unchecked(root: str, rel: str, rules: _Rules, root_name: str = "source") -> Candidate:
    ref, path = f"{root_name}:{rel}", os.path.join(root, rel)
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        return Candidate(ref, path, False, left=[(ref, "symlink" if stat.S_ISLNK(mode) else "special file")])
    if stat.S_ISREG(mode):
        cand = Candidate(ref, path, False)
        _add(cand, rules.kind(os.path.basename(rel)), ref, path)
        _settle_samples(cand, root)
        return cand
    cand = Candidate(ref, path, True)
    _walk(cand, path, ref, rules)
    _settle_samples(cand, root)
    for bucket in (cand.videos, cand.subs, cand.junk, cand.left):
        bucket.sort()
    cand.dirs.sort(key=lambda d: (-d.count("/"), d))
    return cand


def _attach_sidecars(cands: list[Candidate], root: str, rules: _Rules) -> list[Candidate]:
    """A loose video takes the loose subtitles named after it (Film.mkv + Film.en.srt)."""
    stems = {os.path.splitext(c.videos[0].split(":", 1)[1])[0]: c for c in cands if not c.is_dir and c.videos}
    out = []
    for c in cands:
        name = c.ref.split(":", 1)[1]
        if not c.is_dir and c.subs and not c.videos and "/" not in name:
            owners = [s for s in stems if name.startswith(s + ".")]
            if owners:
                stems[max(owners, key=len)].subs.append(c.ref)
                continue
        out.append(c)
    for c in out:
        c.subs.sort()
    return out


def _resolve(root: str, path: str) -> str:
    """A ref or a real path to a relative path inside the source root; ValueError otherwise."""
    if path.startswith("source:"):
        try:
            return tidyshelf_apply.split_ref(path)[1]
        except tidyshelf_apply.PlanError as exc:
            raise ValueError(f"{path}: not a path inside the source root ({exc})") from None
    real, base = os.path.realpath(path), os.path.realpath(root)
    if ":" in path.split("/", 1)[0] or os.path.commonpath([real, base]) != base or real == base:
        raise ValueError(f"{path}: not a path inside the source root")
    return os.path.relpath(real, base)


def scan_source(cfg: dict, paths: list[str] | None = None) -> list[Candidate]:
    """Candidates of the source root, or the ones holding `paths` (a path inside a folder selects the
    folder; a loose subtitle selects the video it belongs to)."""
    root, rules = cfg["roots"]["source"], _Rules(cfg)
    top = [n for n in sorted(os.listdir(root)) if not _skip_top(n) and not rules.excluded(f"source:{n}")]
    if not paths:
        return _attach_sidecars([_candidate(root, n, rules) for n in top], root, rules)
    wanted = set()
    for p in paths:
        rel = _resolve(root, p)
        if not os.path.lexists(os.path.join(root, rel)):
            raise ValueError(f"{p}: not found in the source root")
        wanted.add(rel.split("/", 1)[0])
    loose = {n for n in top if os.path.isfile(os.path.join(root, n)) and not os.path.islink(os.path.join(root, n))}
    cands = _attach_sidecars([_candidate(root, n, rules) for n in top if n in wanted or n in loose], root, rules)
    refs = {f"source:{n}" for n in wanted}
    return [c for c in cands if c.ref in refs or any(s in refs for s in c.subs)]


def scan_root(cfg: dict, root_name: str) -> list[Candidate]:
    """Candidates of a library root, for --audit: each direct child, like the source root."""
    root, rules = cfg["roots"][root_name], _Rules(cfg)
    top = [n for n in sorted(os.listdir(root)) if not _skip_top(n) and not rules.excluded(f"{root_name}:{n}")]
    return _attach_sidecars([_candidate(root, n, rules, root_name) for n in top], root, rules)


def library_index(cfg: dict) -> dict[str, str]:
    """casefold("<Title> (<Year>)") -> movies ref, for each library folder that follows the standard."""
    root, index = cfg["roots"]["movies"], {}
    for name in sorted(os.listdir(root)):
        if not stat.S_ISDIR(os.lstat(os.path.join(root, name)).st_mode):
            continue
        m = MOVIE_FOLDER_RE.fullmatch(name)
        if m:
            index.setdefault(f"{m['title']} ({m['year']})".casefold(), f"movies:{name}")
    return index
