"""Parsing names: guessit plus config rules, overrides, folder against file."""

import pytest

from tidyshelf.parse import Parsed, decide_movie, find_override, parse_name


def P(title=None, year=None, kind="movie"):
    return Parsed(title=title, year=year, kind=kind, season=None, episodes=(), episode_title=None)


# Pins guessit's behaviour on these invented names: an upgrade that changes it fails here first.
@pytest.mark.parametrize("name, title, year, kind", [
    ("Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP.mp4", "Winter Lights", 2004, "movie"),
    ("www.example.com - Winter Lights (2004) 1080p BluRay-GRP", "Winter Lights", 2004, "movie"),
    ("Winter Lights (2004) [ES]", "Winter Lights", 2004, "movie"),
    ("the.lost.city.1999.720p.mkv", "The Lost City", 1999, "movie"),
    ("THE LOST CITY 1999.mkv", "The Lost City", 1999, "movie"),
    ("McQueen and the River 2001.mkv", "McQueen and the River", 2001, "movie"),
    ("Some.Show.S01E02.720p.mkv", "Some Show", None, "series"),
    ("1080p.x264.mkv", None, None, "movie"),
])
def test_parse_name(cfg, name, title, year, kind):
    p = parse_name(name, cfg, [])
    assert (p.title, p.year, p.kind) == (title, year, kind)


def test_series_fields(cfg):
    p = parse_name("Some.Show.S01E02E03.720p.mkv", cfg, [])
    assert (p.season, p.episodes) == (1, (2, 3))


def test_title_of_only_junk_is_none(cfg):
    assert parse_name("x264 HEVC 1999.mkv", cfg, []).title is None


def test_extra_tokens_count_as_junk(cfg):
    cfg["parse"]["extra_tokens"] = ["homecut"]
    assert parse_name("HomeCut.1999.mkv", cfg, []).title is None


def test_title_rules_applied_in_order(cfg):
    cfg["parse"]["title_rules"] = [{"match": r"^Example Site$", "replace": "Winter Lights"},
                                   {"match": r"Lights", "replace": "Light"}]
    p = parse_name("Example.Site.2004.mkv", cfg, trace := [])
    assert p.title == "Winter Light"
    assert sum("title rule" in line for line in trace) == 2


def test_trace_explains_each_step(cfg):
    trace = []
    parse_name("the.lost.city.1999.720p.mkv", cfg, trace)
    assert any("guessit" in line and "the lost city" in line for line in trace)
    assert any("title case" in line for line in trace)


@pytest.mark.parametrize("match", ["Winter*", "re:^www\\.", "*2004*"])
def test_override_glob_and_regex(cfg, match):
    cfg["override"] = [{"match": "Other*"}, {"match": match, "year": 2005}]
    assert find_override("www.example.com - Winter Lights (2004)" if "www" in match else "Winter Lights 2004", cfg) \
        == {"match": match, "year": 2005}


def test_override_none(cfg):
    cfg["override"] = [{"match": "Other*"}]
    assert find_override("Winter Lights", cfg) is None


def test_first_override_wins(cfg):
    cfg["override"] = [{"match": "W*", "year": 1}, {"match": "Wi*", "year": 2}]
    assert find_override("Winter", cfg)["year"] == 1


def test_override_wins_and_is_reported(cfg):
    d = decide_movie(P("Winter Lights", 2004), P("Winter Lights", 2004), {"match": "W*", "title": "Winter Light"}, cfg, [])
    assert (d.title, d.year) == ("Winter Light", 2004)
    assert [c for _l, c, _m in d.findings] == ["parse.override"]


def test_override_ignore(cfg):
    assert decide_movie(None, P("A", 2000), {"match": "A*", "ignore": True}, cfg, []).ignore is True


def test_override_kind(cfg):
    assert decide_movie(None, P("A", 2000), {"match": "A*", "kind": "series"}, cfg, []).kind == "series"


def test_folder_beats_file_on_year(cfg):
    d = decide_movie(P("Winter Lights", 2004), P("Winter Lights", 2005), None, cfg, [])
    assert d.year == 2004
    assert [c for _l, c, _m in d.findings] == ["parse.folder_file_disagree"]


def test_file_beats_folder_when_configured(cfg):
    cfg["parse"]["folder_beats_file"] = False
    assert decide_movie(P("Winter Lights", 2004), P("Winter Lights", 2005), None, cfg, []).year == 2005


def test_missing_field_taken_from_the_other_name(cfg):
    d = decide_movie(P("Winter Lights", None), P(None, 2004), None, cfg, [])
    assert (d.title, d.year) == ("Winter Lights", 2004)
    assert [c for _l, c, _m in d.findings] == ["parse.mixed_names"]  # reported: the pair is not from one name


def test_missing_year_needs_review(cfg):
    assert decide_movie(None, P("Winter Lights", None), None, cfg, []).review == "no year found"


def test_missing_title_needs_review(cfg):
    assert decide_movie(None, P(None, 2004), None, cfg, []).review == "no title found"


def test_series_if_either_name_is_series(cfg):
    assert decide_movie(P("Show", None, "series"), P("Show", None), None, cfg, []).kind == "series"


@pytest.mark.parametrize("title, junk", [("www.example.com", True), ("example.org", True),
                                         ("Winter Lights", False), ("Lights of Example Com", False)])
def test_title_that_is_a_web_address_is_junk(title, junk):
    from tidyshelf.parse import _is_junk
    assert _is_junk(title, set()) is junk


def test_plain_file_name_does_not_count_as_disagreement(cfg):
    d = decide_movie(P("Winter Lights", 2004), P("Film", None), None, cfg, [])
    assert (d.title, d.year, d.findings) == ("Winter Lights", 2004, [])


def test_title_disagreement_reported_when_both_names_are_full(cfg):
    d = decide_movie(P("Winter Lights", 2004), P("Winter Light", 2004), None, cfg, [])
    assert [c for _l, c, _m in d.findings] == ["parse.folder_file_disagree"]


def test_title_and_year_come_from_the_same_name(cfg):
    d = decide_movie(P("New Folder", None), P("Cold River", 2010), None, cfg, [])
    assert (d.title, d.year) == ("Cold River", 2010)


def test_name_rules_apply_before_guessit(cfg):
    cfg["parse"]["name_rules"] = [{"match": r"^Example\.Site - ", "replace": ""}]
    p = parse_name("Example.Site - Winter Lights (2004) 1080p BluRay-GRP", cfg, trace := [])
    assert (p.title, p.year) == ("Winter Lights", 2004)
    assert any("name rule" in line for line in trace)


@pytest.mark.parametrize("name, title", [
    ("W.L. (2004)", "W.L."),
    ("S.W.A.T. (2003)", "S.W.A.T."),
])
def test_dotted_acronyms_keep_their_capitals(cfg, name, title):
    assert parse_name(name, cfg, []).title == title


@pytest.mark.parametrize("word, expected", [("w.l.", "W.L."), ("s.w.a.t", "S.W.A.T"), ("river", "River"),
                                            ("mcqueen", "Mcqueen"), ("2004", "2004")])
def test_title_word(word, expected):
    from tidyshelf.parse import _title_word
    assert _title_word(word) == expected
