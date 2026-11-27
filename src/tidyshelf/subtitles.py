"""Subtitle sidecars: what each file is (by content), and which ones a movie keeps."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Callable

import babelfish

from tidyshelf import textsubs
from tidyshelf.textsubs import READ_LIMIT, Detection

TEXT_EXTS = (".srt", ".ass", ".ssa", ".vtt")
IMAGE_EXTS = (".idx", ".sub")
MIN_CUES = 20  # fewer cues than this in a kept text subtitle is reported
SDH_CUE_SHARE = 0.15  # share of cues that are sound descriptions for content to count as SDH
_HASH_CHUNK = 1024 * 1024
_SOUND_CUE = re.compile(r"^\s*[\[(]")  # [MUSIC] (DOOR CLOSES); lyric lines are not SDH
_TOKENS = re.compile(r"[\s._\-\[\]()]+")
_IDX_ID = re.compile(rb"^id:\s*([A-Za-z]{2,3})\b", re.MULTILINE)

LANG_NAMES = {"en": "English", "es": "Spanish", "it": "Italian", "fr": "French", "de": "German",
              "pt": "Portuguese", "ro": "Romanian", "nl": "Dutch"}
LANG_TAGS: dict[str, str] = {}
for _code, _tags in {
    "en": "en eng english",
    "es": "es spa español espanol spanish castellano",
    "it": "it ita italian italiano",
    "fr": "fr fre fra french français francais",
    "de": "de ger deu german deutsch",
    "pt": "pt por portuguese português portugues",
    "ro": "ro rum ron romanian română romana",
    "nl": "nl dut nld dutch nederlands",
}.items():
    LANG_TAGS.update(dict.fromkeys(_tags.split(), _code))


@dataclass
class SubInfo:
    ref: str
    path: str
    ext: str
    size: int
    mtime: int
    sha256: str
    claimed: str | None
    detection: Detection | None
    variant: str | None
    cues: int
    ad_cues: list[str] = field(default_factory=list)
    pair: str | None = None  # for an .idx: ref of its .sub, set by choose()
    last_cue: float | None = None  # seconds at which the last cue ends


@dataclass
class Choice:
    keep: list = field(default_factory=list)  # (SubInfo, lang, variant)
    drop: list = field(default_factory=list)  # (SubInfo, reason)
    findings: list = field(default_factory=list)  # (level, code, msg)
    review: str | None = None


def _other_language(token: str) -> str | None:
    """An ISO 639-2 code or English language name for a language without a word list."""
    try:
        lang = babelfish.Language.fromalpha3b(token) if len(token) == 3 else babelfish.Language.fromname(
            token.capitalize()) if len(token) >= 5 else None
    except (ValueError, KeyError, babelfish.Error):
        return None
    if lang is None:
        return None
    return getattr(lang, "alpha2", None) or lang.alpha3


def _name_tokens(name: str) -> list[str]:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return [t for t in _TOKENS.split(stem.casefold()) if t]


def _hash(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _head(path: str, size: int) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(size)


def analyse(path: str, ref: str, cfg: dict, video_name: str | None = None) -> SubInfo:
    """Read one subtitle file (bounded) and describe it. Never changes the file."""
    subs = cfg["subtitles"]
    st = os.lstat(path)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    tokens = _name_tokens(name)
    claims = [LANG_TAGS[t] for t in tokens if t in LANG_TAGS]
    claimed = claims[-1] if claims else (_other_language(tokens[-1]) if tokens else None)
    detection, cues, last_cue = None, [], None
    if ext in TEXT_EXTS:
        data = _head(path, READ_LIMIT + 1)
        detection = textsubs.detect(data, ext, textsubs.load_wordlists())
        cues = textsubs.read_cues(data[:READ_LIMIT], ext, detection.encoding)
        codec = "utf-8-sig" if detection.encoding == "utf-8" else detection.encoding
        last_cue = textsubs.last_cue_end(data[:READ_LIMIT].decode(codec, errors="replace"), ext)
    elif ext == ".idx":
        found = _IDX_ID.search(_head(path, 64 * 1024))
        if found:
            claimed = LANG_TAGS.get(found[1].decode("ascii").casefold(), claimed)
    markers = set(tokens) - set(_name_tokens(video_name) if video_name else ())  # "Forced.Entry" is a title
    if markers & {m.casefold() for m in subs["forced_markers"]}:
        variant = "forced"
    elif markers & {m.casefold() for m in subs["sdh_markers"]}:
        variant = "sdh"
    elif cues and sum(bool(_SOUND_CUE.match(c)) for c in cues) / len(cues) >= SDH_CUE_SHARE:
        variant = "sdh"
    else:
        variant = None
    patterns = [re.compile(p) for p in subs["ad_patterns"] + subs["ad_patterns_extra"]]
    edges = cues[:3] + cues[3:][-3:]
    ad_cues = [c for c in edges if any(p.search(c) for p in patterns)]
    return SubInfo(ref=ref, path=path, ext=ext, size=st.st_size, mtime=st.st_mtime_ns // 10**9,
                   sha256=_hash(path), claimed=claimed, detection=detection, variant=variant,
                   cues=len(cues), ad_cues=ad_cues, last_cue=last_cue)


def _rank(sub: SubInfo):
    return (-sub.cues, -sub.size, sub.ref)


def choose(subs: list[SubInfo], cfg: dict, rel_of: Callable[[str], str]) -> Choice:
    """Pick the subtitles a movie keeps (PLAN §7 Subtitles, steps 1-8). Deterministic."""
    conf = cfg["subtitles"]
    junk = [re.compile(p) for p in cfg["junk"]["patterns"]]
    out = Choice()
    subs = sorted(subs, key=lambda s: s.ref)
    idx_by_stem = {s.ref[: -len(s.ext)]: s for s in subs if s.ext == ".idx"}
    candidates = []
    for s in subs:  # 1. orphans; a .sub with its .idx travels with the .idx (pair)
        if s.ext == ".sub" and s.ref[: -len(s.ext)] in idx_by_stem:
            idx_by_stem[s.ref[: -len(s.ext)]].pair = s.ref
    for s in subs:
        if s.ext == ".sub" and s.ref[: -len(s.ext)] in idx_by_stem:
            continue
        lone_idx = s.ext == ".idx" and s.pair is None
        if s.ext == ".sub" or lone_idx or any(p.search(os.path.basename(s.path)) for p in junk):
            out.drop.append((s, "orphan"))
            out.drop.extend((t, "orphan") for t in subs if s.pair and t.ref == s.pair)
            continue
        candidates.append(s)
    first_by_hash: dict[str, SubInfo] = {}
    unique = []
    for s in candidates:  # 2. exact duplicates (sorted by ref, so the first is the smallest)
        if s.sha256 in first_by_hash:
            out.drop.append((s, "duplicate"))
            out.findings.append(("info", "subtitle.duplicate",
                                 f"{rel_of(s.ref)} is identical to {rel_of(first_by_hash[s.sha256].ref)}"))
            continue
        first_by_hash[s.sha256] = s
        unique.append(s)
    langs: dict[str, str | None] = {}
    for s in unique:  # 3. language: confident content first, then the name
        det = s.detection
        if det is not None and det.lang is not None and det.confidence >= conf["min_confidence"]:
            langs[s.ref] = det.lang
            if s.claimed and s.claimed != det.lang:
                out.findings.append(("warn", "subtitle.language_mismatch",
                                     f"{rel_of(s.ref)} is labelled {LANG_NAMES.get(s.claimed, s.claimed)} but its "
                                     f"text is {LANG_NAMES.get(det.lang, det.lang)} (confidence {det.confidence:.2f})"))
        else:
            langs[s.ref] = s.claimed
            if s.claimed and s.ext in TEXT_EXTS:
                out.findings.append(("info", "subtitle.unverified",
                                     f"{rel_of(s.ref)}: language taken from its name ({s.claimed})"))
    unknown = [s for s in unique if langs[s.ref] is None]
    if unknown:  # 4. no language at all: one such file gets the default language
        if len(unknown) == 1:
            langs[unknown[0].ref] = conf["default_language"]
            out.findings.append(("info", "subtitle.default_language",
                                 f"{rel_of(unknown[0].ref)}: no recognisable language; "
                                 f"assumed {conf['default_language']}"))
        else:
            out.review = "several subtitles with no recognisable language"
            return out
    by_lang: dict[str, list[SubInfo]] = {}
    for s in unique:  # 5. languages that are not wanted
        lang = langs[s.ref]
        if lang not in conf["languages"]:
            out.drop.append((s, "language"))
            out.findings.append(("info", "subtitle.dropped_language",
                                 f"{rel_of(s.ref)} is {LANG_NAMES.get(lang, lang)} (not in subtitles.languages)"))
            continue
        by_lang.setdefault(lang, []).append(s)
    for lang in sorted(by_lang):  # 6. per language: one forced, one main (+ SDH alongside)
        group = by_lang[lang]
        forced = sorted((s for s in group if s.variant == "forced"), key=_rank)
        plain = sorted((s for s in group if s.variant is None), key=_rank)
        sdh = sorted((s for s in group if s.variant == "sdh"), key=_rank)
        first, second = (plain, sdh) if conf["prefer"] == "plain" else (sdh, plain)
        chosen = forced[:1] + (first[:1] or second[:1])
        if conf["keep_sdh_alongside"] and plain and sdh:
            chosen = forced[:1] + plain[:1] + sdh[:1]
        for s in group:
            if s in chosen:
                out.keep.append((s, lang, s.variant))
            else:
                out.drop.append((s, "not_preferred"))
    for s, _lang, _variant in out.keep:  # 7. reports on what is kept
        if s.ad_cues:
            out.findings.append(("warn", "subtitle.ad_cues",
                                 f"{rel_of(s.ref)} has advertising cues (reported, not removed): {s.ad_cues[0]!r}"))
        if s.ext in TEXT_EXTS and s.cues < MIN_CUES:
            out.findings.append(("warn", "subtitle.few_cues", f"{rel_of(s.ref)} has only {s.cues} cues"))
        if s.ext == ".idx":
            out.findings.append(("info", "subtitle.unverified",
                                 f"{rel_of(s.ref)} is an image subtitle; its language comes from the .idx file"))
    return out
