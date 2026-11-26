"""Items: --on-error stop/continue, rollback before deletes, partial during deletes."""

import os

import tidyshelf_apply as ta
from tidyshelf.plan import Plan


def two_items(env, break_second=True):
    env.tree("source", "a.mkv\nb.mkv")
    plan = Plan()
    plan.item("i1", "movie").move("source:a.mkv", "movies:a.mkv", *env.stat("source:a.mkv"))
    size, mtime = env.stat("source:b.mkv")
    plan.item("i2", "movie").move("source:b.mkv", "movies:b.mkv", size + (1 if break_second else 0), mtime)
    return plan


def test_stop_runs_nothing_if_any_item_fails_preflight(env):
    plan = two_items(env)
    before = env.snapshot()
    r = env.execute(plan)
    assert r.code == 3
    assert "i2" in r.err
    assert env.snapshot() == before


def test_continue_runs_the_good_items(env):
    r = env.execute(two_items(env), "--on-error", "continue")
    assert r.code == 3
    assert (env.movies / "a.mkv").exists()
    assert (env.source / "b.mkv").exists()


def failing_rename_after(monkeypatch, n):
    """Fail only rename number n+1 with EACCES (so the rollback's own renames still work)."""
    real = os.rename
    calls = {"n": 0}

    def rename(*a, **kw):
        calls["n"] += 1
        if calls["n"] == n + 1:
            raise PermissionError(13, "Permission denied")
        return real(*a, **kw)

    monkeypatch.setattr(ta.os, "rename", rename)


def rollback_plan(env):
    env.tree("source", "A/a.mkv\nA/a.srt")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.mkdir("movies:A (2000)/Subs")
    item.move("source:A/a.mkv", "movies:A (2000)/A (2000).mkv", *env.stat("source:A/a.mkv"))
    item.move("source:A/a.srt", "movies:A (2000)/Subs/A (2000).en.srt", *env.stat("source:A/a.srt"))
    return plan


def test_rollback_restores_moves_and_removes_created_dirs(env, monkeypatch):
    plan = rollback_plan(env)
    before = env.snapshot()
    failing_rename_after(monkeypatch, 1)
    r = env.execute(plan)
    assert r.code == 1  # an I/O error, not a refusal
    assert "rolled back" in r.err
    monkeypatch.undo()
    assert [p for p in env.snapshot() if not p.endswith(".tidyshelf.lock")] == before
    events = [e.get("t") for e in env.journal()]
    assert "rollback" in events


def test_rollback_keeps_preexisting_parent(env, monkeypatch):
    (env.movies / "A (2000)").mkdir()
    (env.movies / "A (2000)" / "poster.jpg").write_bytes(b"p")
    plan = rollback_plan(env)
    failing_rename_after(monkeypatch, 1)
    assert env.execute(plan).code == 1
    assert (env.movies / "A (2000)").is_dir()
    assert not (env.movies / "A (2000)" / "Subs").exists()
    assert (env.source / "A" / "a.mkv").exists()


def test_rollback_skipped_if_destination_changed(env, monkeypatch):
    plan = rollback_plan(env)
    real = os.rename
    calls = {"n": 0}

    def rename(a, b, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            real(a, b, **kw)
            fd = os.open(b, os.O_WRONLY | os.O_APPEND, dir_fd=kw["dst_dir_fd"])
            os.write(fd, b"more")  # someone writes to the file right after it lands
            os.close(fd)
            return None
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ta.os, "rename", rename)
    r = env.execute(plan)
    assert r.code == 1
    assert "not rolled back" in r.err
    assert (env.movies / "A (2000)" / "A (2000).mkv").exists()
    assert not (env.source / "A" / "a.mkv").exists()


def test_partial_on_delete_failure(env, monkeypatch):
    env.tree("source", "A/a.mkv\nA/x.txt")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.move("source:A/a.mkv", "movies:a.mkv", *env.stat("source:A/a.mkv"))
    item.delete_file("source:A/x.txt", *env.stat("source:A/x.txt"), reason="junk")

    def unlink(path, *a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ta.os, "unlink", unlink)
    r = env.execute(plan)
    assert r.code == 1
    assert "partial" in r.err
    assert (env.movies / "a.mkv").exists(), "no rollback once deletes started"
    assert [e for e in env.journal() if e.get("t") == "item"][-1]["result"] == "partial"


def test_needs_review_items_exit_4_and_get_no_ops(env):
    env.tree("source", "a.mkv")
    plan = Plan()
    plan.item("i1", "movie").move("source:a.mkv", "movies:a.mkv", *env.stat("source:a.mkv"))
    plan.item("i2", "movie", status="needs_review").finding("warn", "year.missing", "no year")
    assert env.run(plan).code == 4
    r = env.execute(plan)
    assert r.code == 4
    assert (env.movies / "a.mkv").exists()


def test_only_and_skip(env):
    plan = two_items(env, break_second=False)
    r = env.execute(plan, "--only", "i2")
    assert r.code == 0, r.err
    assert (env.source / "a.mkv").exists() and (env.movies / "b.mkv").exists()
    r = env.execute(plan, "--skip", "i2")
    assert r.code == 0, r.err
    assert (env.movies / "a.mkv").exists()
