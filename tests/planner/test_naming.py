"""Template rendering and filesystem-safe names."""

import pytest

from tidyshelf.naming import MOVIE_FOLDER_RE, episodes_text, movie_names, render, safe_name, subtitle_stem

EPISODE = "S{season:02}{episodes}< - {episode_title}>"


@pytest.mark.parametrize("values, expected", [
    ({"season": 1, "episodes": "E02", "episode_title": "Pilot"}, "S01E02 - Pilot"),
    ({"season": 1, "episodes": "E02", "episode_title": ""}, "S01E02"),
    ({"season": 1, "episodes": "E02", "episode_title": None}, "S01E02"),
    ({"season": 1, "episodes": "E02", "episode_title": "A <b> c"}, "S01E02 - A <b> c"),  # value brackets are text
])
def test_render_optional_segment(values, expected):
    assert render(EPISODE, values) == expected


def test_render_subtitle_variant():
    tpl = "{video}.{lang}<.{variant}>"
    assert render(tpl, {"video": "A (2000)", "lang": "en", "variant": None}) == "A (2000).en"
    assert render(tpl, {"video": "A (2000)", "lang": "en", "variant": "sdh"}) == "A (2000).en.sdh"


def test_render_value_with_braces_is_text():
    assert render("{title} ({year})", {"title": "{year}", "year": 2000}) == "{year} (2000)"


@pytest.mark.parametrize("raw, expected", [
    ("Mission: Possible", "Mission - Possible"), ('A "B" C?', "A B C"), ("Who*", "Who"),
    ("a/b\\c", "abc"), ("Title.", "Title"), ("  two   spaces ", "two spaces"),
    ("...", ""), (":*?", ""), ("..", ""), (".", ""), ("tab\there", "tabhere"), ("pipe|line", "pipeline"),
    ("<x>", "x"), ("...And Then", "And Then"),
])
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_episodes_text():
    assert episodes_text([2], "-E") == "E02"
    assert episodes_text([23, 24], "-E") == "E23-E24"


@pytest.mark.parametrize("name, title, year, suffix", [
    ("Winter Lights (2004)", "Winter Lights", "2004", ""),
    ("Winter Lights (2004) [ES]", "Winter Lights", "2004", " [ES]"),
    ("Winter Lights (2004) - (Studio Cut)", "Winter Lights", "2004", " - (Studio Cut)"),
    ("Winter Lights (2004) [ES] - (Studio Cut)", "Winter Lights", "2004", " [ES] - (Studio Cut)"),
])
def test_existing_movie_folder(name, title, year, suffix):
    m = MOVIE_FOLDER_RE.fullmatch(name)
    assert m is not None
    assert (m["title"], m["year"], m["suffix"]) == (title, year, suffix)


@pytest.mark.parametrize("name", ["Winter.Lights.2004.1080p", "Winter Lights 2004", "(2004)"])
def test_not_a_movie_folder(name):
    assert MOVIE_FOLDER_RE.fullmatch(name) is None


def test_movie_names_from_defaults(cfg):
    assert movie_names(cfg, "Mission: Possible", 1999) == ("Mission - Possible (1999)", "Mission - Possible (1999)")


def test_subtitle_stem_from_defaults(cfg):
    assert subtitle_stem(cfg, "A (2000)", "en", "sdh") == "A (2000).en.sdh"
    assert subtitle_stem(cfg, "A (2000)", "es", None) == "A (2000).es"
