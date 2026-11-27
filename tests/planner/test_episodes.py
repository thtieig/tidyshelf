"""Reading season and episode numbers from names (NAMING "Series")."""

import pytest

from tidyshelf.episodes import Episode, parse_episode, resolve_numbering, season_from_folder


def ep(cfg, name, size=100, folder_season=None):
    return parse_episode(name, f"source:Show/{name}", size, folder_season, cfg, [])


@pytest.mark.parametrize("name, season, episodes, title", [
    ("Harbour.Lights.S01E02.The.Storm.720p.mkv", 1, (2,), "The Storm"),
    ("Harbour Lights - 1x02 - The Storm.mkv", 1, (2,), "The Storm"),
    ("Harbour.Lights.S03E16E17.mkv", 3, (16, 17), None),
    ("Harbour Lights - 1x08-09 - Two Parter.mkv", 1, (8, 9), "Two Parter"),
    ("Harbour.Lights.S01E23-E24.Finale.mkv", 1, (23, 24), "Finale"),
    ("Harbour.Lights.S00E01.Christmas.Special.mkv", 0, (1,), "Christmas Special"),
])
def test_explicit_styles(cfg, name, season, episodes, title):
    e = ep(cfg, name)
    assert (e.season, e.episodes, e.title) == (season, episodes, title)


def test_show_name_from_file(cfg):
    assert ep(cfg, "Harbour.Lights.S01E02.mkv").show == "Harbour Lights"


def test_episode_without_season_takes_the_folder_season(cfg):
    (e,), _f, review = resolve_numbering([ep(cfg, "Harbour Lights - 06 - The Bridge.mkv", folder_season=2)], None)
    assert (e.season, e.episodes, e.title, review) == (2, (6,), "The Bridge", None)


def test_season_encoded_bare_numbers(cfg):
    eps, _f, review = resolve_numbering([ep(cfg, "101.mkv"), ep(cfg, "102.mkv"), ep(cfg, "201.mkv")], None)
    assert [(e.season, e.episodes) for e in eps] == [(1, (1,)), (1, (2,)), (2, (1,))]
    assert review is None


def test_absolute_bare_numbers_with_an_outlier_special(cfg):
    names = [f"Harbour Lights {n:03}.mkv" for n in range(1, 105)]
    eps = [ep(cfg, n, size=1000) for n in names] + [ep(cfg, "Harbour Lights 999.mkv", size=100)]
    eps, findings, review = resolve_numbering(eps, None)
    assert [(e.season, e.episodes) for e in eps[:2]] == [(1, (1,)), (1, (2,))]
    assert (eps[103].season, eps[103].episodes) == (1, (104,))
    assert (eps[-1].season, eps[-1].episodes) == (0, (999,))
    assert review and "999" in review


def test_s1m1_markers_map_in_order(cfg):
    eps, findings, _r = resolve_numbering([ep(cfg, "Harbour.Lights.S1M2.mkv"), ep(cfg, "Harbour.Lights.S1M1.mkv")], None)
    assert [(e.ref.rsplit("/", 1)[1], e.season, e.episodes) for e in eps] == [
        ("Harbour.Lights.S1M2.mkv", 1, (2,)), ("Harbour.Lights.S1M1.mkv", 1, (1,))]
    assert "series.renumbered" in [c for _l, c, _m in findings]
    assert ep(cfg, "Harbour.Lights.S1M1.mkv").show == "Harbour Lights"


def test_assumed_season_with_many_episodes_needs_review(cfg):
    eps = [ep(cfg, f"Harbour Lights - {n:02} - x.mkv") for n in range(1, 31)]
    eps, _f, review = resolve_numbering(eps, None)
    assert all(e.season == 1 for e in eps)
    assert review == "numbering suggests more than one season"


def test_default_season_from_candidate(cfg):
    (e,), _f, review = resolve_numbering([ep(cfg, "E07.mkv")], 3)
    assert (e.season, e.episodes, review) == (3, (7,), None)


@pytest.mark.parametrize("name, season", [
    ("Season 1", 1), ("Season 11", 11), ("season.04", 4), ("S02", 2), ("Specials", 0), ("Season 00", 0),
    ("Featurettes", None), ("Harbour Lights", None),
])
def test_season_from_folder(name, season):
    assert season_from_folder(name) == season


def test_no_episode_number(cfg):
    assert ep(cfg, "Harbour Lights Behind The Scenes.mkv").episodes == ()


def test_episode_type():
    assert Episode.__dataclass_fields__.keys() >= {"ref", "season", "episodes", "title", "show", "size"}
