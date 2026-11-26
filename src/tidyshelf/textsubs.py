"""Text subtitles: decoding, cue text and language detection by counting common words."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from importlib import resources

READ_LIMIT = 2 * 1024 * 1024  # bytes read for detection; plenty for any subtitle
MIN_WORDS = 30  # fewer matched words than this and the language is "unknown"

_TAG = re.compile(r"\{[^}]*\}|<[^>]*>")
_WORD = re.compile(r"[^\W\d_]+")
_CEDILLA = str.maketrans("şţŞŢ", "șțȘȚ")  # Romanian written with cedillas (cp1250) counts the same


@dataclass(frozen=True)
class Detection:
    lang: str | None
    confidence: float  # share of matched words that belong to `lang` (0..1)
    words: int  # matched words, all languages
    encoding: str


@functools.cache
def load_wordlists() -> dict[str, frozenset[str]]:
    lists = {}
    for entry in sorted(resources.files("tidyshelf").joinpath("data", "stopwords").iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".txt"):
            words = (line.strip() for line in entry.read_text(encoding="utf-8").splitlines())
            lists[entry.name[:-4]] = frozenset(w.translate(_CEDILLA) for w in words if w and not w.startswith("#"))
    return lists


def decode_candidates(data: bytes) -> list[tuple[str, str]]:
    """Possible (text, encoding) readings, most likely first."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return [(data.decode("utf-16", errors="replace"), "utf-16")]
    try:
        return [(data.decode("utf-8-sig"), "utf-8")]
    except UnicodeDecodeError:
        return [(data.decode(enc, errors="replace"), enc) for enc in ("cp1252", "cp1250")]


def _clean(line: str) -> str:
    return " ".join(_TAG.sub("", line.replace("\\N", " ").replace("\\n", " ")).split())


def cue_texts(text: str, ext: str) -> list[str]:
    """The dialogue text of each cue, tags removed."""
    if ext in (".ass", ".ssa"):
        cues = [line.split(",", 9)[9] for line in text.splitlines()
                if line.startswith("Dialogue:") and line.count(",") >= 9]
        return [c for c in (_clean(c) for c in cues) if c]
    out = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = [ln for ln in block.splitlines()
                 if ln.strip() and "-->" not in ln and not ln.strip().isdigit() and ln.strip() != "WEBVTT"]
        cue = _clean(" ".join(lines))
        if cue:
            out.append(cue)
    return out


def _score(text: str, ext: str, wordlists: dict) -> tuple[str | None, int, int]:
    """(best language, its matched words, matched words of any language)."""
    tokens = _WORD.findall(" ".join(cue_texts(text, ext)).lower().translate(_CEDILLA))
    hits = {lang: 0 for lang in wordlists}
    any_hits = 0
    for token in tokens:
        matched = [lang for lang, words in wordlists.items() if token in words]
        any_hits += bool(matched)
        for lang in matched:
            hits[lang] += 1
    if not hits:
        return None, 0, 0
    best = min(hits, key=lambda lang: (-hits[lang], lang))
    return best, hits[best], any_hits


def detect(data: bytes, ext: str, wordlists: dict) -> Detection:
    chunk = data[:READ_LIMIT]
    if len(data) > READ_LIMIT and b"\n" in chunk:  # cut at a line end, never inside a character
        chunk = chunk[:chunk.rfind(b"\n") + 1]
    best = None
    for text, enc in decode_candidates(chunk):
        lang, hits, any_hits = _score(text, ext, wordlists)
        if best is None or hits > best[1]:
            best = (lang, hits, any_hits, enc)
    lang, hits, any_hits, enc = best
    if any_hits < MIN_WORDS:
        return Detection(None, 0.0, any_hits, enc)
    return Detection(lang, round(hits / any_hits, 2), any_hits, enc)
