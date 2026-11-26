"""Scanning the source root into candidates, and indexing the movie library."""

import os

import pytest

from tidyshelf.scan import library_index, scan_source


def by_ref(cands):
    return {c.ref: c for c in cands}


def test_one_candidate_per_top_level_entry_sorted(lib):
    lib.tree("source", "B.Film.2001/b.mkv\nA.Film.2000/a.mkv\nc.Film.2002.mkv")
    assert [c.ref for c in scan_source(lib.cfg)] == ["source:A.Film.2000", "source:B.Film.2001", "source:c.Film.2002.mkv"]


def test_trash_lock_journal_and_excluded_are_not_candidates(lib):
    lib.write_config("[exclude]\npatterns = ['^source:_keep(/|$)']\n")
    lib.tree("source", ".tidyshelf-trash/source/x.txt\n.tidyshelf.lock\nplan.journal.jsonl\n_keep/a.mkv\nA/a.mkv")
    assert [c.ref for c in scan_source(lib.cfg)] == ["source:A"]


def test_files_classified(lib):
    lib.tree("source", "\n".join([
        "Film/film.mkv", "Film/Subs/English.srt", "Film/film.en.srt", "Film/sample.mkv",
        "Film/readme.txt", "Film/.DS_Store", "Film/Film.MP4.idx", "Film/cover.jpg",
    ]))
    (c,) = scan_source(lib.cfg)
    assert c.is_dir and c.path == str(lib.roots["source"] / "Film")
    assert c.videos == ["source:Film/film.mkv"]
    assert c.subs == ["source:Film/Film.MP4.idx", "source:Film/Subs/English.srt", "source:Film/film.en.srt"]
    assert c.junk == ["source:Film/.DS_Store", "source:Film/cover.jpg", "source:Film/readme.txt",
                      "source:Film/sample.mkv"]
    assert c.left == [] and c.dirs == ["source:Film/Subs"]


def test_loose_video_takes_its_sidecars(lib):
    lib.tree("source", "Film.2000.mkv\nFilm.2000.en.srt\nFilm.2000.es.srt\nother.srt\nThumbs.db")
    cands = by_ref(scan_source(lib.cfg))
    assert sorted(cands) == ["source:Film.2000.mkv", "source:Thumbs.db", "source:other.srt"]
    film = cands["source:Film.2000.mkv"]
    assert not film.is_dir and film.videos == ["source:Film.2000.mkv"]
    assert film.subs == ["source:Film.2000.en.srt", "source:Film.2000.es.srt"]
    assert cands["source:other.srt"].videos == [] and cands["source:other.srt"].subs == ["source:other.srt"]


def test_symlinks_never_followed_and_left(lib, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mkv").write_bytes(b"x")
    lib.tree("source", f"Film/film.mkv\nFilm/more -> {outside}\nFilm/link.srt -> {outside}/secret.mkv\nTop -> {outside}")
    cands = by_ref(scan_source(lib.cfg))
    film = cands["source:Film"]
    assert film.left == [("source:Film/link.srt", "symlink"), ("source:Film/more", "symlink")]
    assert "secret" not in repr(film)
    assert cands["source:Top"].left == [("source:Top", "symlink")] and cands["source:Top"].videos == []


def test_special_file_left(lib):
    lib.tree("source", "Film/film.mkv")
    os.mkfifo(lib.roots["source"] / "Film" / "pipe")
    (c,) = scan_source(lib.cfg)
    assert c.left == [("source:Film/pipe", "special file")]


def test_excluded_inside_candidate_left(lib):
    lib.write_config("[exclude]\npatterns = ['/keep\\.nfo$']\n")
    lib.tree("source", "Film/film.mkv\nFilm/keep.nfo")
    assert scan_source(lib.cfg)[0].left == [("source:Film/keep.nfo", "excluded")]


def test_extras_folder_left_untouched(lib):
    lib.tree("source", "Film/film.mkv\nFilm/Featurettes/making-of.mkv\nFilm/extras/deleted.mkv")
    (c,) = scan_source(lib.cfg)
    assert c.videos == ["source:Film/film.mkv"]
    assert c.left == [("source:Film/Featurettes", "extras folder"), ("source:Film/extras", "extras folder")]
    assert c.dirs == []


def test_dirs_deepest_first(lib):
    lib.tree("source", "Film/a/b/c/x.srt\nFilm/a/y.srt\nFilm/z/w.txt\nFilm/f.mkv")
    assert scan_source(lib.cfg)[0].dirs == ["source:Film/a/b/c", "source:Film/a/b", "source:Film/a", "source:Film/z"]


def test_paths_select_candidates(lib):
    lib.tree("source", "A/a.mkv\nB/b.mkv")
    src = lib.roots["source"]
    assert [c.ref for c in scan_source(lib.cfg, ["source:B"])] == ["source:B"]
    assert [c.ref for c in scan_source(lib.cfg, [str(src / "A")])] == ["source:A"]


@pytest.mark.parametrize("path", ["/etc", "source:../x", "movies:A", "source:", "SOURCE_ROOT"])
def test_paths_must_be_inside_source(lib, path):
    path = str(lib.roots["source"]) if path == "SOURCE_ROOT" else path
    with pytest.raises(ValueError, match="source"):
        scan_source(lib.cfg, [path])


def test_library_index_casefolds_and_ignores_suffix(lib):
    lib.tree("movies", "Winter Lights (2004) [ES]/x.mkv\nOther (1999)/o.mkv\nnot a movie/\nloose.mkv")
    assert library_index(lib.cfg) == {"winter lights (2004)": "movies:Winter Lights (2004) [ES]",
                                      "other (1999)": "movies:Other (1999)"}


def test_undecodable_name_round_trips(lib):
    raw = b"Caf\xe9 Film 2000"
    os.makedirs(os.path.join(os.fsencode(lib.roots["source"]), raw))
    with open(os.path.join(os.fsencode(lib.roots["source"]), raw, b"f.mkv"), "wb"):
        pass
    (c,) = scan_source(lib.cfg)
    assert "\udce9" in c.ref
    assert os.fsencode(c.ref.split(":", 1)[1]) == raw
    assert c.videos == [c.ref + "/f.mkv"]


def test_path_to_a_loose_video_still_takes_its_sidecars(lib):
    lib.tree("source", "Film.2000.mkv\nFilm.2000.en.srt\nOther.mkv")
    (c,) = scan_source(lib.cfg, ["source:Film.2000.mkv"])
    assert c.subs == ["source:Film.2000.en.srt"]


def test_fixture_files_have_a_fixed_mtime(lib):
    from helpers.tree import MTIME
    lib.tree("source", "A/a.mkv")
    lib.write("source", "A/a.srt", b"x")
    assert {os.lstat(lib.roots["source"] / "A" / n).st_mtime for n in ("a.mkv", "a.srt")} == {MTIME}
