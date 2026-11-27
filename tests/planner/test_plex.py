"""The plex preset: Movies/Title (Year)/Title (Year).ext with subtitles beside the video; series as
Show (Year)/Season 01/Show (Year) - s01e01 - Title.ext. Audit accepts a correct plex tree."""

import json
from pathlib import Path

from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "plex.plan.json"
PLEX = "[naming]\npreset = \"plex\"\n"


def plex(lib):
    lib.write_config(PLEX)
    return lib.cfg


def without_tool(doc):
    return {k: v for k, v in doc.items() if k != "tool"}


def golden_tree(lib):
    lib.write("source", "Amber.Valley.2009.1080p/Amber.Valley.2009.1080p.mkv", b"v" * 500)
    lib.write("source", "Amber.Valley.2009.1080p/Subs/English.eng.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.write("source", "Amber.Valley.2009.1080p/Subs/Spanish.spa.srt", (SUBS / "es.srt").read_bytes())
    lib.write("source", "Amber.Valley.2009.1080p/Subs/English SDH.eng.srt", (SUBS / "en_sdh.srt").read_bytes())
    pack = "Quiet.Bay.2015.S01.720p"
    for name, size in (("Quiet.Bay.S01E01.Arrival.mkv", 300), ("Quiet.Bay.S01E02E03.Two.Parter.mkv", 600),
                       ("Specials/Quiet.Bay.S00E01.Christmas.mkv", 100)):
        lib.write("source", f"{pack}/{name}", b"v" * size)
    lib.write("source", f"{pack}/Quiet.Bay.S01E01.eng.srt", (SUBS / "en_plain.srt").read_bytes())


def test_plex_preset_is_valid_and_overrides_templates(lib):
    from tidyshelf import config
    lib.write_config(PLEX)
    assert config.validate(lib.cfg).ok
    n = lib.cfg["naming"]
    assert (n["season_folder"], n["movie_subs_folder"]) == ("Season {season:02}", "")
    lib.write_config(PLEX + "season_folder = \"S{season}\"\n")  # the user still wins
    assert lib.cfg["naming"]["season_folder"] == "S{season}"


def test_golden_plex(lib):
    golden_tree(lib)
    plan, _ = build_plan(plex(lib), created="2026-11-27T09:00:00Z")
    movie, show = plan.to_dict()["items"]
    mv = {op["from"].rsplit("/", 1)[-1]: op["to"] for op in movie["ops"] if op["op"] == "move"}
    assert mv["Amber.Valley.2009.1080p.mkv"] == "movies:Amber Valley (2009)/Amber Valley (2009).mkv"
    writes = [op["to"] for op in movie["ops"] if op["op"] == "write"]  # the cp1252 fixture is converted to UTF-8
    assert "movies:Amber Valley (2009)/Amber Valley (2009).es.srt" in writes
    assert mv["English.eng.srt"] == "movies:Amber Valley (2009)/Amber Valley (2009).en.srt"
    assert not [op for op in movie["ops"] if op["op"] == "mkdir" and op["path"].startswith("movies:") and op["path"].endswith("/Subs")]
    base = "series:Quiet Bay (2015)"
    sv = {op["from"].rsplit("/", 1)[-1]: op["to"] for op in show["ops"] if op["op"] == "move"}
    assert sv["Quiet.Bay.S01E01.Arrival.mkv"] == f"{base}/Season 01/Quiet Bay (2015) - s01e01 - Arrival.mkv"
    assert sv["Quiet.Bay.S01E02E03.Two.Parter.mkv"] == f"{base}/Season 01/Quiet Bay (2015) - s01e02-e03 - Two Parter.mkv"
    assert sv["Quiet.Bay.S00E01.Christmas.mkv"] == f"{base}/Season 00/Quiet Bay (2015) - s00e01 - Christmas.mkv"
    assert sv["Quiet.Bay.S01E01.eng.srt"] == f"{base}/Season 01/Quiet Bay (2015) - s01e01 - Arrival.en.srt"
    assert without_tool(plan.to_dict()) == without_tool(json.loads(GOLDEN.read_text(encoding="utf-8")))


def test_audit_of_a_correct_plex_tree_is_empty(lib):
    lib.tree("movies", "Old Mill (1987)/Old Mill (1987).mkv")
    lib.write("movies", "Old Mill (1987)/Old Mill (1987).en.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.tree("series", "Old Coast (2001)/Season 01/Old Coast (2001) - s01e01 - Arrival.mkv\n"
                       "Old Coast (2001)/Season 01/Old Coast (2001) - s01e02-e03 - Two Parter.mkv\n"
                       "Old Coast (2001)/Season 00/Old Coast (2001) - s00e01.mkv\n"
                       "Old Coast/Season 02/Old Coast - s02e01.mkv")
    lib.write("series", "Old Coast (2001)/Season 01/Old Coast (2001) - s01e01 - Arrival.en.srt",
              (SUBS / "en_plain.srt").read_bytes())
    plan, _ = build_plan(plex(lib), audit=True)
    assert plan.to_dict()["items"] == []


def test_audit_turns_a_kodi_tree_into_plex_with_moves_only(lib):
    lib.tree("movies", "Old Mill (1987)/Old Mill (1987).mkv")
    lib.write("movies", "Old Mill (1987)/Subs/Old Mill (1987).en.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.tree("series", "Old Coast (2001)/Season 1/S01E01 - Arrival.mkv\nOld Coast (2001)/Season 1/S01E02-E03.mkv")
    plan, _ = build_plan(plex(lib), audit=True)
    ops = [op for it in plan.to_dict()["items"] for op in it["ops"]]
    assert {op["op"] for op in ops} <= {"mkdir", "move", "set_mode"}
    moves = {op["from"]: op["to"] for op in ops if op["op"] == "move"}
    assert moves == {
        "movies:Old Mill (1987)/Subs/Old Mill (1987).en.srt": "movies:Old Mill (1987)/Old Mill (1987).en.srt",
        "series:Old Coast (2001)/Season 1/S01E01 - Arrival.mkv":
            "series:Old Coast (2001)/Season 01/Old Coast (2001) - s01e01 - Arrival.mkv",
        "series:Old Coast (2001)/Season 1/S01E02-E03.mkv":
            "series:Old Coast (2001)/Season 01/Old Coast (2001) - s01e02-e03.mkv"}


def test_existing_show_with_a_tag_or_year_still_matches(lib):
    lib.tree("series", "Tide Line [ITA]/Season 01/Tide Line - s01e01.mkv\nHarbour Row (2015)/Season 01/Harbour Row (2015) - s01e01.mkv")
    lib.write("source", "Tide.Line.S01E02.mkv", b"v" * 50)
    lib.write("source", "Harbour.Row.S01E02.Next.mkv", b"v" * 50)
    plan, _ = build_plan(plex(lib))
    moves = {op["to"] for it in plan.to_dict()["items"] for op in it["ops"] if op["op"] == "move"}
    assert moves == {"series:Tide Line [ITA]/Season 01/Tide Line - s01e02.mkv",
                     "series:Harbour Row (2015)/Season 01/Harbour Row (2015) - s01e02 - Next.mkv"}
