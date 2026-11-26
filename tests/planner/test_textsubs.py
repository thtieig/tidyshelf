"""Word lists, decoding and language detection of text subtitles."""

from pathlib import Path

import pytest

from tidyshelf.textsubs import cue_texts, decode_candidates, detect, load_wordlists

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"


def read_fixture(name):
    return (SUBS / name).read_bytes()


@pytest.mark.parametrize("name, lang", [
    ("en_plain.srt", "en"), ("en_sdh.srt", "en"), ("ro.srt", "ro"),
    ("es.srt", "es"), ("it.srt", "it"), ("en.ass", "en"),
])
def test_detects_language(name, lang):
    d = detect(read_fixture(name), Path(name).suffix, load_wordlists())
    assert d.lang == lang, d
    assert d.confidence >= 0.6, d


def test_cp1250_romanian_decoded_as_cp1250():
    assert detect(read_fixture("ro.srt"), ".srt", load_wordlists()).encoding == "cp1250"


def test_comma_below_and_cedilla_romanian_both_count():
    text = read_fixture("ro.srt").decode("cp1250").replace("ş", "ș").replace("ţ", "ț")
    assert detect(text.encode("utf-8"), ".srt", load_wordlists()).lang == "ro"


def test_utf8_bom_stripped():
    assert decode_candidates(b"\xef\xbb\xbfhello")[0] == ("hello", "utf-8")


def test_utf16_with_bom():
    data = read_fixture("en_plain.srt").decode("utf-8").encode("utf-16")
    d = detect(data, ".srt", load_wordlists())
    assert (d.lang, d.encoding) == ("en", "utf-16")


def test_eight_bit_candidates_in_order():
    assert [enc for _t, enc in decode_candidates(b"caf\xe9")] == ["cp1252", "cp1250"]


@pytest.mark.parametrize("data", [b"", b"\x00\xff" * 5000, b"1\n00:00:01,000 --> 00:00:02,000\nhi\n"])
def test_too_little_text_is_unknown(data):
    assert detect(data, ".srt", load_wordlists()).lang is None


def test_cue_texts_strips_tags_and_timings():
    text = "1\n00:00:01,000 --> 00:00:02,000\n<i>Hello</i> {\\an8}there\n\n"
    assert cue_texts(text, ".srt") == ["Hello there"]


def test_cue_texts_multiline_cue_joined():
    text = "1\n00:00:01,000 --> 00:00:02,000\nHello\nthere\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n"
    assert cue_texts(text, ".srt") == ["Hello there", "Bye"]


def test_cue_texts_vtt():
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"
    assert cue_texts(text, ".vtt") == ["Hello"]


def test_cue_texts_ass_text_field_only():
    text = ("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\i1}Hello, there\\Nfriend{\\i0}\n")
    assert cue_texts(text, ".ass") == ["Hello, there friend"]


def test_wordlists_shipped():
    lists = load_wordlists()
    assert set(lists) == {"en", "es", "it", "fr", "de", "pt", "ro", "nl"}
    assert all(100 <= len(words) <= 150 for words in lists.values()), {k: len(v) for k, v in lists.items()}


def test_large_utf8_file_cut_inside_a_character_stays_utf8():
    from tidyshelf.textsubs import READ_LIMIT
    cue = "1\n00:00:01,000 --> 00:00:02,000\nPerché è così, più o meno, ecco tutto.\n\n"
    data = (cue * (READ_LIMIT // len(cue.encode()) + 10)).encode("utf-8")
    cut = READ_LIMIT
    while not 0x80 <= data[cut] < 0xC0:  # move the limit inside a multi-byte character
        data = b" " + data
    d = detect(data, ".srt", load_wordlists())
    assert d.encoding == "utf-8", d
