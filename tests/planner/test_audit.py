"""plan --audit: the libraries checked against the standard; a consistent library is an empty plan."""

import os
from pathlib import Path

import tidyshelf_apply
from tidyshelf import cli
from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"


def audit(lib):
    plan, traces = build_plan(lib.cfg, audit=True)
    return plan.to_dict()["items"]


def moves(item):
    return {op["from"]: op["to"] for op in item["ops"] if op["op"] == "move"}


def codes(item):
    return [f["code"] for f in item["findings"]]


def consistent_library(lib):
    lib.tree("movies", "Old Mill (1987)/Old Mill (1987).mkv\nOld Mill (1987) [ES]/Old Mill (1987) [ES].mkv")
    lib.write("movies", "Old Mill (1987)/Subs/Old Mill (1987).en.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.tree("series", "Old Coast/Season 1/S01E01 - Arrival.mkv\nOld Coast/Season 1/S01E02-E03 - Two Parter.mkv\n"
                       "Old Coast/Season 00/S00E01.mkv\nOld Coast/Featurettes/making-of.mkv")
    lib.write("series", "Old Coast/Season 1/S01E01 - Arrival.en.srt", (SUBS / "en_plain.srt").read_bytes())


def test_consistent_library_is_an_empty_plan(lib):
    consistent_library(lib)
    assert audit(lib) == []


def test_misnamed_video_is_renamed_in_place(lib):
    lib.tree("movies", "Bright Harbour (2011)/bright.harbour.2011.1080p.mkv")
    (item,) = audit(lib)
    assert moves(item) == {"movies:Bright Harbour (2011)/bright.harbour.2011.1080p.mkv":
                           "movies:Bright Harbour (2011)/Bright Harbour (2011).mkv"}
    assert not [op for op in item["ops"] if op["op"] == "delete"]


def test_folder_not_following_the_standard_moves_and_reports_the_empty_folder(lib):
    lib.tree("movies", "Winter.Lights.2004.720p/Winter.Lights.2004.720p.mkv")
    (item,) = audit(lib)
    assert moves(item) == {"movies:Winter.Lights.2004.720p/Winter.Lights.2004.720p.mkv":
                           "movies:Winter Lights (2004)/Winter Lights (2004).mkv"}
    assert not [op for op in item["ops"] if op["op"] == "delete"]
    (msg,) = [f["msg"] for f in item["findings"] if f["code"] == "audit.empty_folder"]
    assert "movies:Winter.Lights.2004.720p" in msg and "by hand" in msg


def test_loose_subtitle_goes_to_subs_and_junk_to_trash(lib):
    lib.tree("movies", "Bright Harbour (2011)/Bright Harbour (2011).mkv\nBright Harbour (2011)/cover.jpg")
    lib.write("movies", "Bright Harbour (2011)/English.srt", (SUBS / "en_plain.srt").read_bytes())
    (item,) = audit(lib)
    assert moves(item) == {
        "movies:Bright Harbour (2011)/English.srt": "movies:Bright Harbour (2011)/Subs/Bright Harbour (2011).en.srt",
        "movies:Bright Harbour (2011)/cover.jpg": "source:.tidyshelf-trash/movies/Bright Harbour (2011)/cover.jpg"}


def test_season_folder_renamed_and_episode_names_fixed(lib):
    lib.tree("series", "Harbour Lights/Season 01/Harbour.Lights.S01E01.Pilot.mkv\nHarbour Lights/Season 1/S01E02.mkv")
    (item,) = audit(lib)
    assert moves(item) == {"series:Harbour Lights/Season 01/Harbour.Lights.S01E01.Pilot.mkv":
                           "series:Harbour Lights/Season 1/S01E01 - Pilot.mkv"}
    assert "audit.empty_folder" in codes(item)


def test_quality_warning_on_a_consistent_movie_is_visible(lib, probes):
    lib.tree("movies", "Old Mill (1987)/Old Mill (1987).mkv")
    probes({"Old Mill (1987).mkv": "uhd_large"})
    (item,) = audit(lib)
    assert (item["status"], item["ops"], codes(item)) == ("skipped", [], ["quality.large"])


def test_name_that_is_not_utf8_is_reported(lib):
    movies = os.fsencode(lib.roots["movies"])
    os.makedirs(os.path.join(movies, b"Caf\xe9 Noir (2003)"))
    open(os.path.join(movies, b"Caf\xe9 Noir (2003)", b"x.mkv"), "wb").close()
    (item,) = audit(lib)
    assert "name.not_utf8" in codes(item)


def test_planning_a_source_name_that_is_not_utf8_is_reported(lib):
    src = os.fsencode(lib.roots["source"])
    os.makedirs(os.path.join(src, b"Caf\xe9.Noir.2003"))
    open(os.path.join(src, b"Caf\xe9.Noir.2003", b"f.mkv"), "wb").close()
    (item,) = build_plan(lib.cfg)[0].to_dict()["items"]
    assert "name.not_utf8" in codes(item) and item["status"] == "ready"


def test_cli_audit_refuses_paths(lib, capsys):
    code = cli.main(["plan", "--audit", "movies:X", "--config", str(lib.config_path)])
    assert code == 1 and "--audit" in capsys.readouterr().err


def test_audit_fix_then_audit_again_is_empty(lib, capsys, tmp_path):
    lib.tree("movies", "Winter.Lights.2004.720p/Winter.Lights.2004.720p.mkv\nBright Harbour (2011)/b.mkv\n"
                       "Bright Harbour (2011)/cover.jpg")
    lib.tree("series", "Harbour Lights/Season 1/Harbour.Lights.S01E01.mkv")
    target = tmp_path / "audit.json"
    assert cli.main(["plan", "--audit", "--out", str(target), "--config", str(lib.config_path)]) == 2
    assert tidyshelf_apply.main([str(target), "--config", str(lib.config_path), "--execute", "--yes"]) == 0
    capsys.readouterr()
    (lib.roots["movies"] / "Winter.Lights.2004.720p").rmdir()  # the empty folder, removed by hand
    assert audit(lib) == []


def test_audited_show_does_not_join_itself(lib):
    lib.tree("series", "Harbour Lights/Season 1/Harbour.Lights.S01E01.mkv")
    (item,) = audit(lib)
    assert "series.joins_existing" not in codes(item)
