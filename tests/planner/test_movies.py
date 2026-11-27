"""The movie planner: one source candidate -> one plan item."""

import os
from pathlib import Path

import pytest

import tidyshelf_apply
from helpers.tree import MTIME
from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
FOLDER = "Winter.Lights.2004.1080p"


def items(lib, *paths):
    plan, _traces = build_plan(lib.cfg, list(paths) or None)
    return plan.to_dict()["items"]


def only(lib, *paths):
    (item,) = items(lib, *paths)
    return item


def ops(item, kind=None):
    return [op for op in item["ops"] if kind is None or op["op"] == kind]


def codes(item):
    return [f["code"] for f in item["findings"]]


def test_simple_movie_folder(lib):
    lib.tree("source", f"{FOLDER}/Winter.Lights.2004.1080p.mkv [100]\n{FOLDER}/readme.txt [5]")
    item = only(lib)
    assert (item["id"], item["kind"], item["status"], item["source"]) == ("movie-0001", "movie", "ready", f"source:{FOLDER}")
    assert item["ops"] == [
        {"op": "mkdir", "path": "movies:Winter Lights (2004)"},
        {"op": "mkdir", "path": f"source:.tidyshelf-trash/source/{FOLDER}"},
        {"op": "move", "from": f"source:{FOLDER}/Winter.Lights.2004.1080p.mkv",
         "to": "movies:Winter Lights (2004)/Winter Lights (2004).mkv", "size": 100, "mtime": MTIME},
        {"op": "move", "from": f"source:{FOLDER}/readme.txt",
         "to": f"source:.tidyshelf-trash/source/{FOLDER}/readme.txt", "size": 5, "mtime": MTIME},
        {"op": "set_mode", "path": "movies:Winter Lights (2004)"},
        {"op": "set_mode", "path": "movies:Winter Lights (2004)/Winter Lights (2004).mkv"},
        {"op": "delete", "path": f"source:{FOLDER}", "dir": True, "reason": "empty"},
    ]


def test_subtitles_go_to_subs_folder(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/Subs/English.srt", (SUBS / "en_plain.srt").read_bytes())
    item = only(lib)
    assert ops(item, "mkdir")[0] == {"op": "mkdir", "path": "movies:Winter Lights (2004)/Subs"}
    assert {op["to"] for op in ops(item, "move")} == {
        "movies:Winter Lights (2004)/Winter Lights (2004).mkv",
        "movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.srt"}
    assert [op["path"] for op in ops(item, "set_mode")] == [
        "movies:Winter Lights (2004)", "movies:Winter Lights (2004)/Subs",
        "movies:Winter Lights (2004)/Winter Lights (2004).mkv",
        "movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.srt"]
    assert [op["path"] for op in ops(item, "delete")] == [f"source:{FOLDER}/Subs", f"source:{FOLDER}"]


def test_loose_video_at_top_level(lib):
    lib.tree("source", "Winter.Lights.2004.mkv")
    lib.write("source", "Winter.Lights.2004.en.srt", (SUBS / "en_plain.srt").read_bytes())
    item = only(lib)
    assert item["status"] == "ready"
    assert len(ops(item, "move")) == 2 and ops(item, "delete") == []


def test_existing_good_folder_name_kept_with_tags(lib):
    lib.tree("source", "Winter Lights (2004) [ES]/x.mkv")
    assert ops(only(lib), "move")[0]["to"] == "movies:Winter Lights (2004) [ES]/Winter Lights (2004) [ES].mkv"


def test_extension_lowercased(lib):
    lib.tree("source", f"{FOLDER}/w.MKV")
    assert ops(only(lib), "move")[0]["to"].endswith("Winter Lights (2004).mkv")


def test_junk_delete_mode(lib):
    lib.write_config('[junk]\njunk_action = "delete"\n')
    lib.tree("source", f"{FOLDER}/w.mkv\n{FOLDER}/readme.txt [5]")
    item = only(lib)
    assert ops(item, "delete") == [
        {"op": "delete", "path": f"source:{FOLDER}/readme.txt", "size": 5, "mtime": MTIME, "reason": "junk"},
        {"op": "delete", "path": f"source:{FOLDER}", "dir": True, "reason": "empty"}]
    assert not any(op["op"] == "mkdir" and "trash" in op["path"] for op in item["ops"])


def test_trash_name_clash_gets_suffix(lib):
    lib.tree("source", f"{FOLDER}/w.mkv\n{FOLDER}/readme.txt\n.tidyshelf-trash/source/{FOLDER}/readme.txt")
    trash = [op["to"] for op in ops(only(lib), "move") if "trash" in op["to"]]
    assert trash == [f"source:.tidyshelf-trash/source/{FOLDER}/readme.1.txt"]


def test_series_go_to_the_series_planner(lib):
    lib.tree("source", "Some.Show.S01.720p/Some.Show.S01E01.720p.mkv\nSome.Show.S01.720p/Some.Show.S01E02.720p.mkv")
    item = only(lib)
    assert (item["id"], item["kind"], item["status"]) == ("series-0001", "series", "ready")


def test_no_video_skipped(lib):
    lib.tree("source", "Only.Junk/readme.txt")
    item = only(lib)
    assert (item["status"], item["ops"], codes(item)) == ("skipped", [], ["movie.no_video"])


def test_several_videos_needs_review(lib):
    lib.tree("source", f"{FOLDER}/a.mkv\n{FOLDER}/b.mkv")
    item = only(lib)
    assert (item["status"], item["ops"]) == ("needs_review", [])
    assert "movie.several_videos" in codes(item)


def test_missing_year_needs_review_no_ops(lib):
    lib.tree("source", "Winter.Lights.1080p/w.mkv")
    item = only(lib)
    assert (item["status"], item["ops"], codes(item)) == ("needs_review", [], ["parse.no_year"])


def test_year_override_makes_it_ready(lib):
    lib.write_config('[[override]]\nmatch = "Winter.Lights*"\nyear = 2004\n')
    lib.tree("source", "Winter.Lights.1080p/w.mkv")
    item = only(lib)
    assert item["status"] == "ready" and "parse.override" in codes(item)


def test_ignore_override_skips_silently(lib):
    lib.write_config('[[override]]\nmatch = "Winter*"\nignore = true\n')
    lib.tree("source", f"{FOLDER}/w.mkv")
    item = only(lib)
    assert (item["status"], item["ops"], item["findings"]) == ("skipped", [], [])


@pytest.mark.parametrize("on_exists, status", [("report", "needs_review"), ("skip", "skipped")])
def test_conflict_with_library(lib, on_exists, status):
    lib.write_config(f'[conflicts]\non_exists = "{on_exists}"\n')
    lib.tree("movies", "Winter Lights (2004)/Winter Lights (2004).mkv [300]")
    lib.tree("source", f"{FOLDER}/w.mkv [100]")
    item = only(lib)
    assert (item["status"], item["ops"]) == (status, [])
    (msg,) = [f["msg"] for f in item["findings"] if f["code"] == "conflict.exists"]
    assert "movies:Winter Lights (2004)" in msg and "300" in msg and "100" in msg


def test_same_movie_twice_in_source(lib):
    lib.tree("source", "Winter.Lights.2004.1080p/a.mkv\nWinter.Lights.2004.720p/b.mkv")
    first, second = items(lib)
    assert first["status"] == "ready"
    assert second["status"] == "needs_review" and "conflict.in_plan" in codes(second)


def test_library_case_difference_is_a_conflict(lib):
    lib.tree("movies", "winter lights (2004)/x.mkv")
    lib.tree("source", f"{FOLDER}/w.mkv")
    assert "conflict.exists" in codes(only(lib))


def test_symlink_inside_candidate_left_and_folder_kept(lib, tmp_path):
    lib.tree("source", f"{FOLDER}/w.mkv\n{FOLDER}/link -> {tmp_path}")
    item = only(lib)
    assert item["status"] == "ready"
    assert ops(item, "delete") == []
    assert "scan.left" in codes(item)


def test_title_of_only_illegal_chars_needs_review(lib):
    lib.write_config('[[override]]\nmatch = "Winter*"\ntitle = ":*?"\n')
    lib.tree("source", f"{FOLDER}/w.mkv")
    item = only(lib)
    assert item["status"] == "needs_review" and "name.unusable" in codes(item)


def test_subtitle_review_blocks_the_item(lib):
    tiny = b"1\n00:00:01,000 --> 00:00:02,000\n...\n"
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/one.srt", tiny)
    lib.write("source", f"{FOLDER}/two.srt", tiny + b"\n")
    item = only(lib)
    assert item["status"] == "needs_review" and "subtitle.unknown_language" in codes(item)


def test_vobsub_pair_moves_together(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/w.idx", b"id: en, index: 0\n")
    lib.write("source", f"{FOLDER}/w.sub", b"\x00" * 10)
    tos = sorted(op["to"] for op in ops(only(lib), "move") if "/Subs/" in op["to"])
    assert tos == ["movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.idx",
                       "movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.sub"]


def test_dropped_subtitles_go_to_trash_with_reason_in_findings(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/Subs/English.eng.srt", (SUBS / "ro.srt").read_bytes())
    item = only(lib)
    assert [op["to"] for op in ops(item, "move")][-1] == f"source:.tidyshelf-trash/source/{FOLDER}/Subs/English.eng.srt"
    assert "subtitle.language_mismatch" in codes(item) and "subtitle.dropped_language" in codes(item)


def test_undecodable_names_plan_round_trips(lib):
    src = os.fsencode(lib.roots["source"])
    os.makedirs(os.path.join(src, b"Caf\xe9.Film.2000"))
    open(os.path.join(src, b"Caf\xe9.Film.2000", b"f.mkv"), "wb").close()
    plan, _ = build_plan(lib.cfg)
    again = tidyshelf_apply.parse_plan(plan.dumps())
    assert again["items"] == plan.to_dict()["items"]
    assert again["items"][0]["status"] == "ready"


def test_plan_is_deterministic(lib):
    lib.tree("source", f"{FOLDER}/w.mkv\n{FOLDER}/readme.txt\nOther.Film.1999/o.mkv")
    lib.write("source", f"{FOLDER}/Subs/a.srt", (SUBS / "en_sdh.srt").read_bytes())
    a, _ = build_plan(lib.cfg, created="2026-01-01T00:00:00Z")
    b, _ = build_plan(lib.cfg, created="2026-01-01T00:00:00Z")
    assert a.dumps() == b.dumps() and a.plan_id == b.plan_id


def test_traces_per_item(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/Subs/English.eng.srt", (SUBS / "ro.srt").read_bytes())
    _plan, traces = build_plan(lib.cfg)
    lines = traces["movie-0001"]
    assert any("guessit" in line for line in lines)
    assert any("English.eng.srt" in line and "ro" in line and "cp1250" in line for line in lines)
    assert any("Winter Lights (2004)" in line for line in lines)


def test_loose_video_target_name(lib):
    lib.tree("source", "Bright.Harbour.2011.mp4")
    assert ops(only(lib), "move")[0]["to"] == "movies:Bright Harbour (2011)/Bright Harbour (2011).mp4"


@pytest.mark.parametrize("folder, video, target", [
    ("New Folder", "Cold.River.2010.720p.mkv", "movies:Cold River (2010)"),
    ("A", "Mission: Possible (1999).mkv", "movies:Mission - Possible (1999)"),
])
def test_title_and_year_from_one_name(lib, folder, video, target):
    lib.tree("source", f"{folder}/{video}")
    assert ops(only(lib), "mkdir")[0]["path"] == target


@pytest.mark.parametrize("folder", ["Bright Harbour (2011) - 720p BluRay x264-GRP", "Bright Harbour (2011) [1080p]"])
def test_good_looking_folder_with_release_words_is_renamed(lib, folder):
    lib.tree("source", f"{folder}/x.mkv")
    assert ops(only(lib), "mkdir")[0]["path"] == "movies:Bright Harbour (2011)"


def test_titles_that_clean_to_the_same_folder_do_not_crash(lib):
    lib.tree("source", "Who.Knows.2001/a.mkv\nWho.Knows?.2001/b.mkv")
    first, second = items(lib)
    assert first["status"] == "ready" and "conflict.in_plan" in codes(second)


def test_library_clash_uses_the_cleaned_name(lib):
    lib.tree("movies", "who knows (2001)/x.mkv")
    lib.tree("source", "Who.Knows?.2001/b.mkv")
    assert "conflict.exists" in codes(only(lib))


def test_dangling_symlink_in_library_is_a_conflict_not_a_crash(lib, tmp_path):
    lib.tree("movies", f"Winter Lights (2004) -> {tmp_path}/gone")
    lib.tree("source", f"{FOLDER}/w.mkv")
    assert "conflict.exists" in codes(only(lib))


def test_title_with_sample_in_it_is_the_movie(lib):
    lib.tree("source", "The.Sample.2010/The.Sample.2010.mkv [1000]")
    assert ops(only(lib), "mkdir")[0]["path"] == "movies:The Sample (2010)"


def test_small_sample_next_to_the_movie_is_junk(lib):
    lib.tree("source", f"{FOLDER}/w.mkv [1000]\n{FOLDER}/sample.mkv [10]")
    item = only(lib)
    assert item["status"] == "ready"
    assert any(op["to"].endswith("/sample.mkv") and "trash" in op["to"] for op in ops(item, "move"))


def _unreadable(path):
    os.chmod(path, 0)
    return not os.access(path, os.R_OK)


def test_unreadable_subtitle_needs_review_not_crash(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    sub = lib.write("source", f"{FOLDER}/w.en.srt", (SUBS / "en_plain.srt").read_bytes())
    try:
        if not _unreadable(sub):
            pytest.skip("running as root")
        item = only(lib)
    finally:
        os.chmod(sub, 0o644)
    assert item["status"] == "needs_review" and "scan.unreadable" in codes(item)


def test_unreadable_folder_needs_review_not_crash(lib):
    lib.tree("source", f"{FOLDER}/Subs/a.srt\n{FOLDER}/w.mkv\nOther.Film.1999/o.mkv")
    sub_dir = lib.roots["source"] / FOLDER / "Subs"
    try:
        if not _unreadable(sub_dir):
            pytest.skip("running as root")
        first, second = items(lib)
    finally:
        os.chmod(sub_dir, 0o755)
    assert "scan.unreadable" in codes(second) and second["status"] == "needs_review"
    assert first["status"] == "ready"


# --- spots where a broken change used to fail no test (review M10) ---

def test_sub_of_a_dropped_idx_is_trashed_with_it(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/w.en.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.write("source", f"{FOLDER}/w.idx", b"id: en, index: 0\n")
    lib.write("source", f"{FOLDER}/w.sub", b"\x00" * 10)
    item = only(lib)
    trashed = {op["from"].rsplit("/", 1)[1] for op in ops(item, "move") if "trash" in op["to"]}
    assert trashed == {"w.idx", "w.sub"}
    assert ops(item, "delete")[-1]["path"] == f"source:{FOLDER}"


def test_trash_targets_never_collide_within_a_plan(lib):
    lib.tree("source", f"{FOLDER}/w.mkv\n{FOLDER}/readme.txt\n{FOLDER}/readme.1.txt\n"
                       f".tidyshelf-trash/source/{FOLDER}/readme.txt")
    trash = sorted(op["to"].rsplit("/", 1)[1] for op in ops(only(lib), "move") if "trash" in op["to"])
    assert trash == ["readme.1.txt", "readme.2.txt"]


def test_kept_folder_name_with_illegal_characters_is_cleaned(lib):
    lib.tree("source", "Who Knows? (2001)/x.mkv")
    assert ops(only(lib), "mkdir")[0]["path"] == "movies:Who Knows (2001)"


def test_extras_folder_keeps_its_parent_folder(lib):
    lib.tree("source", f"{FOLDER}/w.mkv\n{FOLDER}/Featurettes/making-of.mkv")
    item = only(lib)
    assert item["status"] == "ready"
    assert ops(item, "delete") == []
    assert not any("Featurettes" in (op.get("from") or op.get("path")) for op in item["ops"])
    assert "scan.left" in codes(item)


def test_subtitle_extension_is_lowercased(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/w.en.SRT", (SUBS / "en_plain.srt").read_bytes())
    tos = [op["to"] for op in ops(only(lib), "move")]
    assert "movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.srt" in tos
