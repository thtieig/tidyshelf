"""Subtitle analysis and the choice of one subtitle per language."""

import os
import random
from pathlib import Path

import pytest

from tidyshelf import subtitles
from tidyshelf.subtitles import analyse, choose
from tidyshelf.textsubs import READ_LIMIT

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"


@pytest.fixture
def put(cfg):
    """put(name, fixture_or_bytes) -> SubInfo for source:Film/<name>."""
    folder = Path(cfg["roots"]["source"]) / "Film"

    def _put(name, content):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else (SUBS / content).read_bytes())
        return analyse(str(path), f"source:Film/{name}", cfg)

    return _put


def rel(ref):
    return ref.split(":", 1)[1].split("/", 1)[1]


def kept(choice):
    return [(rel(s.ref), lang, variant) for s, lang, variant in choice.keep]


def dropped(choice):
    return {rel(s.ref): reason for s, reason in choice.drop}


def codes(choice):
    return [code for _level, code, _msg in choice.findings]


def test_analyse_fields(put):
    s = put("Subs/English SDH.eng.srt", "en_sdh.srt")
    assert (s.ext, s.claimed, s.variant) == (".srt", "en", "sdh")
    assert s.detection.lang == "en" and s.cues > 20
    assert s.ad_cues == ["Subtitles by www.example.com"]
    assert len(s.sha256) == 64


def test_duplicate_removed_by_hash(cfg, put):
    a, b = put("a.en.srt", "en_plain.srt"), put("Subs/b.en.srt", "en_plain.srt")
    c = choose([a, b], cfg, rel)
    assert kept(c) == [("Subs/b.en.srt", "en", None)]  # smallest ref wins: "source:Film/Subs/..." < "source:Film/a..."
    assert dropped(c) == {"a.en.srt": "duplicate"}
    assert "subtitle.duplicate" in codes(c)


def test_mislabelled_language_detected_and_dropped(cfg, put):
    c = choose([put("Subs/English.eng.srt", "ro.srt"), put("Subs/en.srt", "en_plain.srt")], cfg, rel)
    assert dropped(c) == {"Subs/English.eng.srt": "language"}
    msgs = [m for _l, code, m in c.findings if code == "subtitle.language_mismatch"]
    assert len(msgs) == 1
    assert msgs[0].startswith("Subs/English.eng.srt is labelled English but its text is Romanian (confidence ")
    assert "subtitle.dropped_language" in codes(c)


def test_sdh_only_english_is_kept_with_sdh_tag(cfg, put):
    c = choose([put("Subs/English SDH.eng.srt", "en_sdh.srt")], cfg, rel)
    assert kept(c) == [("Subs/English SDH.eng.srt", "en", "sdh")]


def test_plain_beats_sdh(cfg, put):
    c = choose([put("sdh.en.sdh.srt", "en_sdh.srt"), put("plain.en.srt", "en_plain.srt")], cfg, rel)
    assert kept(c) == [("plain.en.srt", "en", None)]
    assert dropped(c) == {"sdh.en.sdh.srt": "not_preferred"}


def test_prefer_sdh_config(cfg, put):
    cfg["subtitles"]["prefer"] = "sdh"
    c = choose([put("sdh.en.sdh.srt", "en_sdh.srt"), put("plain.en.srt", "en_plain.srt")], cfg, rel)
    assert kept(c) == [("sdh.en.sdh.srt", "en", "sdh")]


def test_keep_sdh_alongside_keeps_both(cfg, put):
    cfg["subtitles"]["keep_sdh_alongside"] = True
    c = choose([put("sdh.en.sdh.srt", "en_sdh.srt"), put("plain.en.srt", "en_plain.srt")], cfg, rel)
    assert sorted(kept(c)) == [("plain.en.srt", "en", None), ("sdh.en.sdh.srt", "en", "sdh")]


def test_content_marks_sdh_without_name_marker(put):
    assert put("movie.en.srt", "en_sdh.srt").variant == "sdh"
    assert put("other.en.srt", "en_plain.srt").variant is None


def test_forced_kept_besides_main(cfg, put):
    forced = put("movie.en.forced.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHalt!\n")
    c = choose([forced, put("movie.en.srt", "en_plain.srt")], cfg, rel)
    assert sorted(kept(c)) == [("movie.en.forced.srt", "en", "forced"), ("movie.en.srt", "en", None)]


def test_tie_goes_to_more_cues_then_size_then_ref(cfg, put):
    short = put("a.en.srt", (SUBS / "en_plain.srt").read_bytes()[:700])
    full = put("b.en.srt", "en_plain.srt")
    assert kept(choose([short, full], cfg, rel)) == [("b.en.srt", "en", None)]


def test_vobsub_pair_kept_together_and_unverified(cfg, put):
    idx = put("movie.idx", b"# VobSub index file, v7\nsize: 720x576\nid: en, index: 0\n")
    sub = put("movie.sub", b"\x00\x00\x01\xba" + b"\x00" * 100)
    c = choose([idx, sub], cfg, rel)
    assert kept(c) == [("movie.idx", "en", None)]
    assert idx.pair == "source:Film/movie.sub"  # set by choose()
    assert dropped(c) == {}
    assert "subtitle.unverified" in codes(c)


def test_lone_sub_is_orphan(cfg, put):
    c = choose([put("movie.sub", b"\x00" * 50), put("movie.en.srt", "en_plain.srt")], cfg, rel)
    assert dropped(c) == {"movie.sub": "orphan"}


def test_disable_file_is_orphan(cfg, put):
    c = choose([put("movie.DISABLE.en.srt", "en_plain.srt"), put("x.es.srt", "es.srt")], cfg, rel)
    assert dropped(c) == {"movie.DISABLE.en.srt": "orphan"}


def test_single_untagged_unknown_gets_default_language(cfg, put):
    c = choose([put("movie.srt", b"1\n00:00:01,000 --> 00:00:02,000\n...\n")], cfg, rel)
    assert kept(c) == [("movie.srt", "en", None)]
    assert c.review is None


def test_several_unknown_needs_review(cfg, put):
    tiny = b"1\n00:00:01,000 --> 00:00:02,000\n...\n"
    c = choose([put("one.srt", tiny), put("two.srt", tiny + b"\n")], cfg, rel)
    assert c.review == "several subtitles with no recognisable language"


def test_claim_used_when_text_is_too_short(cfg, put):
    c = choose([put("movie.es.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHola.\n"),
                put("movie.en.srt", "en_plain.srt")], cfg, rel)
    assert ("movie.es.srt", "es", None) in kept(c)


def test_ad_cue_reported_not_removed(cfg, put):
    before = (SUBS / "en_sdh.srt").read_bytes()
    s = put("movie.en.srt", "en_sdh.srt")
    c = choose([s], cfg, rel)
    assert "subtitle.ad_cues" in codes(c)
    assert Path(s.path).read_bytes() == before


def test_few_cues_warned(cfg, put):
    c = choose([put("movie.en.srt", (SUBS / "en_plain.srt").read_bytes()[:1300])], cfg, rel)
    assert "subtitle.few_cues" in codes(c)


def test_lang_tags_cover_shipped_languages():
    assert {subtitles.LANG_TAGS[t] for t in ("eng", "spa", "ita", "fre", "ger", "por", "rum", "dut")} == \
        {"en", "es", "it", "fr", "de", "pt", "ro", "nl"}
    assert subtitles.LANG_TAGS["español"] == "es" and subtitles.LANG_TAGS["română"] == "ro"


class _CountingFile:
    def __init__(self, fh, sizes):
        self.fh, self.sizes = fh, sizes

    def read(self, n=-1):
        self.sizes.append(n)
        return self.fh.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fh.close()

    def __getattr__(self, name):
        return getattr(self.fh, name)


@pytest.mark.parametrize("kind", ["empty", "random", "utf16", "huge"])
def test_odd_files_do_not_crash_and_reads_are_bounded(cfg, put, monkeypatch, kind):
    sizes = []
    real_open = open
    monkeypatch.setattr(subtitles, "open", lambda *a, **kw: _CountingFile(real_open(*a, **kw), sizes), raising=False)
    if kind == "empty":
        s = put("movie.srt", b"")
    elif kind == "random":
        s = put("movie.srt", random.Random(1).randbytes(3 * 1024 * 1024))
    elif kind == "utf16":
        s = put("movie.srt", (SUBS / "en_plain.srt").read_text(encoding="utf-8").encode("utf-16"))
        assert s.detection.lang == "en"
    else:
        path = Path(cfg["roots"]["source"]) / "Film" / "movie.srt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        os.truncate(path, 50 * 1024 * 1024)
        s = analyse(str(path), "source:Film/movie.srt", cfg)
        assert s.size == 50 * 1024 * 1024
    assert sizes and all(0 < n <= READ_LIMIT + 1 for n in sizes), sizes[:5]
    choose([s], cfg, rel)


def test_lone_idx_is_orphan(cfg, put):
    c = choose([put("movie.idx", b"id: en, index: 0\n"), put("movie.en.srt", "en_plain.srt")], cfg, rel)
    assert dropped(c) == {"movie.idx": "orphan"}


def test_dropped_idx_takes_its_sub_along(cfg, put):
    idx = put("movie.DISABLE.idx", b"id: en, index: 0\n")
    sub = put("movie.DISABLE.sub", b"\x00" * 50)
    assert dropped(choose([idx, sub], cfg, rel)) == {"movie.DISABLE.idx": "orphan", "movie.DISABLE.sub": "orphan"}


def test_tag_of_a_language_without_word_list_is_a_claim(cfg, put):
    c = choose([put("Croatian.hrv.srt", "hr.srt"), put("Italian.ita.srt", "it.srt")], cfg, rel)
    assert kept(c) == [("Italian.ita.srt", "it", None)]
    assert dropped(c) == {"Croatian.hrv.srt": "language"}


def test_one_unknown_among_known_gets_default_language(cfg, put):
    c = choose([put("English.srt", "en_plain.srt"),
                put("forced.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHalt!\n")], cfg, rel)
    assert c.review is None
    assert sorted(kept(c)) == [("English.srt", "en", None), ("forced.srt", "en", "forced")]


def test_keep_sdh_alongside_keeps_forced_too(cfg, put):
    cfg["subtitles"]["keep_sdh_alongside"] = True
    forced = put("movie.en.forced.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHalt!\n")
    c = choose([forced, put("sdh.en.sdh.srt", "en_sdh.srt"), put("plain.en.srt", "en_plain.srt")], cfg, rel)
    assert sorted(kept(c)) == [("movie.en.forced.srt", "en", "forced"), ("plain.en.srt", "en", None),
                               ("sdh.en.sdh.srt", "en", "sdh")]
