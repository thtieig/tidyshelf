"""Text subtitles: decoding, cue text and language detection by counting common words."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from importlib import resources

READ_LIMIT = 2 * 1024 * 1024  # bytes read for detection; plenty for any subtitle
MIN_WORDS = 30  # fewer matched words than this and the language is "unknown"
# Share of all words that some list knows. Text in a language without a list (Croatian, Catalan)
# matches only a few look-alike words and must stay "unknown", not become the nearest language.
MIN_COVERAGE = 0.3

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


def _score(text: str, ext: str, wordlists: dict) -> tuple[str | None, int, int, int]:
    """(best language, its matched words, matched words of any language, all words)."""
    tokens = _WORD.findall(" ".join(cue_texts(text, ext)).lower().translate(_CEDILLA))
    hits = {lang: 0 for lang in wordlists}
    any_hits = 0
    for token in tokens:
        matched = [lang for lang, words in wordlists.items() if token in words]
        any_hits += bool(matched)
        for lang in matched:
            hits[lang] += 1
    if not hits:
        return None, 0, 0, len(tokens)
    best = min(hits, key=lambda lang: (-hits[lang], lang))
    return best, hits[best], any_hits, len(tokens)


def detect(data: bytes, ext: str, wordlists: dict) -> Detection:
    chunk = data[:READ_LIMIT]
    if len(data) > READ_LIMIT and b"\n" in chunk:  # cut at a line end, never inside a character
        chunk = chunk[:chunk.rfind(b"\n") + 1]
    best = None
    for text, enc in decode_candidates(chunk):
        lang, hits, any_hits, tokens = _score(text, ext, wordlists)
        if best is None or hits > best[1]:
            best = (lang, hits, any_hits, tokens, enc)
    lang, hits, any_hits, tokens, enc = best
    if any_hits < MIN_WORDS or any_hits < MIN_COVERAGE * tokens:
        return Detection(None, 0.0, any_hits, enc)
    return Detection(lang, round(hits / any_hits, 2), any_hits, enc)


_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})")
_ASS_TIME = re.compile(r"^Dialogue:[^,]*,[^,]*,(\d+):(\d{2}):(\d{2})[.](\d{1,3})", re.MULTILINE)


def last_cue_end(text: str, ext: str) -> float | None:
    """Seconds at which the last cue ends, or None when the text has no timings."""
    if ext in (".ass", ".ssa"):
        ends = [int(h) * 3600 + int(m) * 60 + int(s) + int(f) / 100 for h, m, s, f in _ASS_TIME.findall(text)]
    else:
        ends = [int(h) * 3600 + int(m) * 60 + int(s) + int(f) / 1000 for *_start, h, m, s, f in _TIME.findall(text)]
    return max(ends) if ends else None


def read_cues(data: bytes, ext: str, encoding: str) -> list[str]:
    """Cue texts of `data` decoded the way detect() chose."""
    codec = "utf-8-sig" if encoding == "utf-8" else encoding
    return cue_texts(data.decode(codec, errors="replace"), ext)


@dataclass(frozen=True)
class Cleaned:
    data: bytes  # the new file: UTF-8 without BOM
    ads_removed: int
    converted_from: str | None  # the encoding it was converted from, when that was not UTF-8


_BLOCK_SPLIT = re.compile(r"\r?\n\s*\r?\n")


def _srt_blocks(text: str) -> list[list[str]] | None:
    """Cue blocks as raw lines [timing, text...] (the number line dropped); None if the text is not
    plain SRT (a block with no timing line, two timing lines, or stray lines before the timing): such
    a file is never rewritten."""
    blocks = []
    for raw in _BLOCK_SPLIT.split(text.strip("\r\n")):
        lines = raw.splitlines()
        if not any(ln.strip() for ln in lines):
            continue
        timing = [i for i, ln in enumerate(lines) if "-->" in ln]
        if len(timing) != 1 or timing[0] > 1 or (timing[0] == 1 and not lines[0].strip().isdigit()):
            return None
        blocks.append(lines[timing[0]:])
    return blocks


def _cue_text(lines: list[str]) -> str:
    return _clean(" ".join(ln for ln in lines[1:] if ln.strip()))


def clean_srt(data: bytes, encoding: str, patterns: list, clean_ads: bool, to_utf8: bool) -> Cleaned | None:
    """The cleaned bytes of an .srt, or None when nothing needs to change or it cannot be done safely.

    Ad cues are the ones at the edges (first three and last three cues) whose text matches a pattern,
    as the analysis reports them. They are dropped and the rest renumbered from 1; timing and text
    lines are kept as they are, with line endings written as \\n. A file that is not UTF-8 is decoded
    strictly (a byte with no meaning in the detected encoding means no conversion) and written as UTF-8
    without a BOM. A UTF-8 file without ad cues is left alone, byte for byte."""
    codec = "utf-8-sig" if encoding == "utf-8" else encoding
    try:
        text = data.decode(codec)
    except UnicodeDecodeError:
        return None
    converted = encoding if (to_utf8 and encoding != "utf-8") else None
    if encoding != "utf-8" and not to_utf8:
        return None  # a file that stays in its old encoding is not rewritten at all
    removed = 0
    if clean_ads:
        blocks = _srt_blocks(text)
        if blocks is not None:
            texts = [(i, _cue_text(b)) for i, b in enumerate(blocks)]
            texts = [(i, t) for i, t in texts if t]
            edges = texts[:3] + texts[3:][-3:]
            ads = {i for i, t in edges if any(p.search(t) for p in patterns)}
            if ads and len(ads) < len(blocks):
                kept = [b for i, b in enumerate(blocks) if i not in ads]
                text = "\n\n".join("\n".join([str(n)] + b) for n, b in enumerate(kept, 1)) + "\n"
                removed = len(ads)
    if not removed and converted is None:
        return None
    return Cleaned(text.encode("utf-8"), removed, converted)
