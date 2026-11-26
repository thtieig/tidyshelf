"""A folder swapped for a symlink between the check and the change is never followed.

The swap is done at the moment the `begin` journal line is written, which is the last thing
before the filesystem call: the widest real-world race window.
"""

import os

import pytest

import tidyshelf_apply as ta
from tidyshelf.plan import Plan


@pytest.fixture
def outside(env):
    path = env.tmp / "outside"
    path.mkdir()
    return path


def swap_on_begin(monkeypatch, op_name, swap):
    real_log = ta.Journal.log
    fired = []

    def log(self, entry):
        real_log(self, entry)
        if entry.get("t") == "begin" and entry["op"]["op"] == op_name and not fired:
            fired.append(True)
            swap()

    monkeypatch.setattr(ta.Journal, "log", log)
    return fired


def to_symlink(path, target):
    os.rename(path, str(path) + ".real")
    os.symlink(target, path)


def test_move_into_swapped_destination_folder_is_refused(env, outside, monkeypatch):
    env.tree("source", "a.mkv [3]")
    (env.movies / "X").mkdir()
    plan = Plan()
    plan.item("i1", "movie").move("source:a.mkv", "movies:X/a.mkv", *env.stat("source:a.mkv"))
    fired = swap_on_begin(monkeypatch, "move", lambda: to_symlink(env.movies / "X", outside))
    r = env.execute(plan)
    assert fired
    assert r.code == 3, r.err
    assert not (outside / "a.mkv").exists()
    assert (env.source / "a.mkv").exists()


def test_move_from_swapped_source_folder_is_refused(env, outside, monkeypatch):
    env.tree("source", "d/a.mkv [3]")
    (outside / "a.mkv").write_bytes(b"xyz")
    os.utime(outside / "a.mkv", ns=(os.stat(env.source / "d" / "a.mkv").st_mtime_ns,) * 2)
    plan = Plan()
    plan.item("i1", "movie").move("source:d/a.mkv", "movies:a.mkv", *env.stat("source:d/a.mkv"))
    fired = swap_on_begin(monkeypatch, "move", lambda: to_symlink(env.source / "d", outside))
    r = env.execute(plan)
    assert fired
    assert r.code == 3, r.err
    assert (outside / "a.mkv").exists(), "a file outside the roots was moved in"
    assert not (env.movies / "a.mkv").exists()


def test_delete_in_swapped_folder_is_refused(env, outside, monkeypatch):
    env.tree("source", "d/junk.txt [4]")
    (outside / "junk.txt").write_bytes(b"keep")
    plan = Plan()
    plan.item("i1", "movie").delete_file("source:d/junk.txt", *env.stat("source:d/junk.txt"), reason="junk")
    fired = swap_on_begin(monkeypatch, "delete", lambda: to_symlink(env.source / "d", outside))
    r = env.execute(plan)
    assert fired
    assert r.code == 3, r.err
    assert (outside / "junk.txt").read_bytes() == b"keep"


def test_set_mode_on_swapped_file_does_not_follow_it(env, outside, monkeypatch):
    env.write_config('[owner]\nfile_mode = "0600"\n')
    env.tree("source", "a.mkv")
    os.chmod(env.source / "a.mkv", 0o644)
    target = outside / "target"
    target.write_bytes(b"t")
    os.chmod(target, 0o644)
    plan = Plan()
    plan.item("i1", "movie").set_mode("source:a.mkv")

    def swap():
        os.unlink(env.source / "a.mkv")
        os.symlink(target, env.source / "a.mkv")

    fired = swap_on_begin(monkeypatch, "set_mode", swap)
    r = env.execute(plan)
    assert fired
    assert r.code == 3, r.err
    assert os.stat(target).st_mode & 0o777 == 0o644


def test_set_mode_in_swapped_folder_does_not_follow_it(env, outside, monkeypatch):
    env.write_config('[owner]\nfile_mode = "0600"\n')
    env.tree("movies", "X/a.mkv")
    os.chmod(env.movies / "X" / "a.mkv", 0o644)
    (outside / "a.mkv").write_bytes(b"")
    os.chmod(outside / "a.mkv", 0o644)
    plan = Plan()
    plan.item("i1", "movie").set_mode("movies:X/a.mkv")
    fired = swap_on_begin(monkeypatch, "set_mode", lambda: to_symlink(env.movies / "X", outside))
    r = env.execute(plan)
    assert fired
    assert r.code == 3, r.err
    assert os.stat(outside / "a.mkv").st_mode & 0o777 == 0o644
