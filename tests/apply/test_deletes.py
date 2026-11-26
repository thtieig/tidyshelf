"""Deletes: only under source, never the root, only empty dirs, capped."""

import pytest

from tidyshelf.plan import Plan


def delete_plan(env, *refs):
    plan = Plan()
    item = plan.item("i1", "movie")
    for ref in refs:
        item.delete_file(ref, *env.stat(ref), reason="junk")
    return plan


def test_delete_file_executes(env):
    env.tree("source", "A/readme.txt [5]")
    r = env.execute(delete_plan(env, "source:A/readme.txt"))
    assert r.code == 0, r.err
    assert not (env.source / "A" / "readme.txt").exists()


@pytest.mark.parametrize("root", ["movies", "series"])
def test_delete_outside_source_refused(env, root):
    env.tree(root, "A/readme.txt")
    r = env.execute(delete_plan(env, f"{root}:A/readme.txt"))
    assert r.code == 3
    assert "only under source" in r.err
    assert (getattr(env, root) / "A" / "readme.txt").exists()


def test_delete_root_refused(env):
    plan = Plan()
    plan.item("i1", "movie").delete_dir("source:", reason="empty")
    r = env.run(plan)
    assert r.code == 3


def test_delete_non_empty_dir_refused(env):
    env.tree("source", "A/keep.mkv")
    plan = Plan()
    plan.item("i1", "movie").delete_dir("source:A", reason="empty")
    r = env.execute(plan)
    assert r.code == 3
    assert "not empty" in r.err
    assert (env.source / "A" / "keep.mkv").exists()


def test_delete_of_a_just_moved_path_refused(env):
    env.tree("source", "a.txt")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.move("source:a.txt", "source:b.txt", *env.stat("source:a.txt"))
    item.delete_file("source:b.txt", *env.stat("source:a.txt"), reason="junk")
    r = env.run(plan)
    assert r.code == 3
    assert "another op" in r.err


def test_delete_file_changed_refused(env):
    env.tree("source", "a.txt [5]")
    plan = delete_plan(env, "source:a.txt")
    (env.source / "a.txt").write_bytes(b"changed!")
    r = env.execute(plan)
    assert r.code == 3
    assert "changed" in r.err
    assert (env.source / "a.txt").exists()


def test_count_cap(env):
    env.write_config("[safety]\nmax_delete_files = 2\n")
    env.tree("source", "a.txt\nb.txt\nc.txt")
    r = env.execute(delete_plan(env, "source:a.txt", "source:b.txt", "source:c.txt"))
    assert r.code == 3
    assert "cap" in r.err
    assert len(list(env.source.iterdir())) == 3


def test_byte_cap(env):
    env.write_config("[safety]\nmax_delete_bytes = 10\n")
    env.tree("source", "a.txt [6]\nb.txt [6]")
    r = env.execute(delete_plan(env, "source:a.txt", "source:b.txt"))
    assert r.code == 3
    assert "cap" in r.err


def test_default_count_cap_is_200(env):
    env.tree("source", "\n".join(f"f{i}.txt" for i in range(201)))
    r = env.run(delete_plan(env, *(f"source:f{i}.txt" for i in range(201))))
    assert r.code == 3
    assert "cap" in r.err


def test_allow_large_delete(env):
    env.write_config("[safety]\nmax_delete_files = 1\n")
    env.tree("source", "a.txt\nb.txt")
    r = env.execute(delete_plan(env, "source:a.txt", "source:b.txt"), "--allow-large-delete")
    assert r.code == 0, r.err
    assert list(env.source.iterdir()) == [] or [p.name for p in env.source.iterdir()] == [".tidyshelf.lock"]


def test_empty_dir_removals_not_counted(env):
    env.write_config("[safety]\nmax_delete_files = 1\n")
    env.tree("source", "A/a.txt\nB/")
    plan = delete_plan(env, "source:A/a.txt")
    plan.items[0].delete_dir("source:A", reason="empty")
    plan.items[0].delete_dir("source:B", reason="empty")
    r = env.execute(plan)
    assert r.code == 0, r.err


def test_emptied_dir_delete_in_dry_run(env):
    """The overlay knows A/ is empty once its files are moved/deleted, before anything really happens."""
    env.tree("source", "A/a.mkv\nA/x.txt\nA/sub/")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.move("source:A/a.mkv", "movies:a.mkv", *env.stat("source:A/a.mkv"))
    item.delete_file("source:A/x.txt", *env.stat("source:A/x.txt"), reason="junk")
    item.delete_dir("source:A/sub", reason="empty")
    item.delete_dir("source:A", reason="empty")
    before = env.snapshot()
    r = env.run(plan)
    assert r.code == 2, r.err
    assert env.snapshot() == before
    assert env.execute(plan).code == 0
    assert not (env.source / "A").exists()


def test_parent_before_child_delete_refused(env):
    env.tree("source", "A/sub/")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.delete_dir("source:A", reason="empty")
    item.delete_dir("source:A/sub", reason="empty")
    r = env.run(plan)
    assert r.code == 3
    assert "not empty" in r.err
