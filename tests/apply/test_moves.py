"""Moves: never overwrite, never cross roots the wrong way, never copy, re-check the source."""

import errno
import os

import pytest

import tidyshelf_apply as ta
from tidyshelf.plan import Plan


def move_plan(env, src="source:a.mkv", dst="movies:A (2000)/A (2000).mkv", mkdir=True):
    plan = Plan()
    item = plan.item("i1", "movie")
    if mkdir:
        item.mkdir(dst.rsplit("/", 1)[0])
    item.move(src, dst, *env.stat(src))
    return plan


def test_simple_move_executes(env):
    env.tree("source", "a.mkv [10]")
    r = env.execute(move_plan(env))
    assert r.code == 0, r.err
    assert (env.movies / "A (2000)" / "A (2000).mkv").read_bytes() == b"x" * 10
    assert not (env.source / "a.mkv").exists()


@pytest.mark.parametrize("existing", ["file", "dangling", "dir"])
def test_destination_exists_refused(env, existing):
    env.tree("source", "a.mkv [10]")
    dst = env.movies / "A (2000)" / "A (2000).mkv"
    dst.parent.mkdir()
    if existing == "file":
        dst.write_bytes(b"keep")
    elif existing == "dangling":
        dst.symlink_to(env.movies / "nowhere")
    else:
        dst.mkdir()
    before = env.snapshot()
    r = env.execute(move_plan(env))
    assert r.code == 3
    assert "exists" in r.err
    assert env.snapshot() == before
    if existing == "file":
        assert dst.read_bytes() == b"keep"


def test_library_to_source_refused(env):
    env.tree("movies", "A/a.mkv")
    r = env.run(move_plan(env, "movies:A/a.mkv", "source:a.mkv", mkdir=False))
    assert r.code == 3
    assert "into source" in r.err


def test_library_to_trash_allowed(env):
    env.tree("movies", "A/poster.jpg [3]")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.mkdir("source:.tidyshelf-trash/movies/A")
    item.move("movies:A/poster.jpg", "source:.tidyshelf-trash/movies/A/poster.jpg", *env.stat("movies:A/poster.jpg"))
    r = env.execute(plan)
    assert r.code == 0, r.err
    assert (env.source / ".tidyshelf-trash" / "movies" / "A" / "poster.jpg").exists()


def test_library_to_library_allowed(env):
    env.tree("movies", "A/a.mkv")
    r = env.execute(move_plan(env, "movies:A/a.mkv", "movies:A/A (2000).mkv", mkdir=False))
    assert r.code == 0, r.err


def test_chained_moves_refused(env):
    env.tree("source", "a.mkv\nb.mkv")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.move("source:a.mkv", "source:b2.mkv", *env.stat("source:a.mkv"))
    item.move("source:b2.mkv", "movies:c.mkv", *env.stat("source:a.mkv"))
    r = env.run(plan)
    assert r.code == 3
    assert "another op" in r.err


def test_swapped_moves_refused(env):
    env.tree("source", "a.mkv\nb.mkv")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.move("source:a.mkv", "source:b.mkv", *env.stat("source:a.mkv"))
    item.move("source:b.mkv", "source:a.mkv", *env.stat("source:b.mkv"))
    r = env.run(plan)
    assert r.code == 3


def test_same_source_twice_refused(env):
    env.tree("source", "a.mkv")
    plan = Plan()
    plan.item("i1", "movie").move("source:a.mkv", "movies:x.mkv", *env.stat("source:a.mkv"))
    plan.item("i2", "movie").move("source:a.mkv", "movies:y.mkv", *env.stat("source:a.mkv"))
    r = env.run(plan)
    assert r.code == 3
    assert "more than one op" in r.err


def test_cross_device_detected_in_preflight(env, monkeypatch):
    env.tree("source", "a.mkv")
    real_lstat = os.lstat
    movies = str(env.movies)

    class FakeStat:
        def __init__(self, st):
            self._st = st

        def __getattr__(self, name):
            return self._st.st_dev + 1 if name == "st_dev" else getattr(self._st, name)

    def fake_lstat(path, *a, **kw):
        st = real_lstat(path, *a, **kw)
        return FakeStat(st) if os.fspath(path).startswith(movies) else st

    monkeypatch.setattr(ta.os, "lstat", fake_lstat)
    r = env.execute(move_plan(env))
    assert r.code == 3
    assert "different filesystem" in r.err
    assert (env.source / "a.mkv").exists()


def test_exdev_at_rename_is_a_refusal_not_a_copy(env, monkeypatch):
    env.tree("source", "a.mkv")

    def fake_rename(*a, **kw):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(ta.os, "rename", fake_rename)
    r = env.execute(move_plan(env))
    assert r.code == 3
    assert "different filesystem" in r.err
    assert (env.source / "a.mkv").exists()
    assert not (env.movies / "A (2000)" / "A (2000).mkv").exists()


@pytest.mark.parametrize("change", ["size", "mtime"])
def test_source_changed_refused(env, change):
    env.tree("source", "a.mkv [10]")
    plan = move_plan(env)
    if change == "size":
        (env.source / "a.mkv").write_bytes(b"y" * 11)
    else:
        os.utime(env.source / "a.mkv", (1, 1))
    r = env.execute(plan)
    assert r.code == 3
    assert "changed" in r.err
    assert (env.source / "a.mkv").exists()


def test_missing_source_refused(env):
    env.tree("source", "a.mkv")
    plan = move_plan(env)
    (env.source / "a.mkv").unlink()
    r = env.execute(plan)
    assert r.code == 3
    assert "missing" in r.err


def test_missing_destination_parent_refused(env):
    env.tree("source", "a.mkv")
    r = env.execute(move_plan(env, mkdir=False))
    assert r.code == 3
    assert "parent" in r.err


def test_mkdir_creates_nested_levels(env):
    env.tree("source", "a.mkv")
    plan = Plan()
    item = plan.item("i1", "series")
    item.mkdir("series:Show/Season 1")
    item.move("source:a.mkv", "series:Show/Season 1/S01E01.mkv", *env.stat("source:a.mkv"))
    r = env.execute(plan)
    assert r.code == 0, r.err
    assert (env.series / "Show" / "Season 1" / "S01E01.mkv").exists()


def test_mkdir_over_a_file_refused(env):
    env.tree("movies", "A")
    plan = Plan()
    plan.item("i1", "movie").mkdir("movies:A/B")
    r = env.run(plan)
    assert r.code == 3
    assert "not a directory" in r.err
