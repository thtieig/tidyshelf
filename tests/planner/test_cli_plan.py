"""tidyshelf plan / tidyshelf explain."""

import json
from pathlib import Path

import tidyshelf_apply
from tidyshelf import cli

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
FOLDER = "Winter.Lights.2004.1080p"


def run(lib, capsys, *argv, config=True):
    capsys.readouterr()
    args = list(argv) + (["--config", str(lib.config_path)] if config else [])
    code = cli.main(args)
    out, err = capsys.readouterr()
    return code, out, err


def film(lib):
    lib.tree("source", f"{FOLDER}/Winter.Lights.2004.1080p.mkv [100]\n{FOLDER}/readme.txt [5]")


def test_plan_summary_and_exit_2(lib, capsys):
    film(lib)
    code, out, err = run(lib, capsys, "plan")
    assert code == 2, err
    assert f"movie-0001  ready  source:{FOLDER} -> movies:Winter Lights (2004)" in out
    assert "1 items (1 ready, 0 needs review, 0 skipped)" in out


def test_plan_out_file_round_trips_through_apply_validation(lib, capsys, tmp_path):
    film(lib)
    target = tmp_path / "plan.json"
    code, out, err = run(lib, capsys, "plan", "--out", str(target))
    assert code == 2, err
    doc = tidyshelf_apply.parse_plan(target.read_text(encoding="utf-8"))
    tidyshelf_apply.validate_plan(doc)
    assert tidyshelf_apply.plan_id(doc) in out
    assert f"tidyshelf-apply {target}" in out


def test_plan_out_dash_writes_json_to_stdout_summary_to_stderr(lib, capsys):
    film(lib)
    code, out, err = run(lib, capsys, "plan", "--out", "-")
    assert code == 2
    assert json.loads(out)["format"] == 1
    assert "movie-0001" in err


def test_plan_out_inside_a_root_refused(lib, capsys):
    film(lib)
    target = lib.roots["movies"] / "plan.json"
    code, _out, err = run(lib, capsys, "plan", "--out", str(target))
    assert code == 1 and "inside" in err
    assert not target.exists()


def test_plan_exit_4_with_needs_review(lib, capsys):
    lib.tree("source", "Winter.Lights.1080p/w.mkv")
    code, out, _err = run(lib, capsys, "plan")
    assert code == 4
    assert "parse.no_year" in out


def test_plan_exit_0_empty_source(lib, capsys):
    assert run(lib, capsys, "plan")[0] == 0


def test_plan_bad_config_exit_1(lib, capsys):
    lib.write_config("[naming]\nmovie_folder = \"{nope}\"\n")
    code, _out, err = run(lib, capsys, "plan")
    assert code == 1 and "nope" in err


def test_plan_missing_root_exit_1(lib, capsys):
    lib.roots["series"].rmdir()
    code, _out, err = run(lib, capsys, "plan")
    assert code == 1 and "roots.series" in err


def test_plan_path_outside_source_exit_1(lib, capsys):
    code, _out, err = run(lib, capsys, "plan", "/etc")
    assert code == 1 and "source root" in err


def test_plan_json_output(lib, capsys):
    film(lib)
    code, out, _err = run(lib, capsys, "plan", "--json")
    doc = json.loads(out)
    assert code == 2 and doc["exit"] == 2
    (item,) = doc["items"]
    assert item["target"] == "movies:Winter Lights (2004)" and item["status"] == "ready"
    assert len(doc["plan_id"]) == 12


def test_explain_shows_trace_and_ops(lib, capsys):
    film(lib)
    lib.write("source", f"{FOLDER}/Subs/English.eng.srt", (SUBS / "ro.srt").read_bytes())
    code, out, _err = run(lib, capsys, "explain", f"source:{FOLDER}")
    assert code == 0
    assert "guessit" in out and "English.eng.srt" in out and "cp1250" in out
    assert "movies:Winter Lights (2004)/Winter Lights (2004).mkv" in out
    assert "language_mismatch" in out


def test_explain_json(lib, capsys):
    film(lib)
    code, out, _err = run(lib, capsys, "explain", f"source:{FOLDER}", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["source"] == f"source:{FOLDER}"
    assert doc["trace"] and doc["item"]["status"] == "ready"


def test_explain_unknown_path_exit_1(lib, capsys):
    code, _out, err = run(lib, capsys, "explain", "source:Nope")
    assert code == 1 and "Nope" in err


def test_plan_then_apply_end_to_end(lib, capsys, tmp_path):
    film(lib)
    lib.write("source", f"{FOLDER}/Subs/English SDH.eng.srt", (SUBS / "en_sdh.srt").read_bytes())
    target = tmp_path / "plan.json"
    assert run(lib, capsys, "plan", "--out", str(target))[0] == 2
    code = tidyshelf_apply.main([str(target), "--config", str(lib.config_path), "--execute", "--yes"])
    capsys.readouterr()
    assert code == 0
    movie = lib.roots["movies"] / "Winter Lights (2004)"
    assert (movie / "Winter Lights (2004).mkv").exists()
    assert (movie / "Subs" / "Winter Lights (2004).en.sdh.srt").exists()
    assert (lib.roots["source"] / ".tidyshelf-trash" / "source" / FOLDER / "readme.txt").exists()
    assert not (lib.roots["source"] / FOLDER).exists()
    code, out, _err = run(lib, capsys, "plan")
    assert code == 0 and "0 items" in out  # idempotent: nothing left to do


def test_undecodable_names_print_without_crashing(lib, capsys):
    import os
    src = os.fsencode(lib.roots["source"])
    os.makedirs(os.path.join(src, b"Caf\xe9.Noir.2003"))
    open(os.path.join(src, b"Caf\xe9.Noir.2003", b"f.mkv"), "wb").close()
    for argv in (["plan"], ["plan", "--json"], ["explain", "source:Caf\udce9.Noir.2003"]):
        code, out, err = run(lib, capsys, *argv)
        assert code in (0, 2), (argv, err)
        assert "Traceback" not in err


def test_series_plan_then_apply_end_to_end(lib, capsys, tmp_path):
    lib.tree("source", "Harbour.Lights.S01.720p/Harbour.Lights.S01E01.Pilot.mkv [10]\n"
                       "Harbour.Lights.S01.720p/Harbour.Lights.S01E02.mkv [10]\nHarbour.Lights.S01.720p/x.nfo")
    target = tmp_path / "plan.json"
    assert run(lib, capsys, "plan", "--out", str(target))[0] == 2
    assert tidyshelf_apply.main([str(target), "--config", str(lib.config_path), "--execute", "--yes"]) == 0
    capsys.readouterr()
    season = lib.roots["series"] / "Harbour Lights" / "Season 1"
    assert sorted(p.name for p in season.iterdir()) == ["S01E01 - Pilot.mkv", "S01E02.mkv"]
    code, out, _err = run(lib, capsys, "plan")
    assert code == 0 and "0 items" in out
