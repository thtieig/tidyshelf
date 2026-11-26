"""Path guard: syntax, containment, symlinks, special files, protected files, [exclude]."""

import os
import socket

import pytest

from tidyshelf.plan import Plan


def one_move(env, src, dst, size=0, mtime=0):
    plan = Plan()
    plan.item("i1", "movie").move(src, dst, size, mtime)
    return plan


@pytest.mark.parametrize("ref, needle", [
    ("source:../x", "'..'"),
    ("source:a/../../x", "'..'"),
    ("source:./x", "'.'"),
    ("source:/etc/passwd", "absolute"),
    ("/etc/passwd", "root:path"),
    ("source:a//b", "empty segment"),
    ("source:a/", "empty segment"),
    ("source:a\x00b", "NUL"),
    ("other:a", "unknown root 'other'"),
    ("source:", "empty path"),
])
def test_bad_refs_are_invalid_plans(env, ref, needle):
    r = env.run(one_move(env, ref, "movies:x.mkv"))
    assert r.code == 3
    assert needle in r.err, r.err
    r = env.run(one_move(env, "source:x.mkv", ref))
    assert r.code == 3


def test_sibling_prefix_root_is_not_inside(env):
    """movies = .../Movies; a symlinked parent into .../Movies2 must not count as inside."""
    sibling = env.nas / "Movies2"
    sibling.mkdir()
    (env.movies / "sneaky").symlink_to(sibling)
    env.tree("source", "a.mkv")
    r = env.run(one_move(env, "source:a.mkv", "movies:sneaky/a.mkv", *env.stat("source:a.mkv")))
    assert r.code == 3
    assert "symlink" in r.err
    assert not (sibling / "a.mkv").exists()


def test_symlinked_parent_escape_refused(env, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mkv").write_bytes(b"s")
    (env.source / "link").symlink_to(outside)
    st = os.stat(outside / "secret.mkv")
    r = env.execute(one_move(env, "source:link/secret.mkv", "movies:secret.mkv", st.st_size, st.st_mtime_ns // 10**9))
    assert r.code == 3
    assert "symlink" in r.err
    assert (outside / "secret.mkv").exists()


def test_symlinked_parent_inside_root_also_refused(env):
    env.tree("source", "real/a.mkv")
    (env.source / "alias").symlink_to(env.source / "real")
    r = env.run(one_move(env, "source:alias/a.mkv", "movies:a.mkv", *env.stat("source:real/a.mkv")))
    assert r.code == 3
    assert "symlink" in r.err


def test_symlink_as_move_source_refused(env):
    env.tree("source", "real.mkv\nlink.mkv -> real.mkv")
    st = env.stat("source:real.mkv")
    r = env.execute(one_move(env, "source:link.mkv", "movies:x.mkv", *st))
    assert r.code == 3
    assert "symlink" in r.err
    assert (env.source / "link.mkv").is_symlink()


def test_symlink_as_delete_target_refused(env):
    env.tree("source", "real.txt\nlink.txt -> real.txt")
    plan = Plan()
    plan.item("i1", "movie").delete_file("source:link.txt", *env.stat("source:real.txt"), reason="junk")
    r = env.execute(plan)
    assert r.code == 3
    assert "symlink" in r.err
    assert (env.source / "link.txt").is_symlink()


def test_symlink_dir_delete_refused(env):
    env.tree("source", "d/\nlinkdir -> d")
    plan = Plan()
    plan.item("i1", "movie").delete_dir("source:linkdir", reason="empty")
    r = env.execute(plan)
    assert r.code == 3
    assert (env.source / "linkdir").is_symlink()


def test_fifo_refused(env):
    os.mkfifo(env.source / "pipe.mkv")
    r = env.execute(one_move(env, "source:pipe.mkv", "movies:x.mkv", 0, env.stat("source:pipe.mkv")[1]))
    assert r.code == 3
    assert "not a regular file" in r.err


def test_socket_refused(env, tmp_path):
    path = env.source / "s.mkv"
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(path))
    except OSError:
        pytest.skip("cannot bind a unix socket here")
    try:
        plan = Plan()
        plan.item("i1", "movie").delete_file("source:s.mkv", 0, env.stat("source:s.mkv")[1], reason="junk")
        r = env.execute(plan)
        assert r.code == 3
        assert "not a regular file" in r.err
    finally:
        sock.close()


def test_lock_file_is_protected(env):
    (env.source / ".tidyshelf.lock").write_bytes(b"")
    plan = Plan()
    plan.item("i1", "movie").delete_file("source:.tidyshelf.lock", *env.stat("source:.tidyshelf.lock"), reason="junk")
    r = env.run(plan)
    assert r.code == 3
    assert "protected" in r.err


def test_plan_and_journal_files_are_protected(env):
    """A plan kept inside the source root may not move itself or its journal."""
    env.plan_path = env.source / "plan.json"
    plan = Plan()
    plan.item("i1", "movie").move("source:plan.json", "movies:plan.json", 0, 0)
    r = env.run(plan)
    assert r.code == 3
    assert "protected" in r.err
    plan = Plan()
    plan.item("i1", "movie").mkdir("source:plan.journal.jsonl")
    r = env.run(plan)
    assert r.code == 3
    assert "protected" in r.err


def test_excluded_paths_refused(env):
    env.write_config("[exclude]\npatterns = ['^movies:_keep-out(/|$)']\n")
    env.tree("source", "a.mkv")
    r = env.run(one_move(env, "source:a.mkv", "movies:_keep-out/a.mkv", *env.stat("source:a.mkv")))
    assert r.code == 3
    assert "excluded" in r.err
    plan = Plan()
    plan.item("i1", "movie").mkdir("movies:_keep-out")
    assert env.run(plan).code == 3
    plan = Plan()
    plan.item("i1", "movie").mkdir("movies:_keep-out-not")
    assert env.run(plan).code == 2


def test_trash_contents_cannot_be_a_source(env):
    env.tree("source", ".tidyshelf-trash/movies/x.txt")
    plan = Plan()
    plan.item("i1", "movie").move("source:.tidyshelf-trash/movies/x.txt", "movies:x.txt", *env.stat("source:.tidyshelf-trash/movies/x.txt"))
    r = env.run(plan)
    assert r.code == 3
    assert "trash" in r.err
