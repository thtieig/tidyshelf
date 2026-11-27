"""The series planner: season packs, show folders and loose episodes."""

from pathlib import Path

import pytest

from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
PACK = "Harbour.Lights.S01.720p"
SHOW = "series:Harbour Lights/Season 1"


def items(lib, *paths):
    return build_plan(lib.cfg, list(paths) or None)[0].to_dict()["items"]


def only(lib, *paths):
    (item,) = items(lib, *paths)
    return item


def moves(item):
    return {op["from"].rsplit("/", 1)[-1].split(":", 1)[-1]: op["to"] for op in item["ops"] if op["op"] == "move"}


def codes(item):
    return [f["code"] for f in item["findings"]]


def test_season_pack(lib):
    lib.tree("source", f"{PACK}/Harbour.Lights.S01E01.Pilot.720p.mkv\n{PACK}/Harbour.Lights.S01E02.The.Storm.720p.mkv")
    item = only(lib)
    assert (item["id"], item["kind"], item["status"]) == ("series-0001", "series", "ready")
    assert moves(item) == {"Harbour.Lights.S01E01.Pilot.720p.mkv": f"{SHOW}/S01E01 - Pilot.mkv",
                           "Harbour.Lights.S01E02.The.Storm.720p.mkv": f"{SHOW}/S01E02 - The Storm.mkv"}
    assert [op["path"] for op in item["ops"] if op["op"] == "mkdir"] == [SHOW]
    assert [op["path"] for op in item["ops"] if op["op"] == "set_mode"][:2] == ["series:Harbour Lights", SHOW]
    assert [op["path"] for op in item["ops"] if op["op"] == "delete"] == [f"source:{PACK}"]


def test_show_folder_with_season_and_specials_folders(lib):
    lib.tree("source", "Old Coast/Season 2/Old Coast - 2x03 - The Lighthouse.mkv\n"
                       "Old Coast/Specials/Old.Coast.S00E01.mkv\nOld Coast/Season 2/E04.mkv")
    m = moves(only(lib))
    assert m["Old Coast - 2x03 - The Lighthouse.mkv"] == "series:Old Coast/Season 2/S02E03 - The Lighthouse.mkv"
    assert m["E04.mkv"] == "series:Old Coast/Season 2/S02E04.mkv"
    assert m["Old.Coast.S00E01.mkv"] == "series:Old Coast/Season 00/S00E01.mkv"


def test_loose_episode_with_its_subtitle(lib):
    lib.tree("source", "Harbour.Lights.S01E05.mkv")
    lib.write("source", "Harbour.Lights.S01E05.en.srt", (SUBS / "en_plain.srt").read_bytes())
    item = only(lib)
    assert moves(item) == {"Harbour.Lights.S01E05.mkv": f"{SHOW}/S01E05.mkv",
                           "Harbour.Lights.S01E05.en.srt": f"{SHOW}/S01E05.en.srt"}
    assert not [op for op in item["ops"] if op["op"] == "delete"]


def test_multi_episode_file_and_its_subtitle(lib):
    lib.tree("source", f"{PACK}/Harbour.Lights.S01E03E04.Two.Parter.mkv\n{PACK}/Harbour.Lights.S01E01.mkv")
    lib.write("source", f"{PACK}/Subs/Harbour.Lights.S01E03E04.eng.srt", (SUBS / "en_plain.srt").read_bytes())
    m = moves(only(lib))
    assert m["Harbour.Lights.S01E03E04.Two.Parter.mkv"] == f"{SHOW}/S01E03-E04 - Two Parter.mkv"
    assert m["Harbour.Lights.S01E03E04.eng.srt"] == f"{SHOW}/S01E03-E04 - Two Parter.en.srt"


def test_subtitles_go_next_to_their_episode(lib):
    lib.tree("source", f"{PACK}/Harbour.Lights.S01E01.Pilot.mkv\n{PACK}/Harbour.Lights.S01E02.mkv")
    lib.write("source", f"{PACK}/Subs/Harbour.Lights.S01E01.eng.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.write("source", f"{PACK}/Subs/Harbour.Lights.S01E02.eng.srt", (SUBS / "en_sdh.srt").read_bytes())
    m = moves(only(lib))
    assert m["Harbour.Lights.S01E01.eng.srt"] == f"{SHOW}/S01E01 - Pilot.en.srt"
    assert m["Harbour.Lights.S01E02.eng.srt"] == f"{SHOW}/S01E02.en.sdh.srt"


def test_subtitle_without_episode_is_trashed(lib):
    lib.tree("source", f"{PACK}/Harbour.Lights.S01E01.mkv\n{PACK}/Harbour.Lights.S01E02.mkv")
    lib.write("source", f"{PACK}/English.srt", (SUBS / "en_plain.srt").read_bytes())
    item = only(lib)
    assert "trash" in moves(item)["English.srt"] and "subtitle.no_episode" in codes(item)


def test_illegal_characters_in_episode_title(lib):
    lib.tree("source", "Harbour.Lights.S01E06.Who.Goes:There?.mkv")
    assert moves(only(lib))["Harbour.Lights.S01E06.Who.Goes:There?.mkv"].startswith(f"{SHOW}/S01E06 - Who Goes")
    assert "?" not in moves(only(lib))["Harbour.Lights.S01E06.Who.Goes:There?.mkv"].split("/")[-1]


def test_two_videos_claiming_one_episode_need_review(lib):
    lib.tree("source", f"{PACK}/Harbour.Lights.S01E01.mkv\n{PACK}/Harbour.Lights.S01E01.Proper.mkv")
    item = only(lib)
    assert (item["status"], item["ops"]) == ("needs_review", []) and "series.duplicate" in codes(item)


def test_video_without_episode_number_needs_review(lib):
    lib.tree("source", f"{PACK}/Harbour.Lights.S01E01.mkv\n{PACK}/Harbour Lights Behind The Scenes.mkv")
    item = only(lib)
    assert item["status"] == "needs_review" and "series.no_episode" in codes(item)


def test_merges_into_an_existing_show(lib):
    lib.tree("series", "Harbour Lights/Season 1/S01E01 - Pilot.mkv")
    lib.tree("source", "Harbour.Lights.S01E02.mkv")
    item = only(lib)
    assert item["status"] == "ready"
    assert moves(item) == {"Harbour.Lights.S01E02.mkv": f"{SHOW}/S01E02.mkv"}
    assert "series:Harbour Lights" not in [op["path"] for op in item["ops"] if op["op"] == "set_mode"]


def test_existing_show_with_a_year_is_reused(lib):
    lib.tree("series", "Harbour Lights (2019)/Season 1/S01E01.mkv")
    lib.tree("source", "Harbour.Lights.2019.S01E02.mkv")
    assert moves(only(lib))["Harbour.Lights.2019.S01E02.mkv"] == "series:Harbour Lights (2019)/Season 1/S01E02.mkv"


@pytest.mark.parametrize("on_exists, status", [("report", "needs_review"), ("skip", "skipped")])
def test_episode_already_in_the_library(lib, on_exists, status):
    lib.write_config(f'[conflicts]\non_exists = "{on_exists}"\n')
    lib.tree("series", "Harbour Lights/Season 1/S01E02 - The Storm.mkv [500]")
    lib.tree("source", "Harbour.Lights.S01E02.mkv")
    item = only(lib)
    assert (item["status"], item["ops"]) == (status, []) and "conflict.exists" in codes(item)


def test_same_episode_twice_in_one_plan(lib):
    lib.tree("source", "Harbour.Lights.S01E02.mkv\nHarbour.Lights.S01E02.720p/Harbour.Lights.S01E02.720p.mkv")
    first, second = items(lib)
    assert first["status"] == "ready" and "conflict.in_plan" in codes(second)


def test_override_season_for_loose_numbers(lib):
    lib.write_config('[[override]]\nmatch = "Old Coast"\nseason = 3\n')
    lib.tree("source", "Old Coast/Old Coast - 06 - The Bridge.mkv")
    assert moves(only(lib))["Old Coast - 06 - The Bridge.mkv"] == "series:Old Coast/Season 3/S03E06 - The Bridge.mkv"


def test_absolute_outlier_needs_review(lib):
    listing = "\n".join(f"Old Coast/Old Coast {n:03}.mkv [1000]" for n in range(1, 11)) + "\nOld Coast/Old Coast 999.mkv [10]"
    lib.tree("source", listing)
    item = only(lib)
    assert item["status"] == "needs_review" and "999" in " ".join(f["msg"] for f in item["findings"])


def test_extras_folder_left_in_a_show(lib):
    lib.tree("source", f"{PACK}/Harbour.Lights.S01E01.mkv\n{PACK}/Featurettes/making-of.mkv")
    item = only(lib)
    assert item["status"] == "ready" and not [op for op in item["ops"] if op["op"] == "delete"]


def test_marker_files_in_a_plain_folder_are_a_series(lib):
    lib.tree("source", "Harbour Lights/Harbour.Lights.S1M1.mkv\nHarbour Lights/Harbour.Lights.S1M2.mkv")
    item = only(lib)
    assert item["status"] == "ready"
    assert sorted(moves(item).values()) == [f"{SHOW}/S01E01.mkv", f"{SHOW}/S01E02.mkv"]


def test_joining_an_existing_show_is_reported(lib):
    lib.tree("series", "Harbour Lights (2019)/Season 1/S01E01.mkv")
    lib.tree("source", "Harbour.Lights.S01E02.mkv")
    assert "series.joins_existing" in codes(only(lib))


# Number-first names: the text after the number is the episode title (docs/NAMING.md: keep it).

@pytest.mark.parametrize("name, target", [
    ("304 The Burglary.mkv", "Season 3/S03E04 - The Burglary.mkv"),
    ("304.The.Burglary.720p.mkv", "Season 3/S03E04 - The Burglary.mkv"),
    ("3x05 The Ledger.mkv", "Season 3/S03E05 - The Ledger.mkv"),
    ("S03E06.The.Night.Shift.mkv", "Season 3/S03E06 - The Night Shift.mkv"),
    ("s03e07 - the long road.mkv", "Season 3/S03E07 - The Long Road.mkv"),
])
def test_number_first_name_keeps_the_episode_title(lib, name, target):
    lib.tree("source", f"Quiet Street/{name}")
    assert moves(only(lib)) == {name: f"series:Quiet Street/{target}"}


def test_absolute_number_first_keeps_the_episode_title(lib):
    lib.tree("source", "Nonna Rosa/070 La teoria di Alvaro.mp4\nNonna Rosa/071 Il ritorno.mp4")
    assert moves(only(lib)) == {"070 La teoria di Alvaro.mp4": "series:Nonna Rosa/Season 1/S01E70 - La teoria di Alvaro.mp4",
                                "071 Il ritorno.mp4": "series:Nonna Rosa/Season 1/S01E71 - Il ritorno.mp4"}


def test_number_first_title_is_not_taken_as_the_show(lib):
    lib.tree("source", "304 The Burglary.mkv")
    item = only(lib)
    assert item["status"] == "needs_review" and "parse.no_title" in codes(item)


def test_show_name_with_leading_digits_is_still_the_show(lib):
    lib.tree("source", "100.Doors.S01E02.mkv")
    assert moves(only(lib)) == {"100.Doors.S01E02.mkv": "series:100 Doors/Season 1/S01E02.mkv"}


# New show folders keep the year and the tags of the source.

@pytest.mark.parametrize("listing, show", [
    ("Glen (2015)/S01E01.mkv", "Glen (2015)"),
    ("Pier.Lane.2011.S02/Pier.Lane.2011.S02E01.mkv", "Pier Lane (2011)"),
    ("Pier.Lane.2011.S02E01.mkv", "Pier Lane (2011)"),
    ("Pier Lane/Pier.Lane.2011.S02E01.mkv", "Pier Lane (2011)"),
    ("Nonna Rosa [ITA]/S01E01.mkv", "Nonna Rosa [ITA]"),
    ("Glen (2015) [ITA] [ESP]/S01E01.mkv", "Glen (2015) [ITA] [ESP]"),
    ("Nonna.Rosa.[ITA].S01/Nonna.Rosa.S01E01.mkv", "Nonna Rosa [ITA]"),
    ("Nonna.Rosa.S01E01.[ITA].mkv", "Nonna Rosa [ITA]"),
    ("Nonna Rosa [1080p]/S01E01.mkv", "Nonna Rosa"),
    ("Nonna Rosa [Studio Cut]/S01E01.mkv", "Nonna Rosa [Studio Cut]"),
    ("Nonna.Rosa.[Studio Cut].S01/S01E01.mkv", "Nonna Rosa"),
])
def test_new_show_folder_keeps_year_and_tags(lib, listing, show):
    lib.tree("source", listing)
    (target,) = moves(only(lib)).values()
    assert target.rsplit("/", 2)[0] == f"series:{show}"


def test_same_new_show_twice_in_one_plan_gets_one_folder(lib):
    lib.tree("source", "Glen (2015)/S01E01.mkv\nGlen.S01E02.mkv")
    first, second = items(lib)
    assert {t.rsplit("/", 2)[0] for t in [*moves(first).values(), *moves(second).values()]} == {"series:Glen (2015)"}


# Existing library shows are found whatever the new name leaves out.

@pytest.mark.parametrize("library, source, show", [
    ("Mountain Girl [ITA]", "Mountain.Girl.S01E03.mkv", "Mountain Girl [ITA]"),
    ("Pine and Willow (2015)", "Pine.and.Willow.S01E03.mkv", "Pine and Willow (2015)"),
    ("Pine and Willow (2015) [ITA]", "Pine.and.Willow.S01E03.mkv", "Pine and Willow (2015) [ITA]"),
    ("Pine and Willow", "Pine.and.Willow.2015.S01E03.mkv", "Pine and Willow"),
    ("Mountain Girl", "Mountain Girl [ITA]/S01E03.mkv", "Mountain Girl"),
    ("mountain girl [ita]", "Mountain Girl [ITA]/S01E03.mkv", "mountain girl [ita]"),
])
def test_new_episode_joins_the_existing_show(lib, library, source, show):
    lib.tree("series", f"{library}/Season 1/S01E01.mkv")
    lib.tree("source", source)
    item = only(lib)
    (target,) = moves(item).values()
    assert target == f"series:{show}/Season 1/S01E03.mkv" and "series.joins_existing" in codes(item)


@pytest.mark.parametrize("source, show", [
    ("Mountain Girl [ITA]/S01E03.mkv", "Mountain Girl [ITA]"),
    ("Mountain.Girl.S01E03.mkv", "Mountain Girl"),
])
def test_exact_library_name_wins_over_a_tagged_or_untagged_twin(lib, source, show):
    lib.tree("series", "Mountain Girl/Season 1/S01E01.mkv\nMountain Girl [ITA]/Season 1/S01E01.mkv")
    lib.tree("source", source)
    (target,) = moves(only(lib)).values()
    assert target == f"series:{show}/Season 1/S01E03.mkv"


def test_different_year_is_a_different_show(lib):
    lib.tree("series", "Glen (1990)/Season 1/S01E01.mkv")
    lib.tree("source", "Glen (2015)/S01E01.mkv")
    item = only(lib)
    assert moves(item) == {"S01E01.mkv": "series:Glen (2015)/Season 1/S01E01.mkv"}
    assert "series.joins_existing" not in codes(item)


def test_two_possible_library_shows_need_review(lib):
    lib.tree("series", "Mountain Girl [ITA]/Season 1/S01E01.mkv\nMountain Girl [ESP]/Season 1/S01E01.mkv")
    lib.tree("source", "Mountain.Girl.S01E03.mkv")
    item = only(lib)
    assert (item["status"], item["ops"]) == ("needs_review", []) and "series.ambiguous_show" in codes(item)
    assert "Mountain Girl [ESP]" in item["findings"][-1]["msg"] and "Mountain Girl [ITA]" in item["findings"][-1]["msg"]


@pytest.mark.parametrize("override", ['title = "mountain girl [esp]"', 'title = "Mountain Girl"\nyear = 2001'])
def test_override_settles_an_ambiguous_show(lib, override):
    lib.write_config(f'[[override]]\nmatch = "Mountain.Girl.*"\n{override}\n')
    lib.tree("series", "Mountain Girl [ITA]/Season 1/S01E01.mkv\nMountain Girl [ESP]/Season 1/S01E01.mkv\n"
                       "Mountain Girl (2001)/Season 1/S01E01.mkv")
    lib.tree("source", "Mountain.Girl.S01E03.mkv")
    (target,) = moves(only(lib)).values()
    assert target.split("/")[0] == "series:" + ("Mountain Girl [ESP]" if "esp" in override else "Mountain Girl (2001)")


def test_tags_come_from_the_file_names_when_the_folder_has_no_title(lib):
    lib.tree("source", "1080p [ESP]/Nonna.Rosa.S01E01.[ITA].mkv\n1080p [ESP]/Nonna.Rosa.S01E02.[ITA].mkv")
    assert {t.rsplit("/", 2)[0] for t in moves(only(lib)).values()} == {"series:Nonna Rosa [ITA]"}
