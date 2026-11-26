"""Lock, journal, crash resume, undo."""

import fcntl
import json
import os

import pytest

import tidyshelf_apply as ta
from helpers.plans import movie_plan
from tidyshelf.plan import Plan


def test_concurrent_lock_refused(env):
    plan = movie_plan(env)
    lock = env.source / ".tidyshelf.lock"
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = env.execute(plan)
    assert r.code == 3
    assert "lock" in r.err
    assert (env.source / "Some.Film.1999.1080p" / "film.mkv").exists()


def test_journal_written_on_execute(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    entries = env.journal()
    assert entries[0]["t"] == "header" and entries[0]["plan_id"] == plan.plan_id
    assert entries[0]["plan_size"] == env.plan_path.stat().st_size
    kinds = [(e["t"], e.get("op", {}).get("op")) for e in entries[1:]]
    assert ("mkdir", None) in kinds
    assert ("begin", "move") in kinds and ("done", "move") in kinds
    assert entries[-1] == {"t": "item", "item": "movie-0001", "result": "ok"}
    text = env.journal_path.read_text(encoding="utf-8")
    assert str(env.nas) not in text, "journal uses root:rel refs only"


def test_one_mkdir_entry_per_level(env):
    env.tree("source", "a.mkv")
    plan = Plan()
    item = plan.item("i1", "series")
    item.mkdir("series:Show/Season 1")
    item.move("source:a.mkv", "series:Show/Season 1/S01E01.mkv", *env.stat("source:a.mkv"))
    assert env.execute(plan).code == 0
    assert [e["path"] for e in env.journal() if e["t"] == "mkdir"] == ["series:Show", "series:Show/Season 1"]


def test_begin_is_on_disk_before_rename(env, monkeypatch):
    plan = movie_plan(env)
    seen = []
    real = os.rename

    def rename(*a, **kw):
        lines = env.journal_path.read_text(encoding="utf-8").splitlines()
        seen.append(json.loads(lines[-1]))
        return real(*a, **kw)

    monkeypatch.setattr(ta.os, "rename", rename)
    assert env.execute(plan).code == 0
    assert seen and seen[0]["t"] == "begin" and seen[0]["op"]["op"] == "move"


def test_mkdir_is_on_disk_before_the_folder_is_made(env, monkeypatch):
    plan = movie_plan(env)
    seen = []
    real = os.mkdir

    def mkdir(*a, **kw):
        seen.append(json.loads(env.journal_path.read_text(encoding="utf-8").splitlines()[-1]))
        return real(*a, **kw)

    monkeypatch.setattr(ta.os, "mkdir", mkdir)
    assert env.execute(plan).code == 0
    assert seen == [{"t": "mkdir", "item": "movie-0001", "path": "movies:Some Film (1999)"}]


def test_crash_resume_skips_done_ops(env, monkeypatch):
    plan = movie_plan(env)

    class Crash(BaseException):
        pass

    def unlink(*a, **kw):
        raise Crash()

    monkeypatch.setattr(ta.os, "unlink", unlink)
    with pytest.raises(Crash):
        env.execute(plan)
    monkeypatch.undo()
    assert (env.movies / "Some Film (1999)" / "Some Film (1999).mkv").exists()
    r = env.run(plan)
    assert r.code == 2  # the delete is still to do
    assert "done" in r.out
    r = env.execute(plan)
    assert r.code == 0, r.err
    assert not (env.source / "Some.Film.1999.1080p").exists()
    assert env.run(plan).code == 0  # nothing left


def test_rerun_of_finished_plan_is_a_noop(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    snap = env.snapshot()
    assert env.execute(plan).code == 0
    assert env.snapshot() == snap


def test_journal_of_another_plan_refused(env):
    plan = movie_plan(env)
    env.journal_path.write_text(json.dumps({"t": "header", "plan_id": "ffffffffffff"}) + "\n", encoding="utf-8")
    os.chmod(env.journal_path, 0o644)
    r = env.execute(plan)
    assert r.code == 3
    assert "another plan" in r.err


def run_undo(env, *extra):
    """Undo takes the plan; the journal is found next to it, as for a normal run."""
    return env.run(None, str(env.plan_path), "--undo", *extra)


def write_journal(env, entries):
    env.journal_path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    os.chmod(env.journal_path, 0o644)


def test_undo_reverses_moves_only(env):
    plan = movie_plan(env)
    before = env.snapshot()
    assert env.execute(plan).code == 0
    r = run_undo(env)
    assert r.code == 2, r.err  # dry-run first
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 0, r.err
    after = env.snapshot()
    assert "to-sort/Some.Film.1999.1080p/film.mkv" in after
    assert "to-sort/Some.Film.1999.1080p/readme.txt" not in after, "deletes are not undone"
    assert not (env.movies / "Some Film (1999)").exists(), "created dirs removed"
    assert set(after) - {"to-sort/.tidyshelf.lock"} == set(before) - {"to-sort/Some.Film.1999.1080p/readme.txt"}


def test_undo_refuses_if_a_destination_changed(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    dst = env.movies / "Some Film (1999)" / "Some Film (1999).mkv"
    dst.write_bytes(b"changed")
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 3
    assert "changed" in r.err
    assert dst.read_bytes() == b"changed"


def test_undo_is_all_or_nothing(env):
    env.tree("source", "a.mkv\nb.mkv")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.move("source:a.mkv", "movies:a.mkv", *env.stat("source:a.mkv"))
    item.move("source:b.mkv", "movies:b.mkv", *env.stat("source:b.mkv"))
    assert env.execute(plan).code == 0
    (env.movies / "b.mkv").unlink()
    assert run_undo(env, "--execute", "--yes").code == 3
    assert (env.movies / "a.mkv").exists()


def test_undo_with_tampered_journal_path_refused(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    lines = env.journal_path.read_text(encoding="utf-8").splitlines()
    tampered = []
    for line in lines:
        e = json.loads(line)
        if e["t"] == "done" and e["op"]["op"] == "move":
            e["op"]["from"] = "source:../../escape.mkv"
        tampered.append(json.dumps(e))
    env.journal_path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 3
    assert "'..'" in r.err
    assert (env.movies / "Some Film (1999)" / "Some Film (1999).mkv").exists()


def test_undo_twice_is_a_noop(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    assert run_undo(env, "--execute", "--yes").code == 0
    snap = env.snapshot()
    assert run_undo(env, "--execute", "--yes").code == 0
    assert env.snapshot() == snap


def test_begin_without_done_resolved_by_state(env):
    """Crash between rename and the `done` line: undo still sees the move happened."""
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    lines = [json.loads(x) for x in env.journal_path.read_text(encoding="utf-8").splitlines()]
    lines = [e for e in lines if not (e["t"] == "done" and e["op"]["op"] == "move")]
    env.journal_path.write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")
    assert run_undo(env, "--execute", "--yes").code == 0
    assert (env.source / "Some.Film.1999.1080p" / "film.mkv").exists()


def test_rerun_with_nothing_to_do_takes_no_confirmation(env, monkeypatch):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    lines = len(env.journal())
    monkeypatch.setattr(ta, "_isatty", lambda: False)
    assert env.run(plan, "--execute").code == 0  # no --yes needed: nothing would change
    assert len(env.journal()) == lines


def test_undo_ignores_a_journal_move_the_plan_does_not_have(env):
    """A planted journal line cannot make undo move a library file into source."""
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    env.tree("movies", "Other (2000)/Other (2000).mkv [10]")
    size, mtime = env.stat("movies:Other (2000)/Other (2000).mkv")
    entries = env.journal()
    entries.append({"t": "done", "item": "movie-0001", "n": 1, "op": {
        "op": "move", "from": "source:evil/x.mkv", "to": "movies:Other (2000)/Other (2000).mkv",
        "size": size, "mtime": mtime}})
    write_journal(env, entries)
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 3
    assert "not in the plan" in r.err
    assert (env.movies / "Other (2000)" / "Other (2000).mkv").exists()
    assert not (env.source / "evil").exists()


def test_undo_ignores_a_journal_mkdir_the_plan_does_not_have(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    (env.movies / "Unrelated").mkdir()
    write_journal(env, env.journal() + [{"t": "mkdir", "item": "movie-0001", "path": "movies:Unrelated"}])
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 3
    assert "not in the plan" in r.err
    assert (env.movies / "Unrelated").is_dir()


def test_undo_of_a_journal_from_another_plan_refused(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    entries = env.journal()
    entries[0]["plan_id"] = "ffffffffffff"
    write_journal(env, entries)
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 3
    assert "another plan" in r.err


@pytest.mark.parametrize("entry", [
    {"t": "done", "item": ["x"], "n": 0, "op": {"op": "mkdir", "path": "movies:a"}},
    {"t": "done", "item": "movie-0001", "n": {}, "op": {"op": "mkdir", "path": "movies:a"}},
    {"t": "mkdir", "item": "movie-0001", "path": 5},
])
def test_undo_with_malformed_journal_line_is_refused_cleanly(env, entry):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    write_journal(env, env.journal() + [entry])
    assert run_undo(env, "--execute", "--yes").code == 3


def test_existing_journal_writable_by_others_is_refused(env):
    plan = movie_plan(env)
    write_journal(env, [{"t": "header", "plan_id": plan.plan_id}])
    os.chmod(env.journal_path, 0o666)
    r = env.execute(plan)
    assert r.code == 3
    assert "writable by others" in r.err
    assert (env.source / "Some.Film.1999.1080p" / "film.mkv").exists()


def test_undo_json_output(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    r = run_undo(env, "--json")
    assert r.code == 2
    doc = r.json()
    assert doc["plan_id"] == plan.plan_id and doc["exit"] == 2
    assert doc["moves"] == [{"from": "movies:Some Film (1999)/Some Film (1999).mkv",
                             "to": "source:Some.Film.1999.1080p/film.mkv"}]


def test_undo_failing_midway_reports_partial(env, monkeypatch):
    env.tree("source", "a.mkv\nb.mkv")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.move("source:a.mkv", "movies:a.mkv", *env.stat("source:a.mkv"))
    item.move("source:b.mkv", "movies:b.mkv", *env.stat("source:b.mkv"))
    assert env.execute(plan).code == 0
    real, calls = ta.os.rename, []

    def rename(*a, **kw):
        calls.append(a)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied")
        return real(*a, **kw)

    monkeypatch.setattr(ta.os, "rename", rename)
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 1
    assert "partial" in r.err and "1 of 2" in r.err
    monkeypatch.undo()
    assert run_undo(env, "--execute", "--yes").code == 0  # run again to finish
    assert (env.source / "a.mkv").exists() and (env.source / "b.mkv").exists()


def test_undo_given_the_journal_instead_of_the_plan_says_so(env):
    plan = movie_plan(env)
    assert env.execute(plan).code == 0
    r = env.run(None, str(env.journal_path), "--undo")
    assert r.code == 1
    assert "plan file, not the journal" in r.err


def test_io_error_names_the_plan_paths(env):
    env.tree("source", "a.mkv")
    (env.movies / "ro").mkdir()
    plan = Plan()
    plan.item("i1", "movie").move("source:a.mkv", "movies:ro/a.mkv", *env.stat("source:a.mkv"))
    os.chmod(env.movies / "ro", 0o555)
    try:
        if os.access(env.movies / "ro", os.W_OK):
            pytest.skip("running as root")
        r = env.execute(plan)
    finally:
        os.chmod(env.movies / "ro", 0o755)
    assert r.code == 1
    assert "source:a.mkv -> movies:ro/a.mkv" in r.err
