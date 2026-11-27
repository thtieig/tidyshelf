"""The write op: a new file with exactly the planned bytes, made from a file that must be unchanged."""

import base64
import hashlib
import json
import os

import pytest

import tidyshelf_apply as ta
from tidyshelf.plan import Plan

ORIGINAL = b"1\n00:00:01,000 --> 00:00:02,000\nAd\n\n2\n00:00:03,000 --> 00:00:04,000\nHello\n"
CLEANED = b"1\n00:00:03,000 --> 00:00:04,000\nHello\n"
DEST = "movies:A (2000)/Subs/A (2000).en.srt"
TRASHED = "source:.tidyshelf-trash/source/Film/en.srt"


def write_plan(env, data=CLEANED, to=DEST, orig=ORIGINAL, trash=True, **tweak):
    env.tree("source", "Film/a.mkv [10]")
    (env.source / "Film" / "en.srt").write_bytes(orig)
    os.utime(env.source / "Film" / "en.srt", (1_700_000_000, 1_700_000_000))
    size, mtime = env.stat("source:Film/en.srt")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.mkdir(to.rsplit("/", 1)[0])
    if trash:
        item.mkdir(TRASHED.rsplit("/", 1)[0])
    item.write(to, data, "source:Film/en.srt", hashlib.sha256(orig).hexdigest(), size, mtime)
    if trash:
        item.move("source:Film/en.srt", TRASHED, size, mtime)
    item.set_mode(to)
    for op in item.ops:
        if op["op"] == "write":
            op.update(tweak)
    return plan


def dest(env):
    return env.movies / "A (2000)" / "Subs" / "A (2000).en.srt"


def test_happy_path(env):
    plan = write_plan(env)
    r = env.run(plan)
    assert r.code == 2, r.err
    assert f"write    {DEST}  (cleaned from source:Film/en.srt)" in r.out
    assert "data_b64" not in r.out and not dest(env).exists()  # dry-run changes nothing
    r = env.execute(plan)
    assert r.code == 0, r.err
    assert dest(env).read_bytes() == CLEANED
    assert (env.source / ".tidyshelf-trash" / "source" / "Film" / "en.srt").read_bytes() == ORIGINAL
    assert not (env.source / "Film" / "en.srt").exists()
    assert not [p for p in dest(env).parent.iterdir() if p.name.startswith(".tidyshelf-tmp")]
    entries = env.journal()
    done = [e for e in entries if e["t"] == "done" and e["op"]["op"] == "write"]
    assert len(done) == 1 and "data_b64" not in done[0]["op"] and done[0]["op"]["sha256"]
    assert max(len(json.dumps(e)) for e in entries) < 2000  # no data in the journal


def test_rerun_is_a_no_op(env):
    plan = write_plan(env)
    assert env.execute(plan).code == 0
    r = env.run(plan)
    assert r.code == 0, r.err
    assert env.execute(plan).code == 0 and dest(env).read_bytes() == CLEANED


def test_resume_after_the_write_but_before_the_move(env):
    plan = write_plan(env)
    d = dest(env)
    d.parent.mkdir(parents=True)
    d.write_bytes(CLEANED)  # as if a run stopped right after the write
    r = env.execute(plan)
    assert r.code == 0, r.err
    assert d.read_bytes() == CLEANED and not (env.source / "Film" / "en.srt").exists()


def test_json_output_has_no_data(env):
    r = env.run(write_plan(env), "--json")
    assert "data_b64" not in r.out and json.loads(r.out)["items"][0]["ops"][2]["op"] == "write"


@pytest.mark.parametrize("change", ["content", "size", "mtime", "symlink", "gone"])
def test_source_changed_refused(env, change):
    plan = write_plan(env)
    src = env.source / "Film" / "en.srt"
    if change == "content":  # same size and mtime, different bytes: only the hash can tell
        src.write_bytes(ORIGINAL.replace(b"Ad", b"AD"))
        os.utime(src, (1_700_000_000, 1_700_000_000))
    elif change == "size":
        src.write_bytes(ORIGINAL + b"x")
        os.utime(src, (1_700_000_000, 1_700_000_000))
    elif change == "mtime":
        os.utime(src, (1_700_000_001, 1_700_000_001))
    elif change == "symlink":
        src.unlink()
        src.symlink_to(env.source / "Film" / "a.mkv")
    else:
        src.unlink()
    before = env.snapshot()
    r = env.execute(plan)
    assert r.code == 3, r.err
    assert not dest(env).exists() and env.snapshot() == before


def test_source_changed_after_preflight_leaves_nothing_behind(env, monkeypatch):
    plan = write_plan(env)
    real = ta._check_write_source
    calls = []

    def sneaky(guard, op):  # the file changes between the pre-flight and the real run
        calls.append(1)
        if len(calls) == 2:
            (env.source / "Film" / "en.srt").write_bytes(ORIGINAL.replace(b"Ad", b"AD"))
            os.utime(env.source / "Film" / "en.srt", (1_700_000_000, 1_700_000_000))
        return real(guard, op)

    monkeypatch.setattr(ta, "_check_write_source", sneaky)
    r = env.execute(plan)
    assert r.code == 3 and "content changed" in r.err
    assert not dest(env).exists()


def test_data_hash_mismatch_refused(env):
    plan = write_plan(env, sha256=hashlib.sha256(b"something else").hexdigest())
    r = env.execute(plan)
    assert r.code == 3 and "sha256" in r.err
    assert not dest(env).exists()


def test_data_changed_but_hash_kept_is_refused(env):
    plan = write_plan(env, data_b64=base64.b64encode(b"tampered").decode())
    r = env.execute(plan)
    assert r.code == 3 and "sha256" in r.err
    assert not dest(env).exists()


@pytest.mark.parametrize("bad", ["not base64!", "abc", "====", "é"])
def test_bad_base64_refused(env, bad):
    r = env.execute(write_plan(env, data_b64=bad))
    assert r.code == 3 and not dest(env).exists()


@pytest.mark.parametrize("existing", ["file", "same", "dangling", "dir"])
def test_destination_exists_refused(env, existing):
    plan = write_plan(env)
    d = dest(env)
    d.parent.mkdir(parents=True)
    if existing == "file":
        d.write_bytes(b"keep")
    elif existing == "same":
        pass
    elif existing == "dangling":
        d.symlink_to(env.movies / "nowhere")
    else:
        d.mkdir()
    if existing == "same":
        d.write_bytes(b"keep")
    before = env.snapshot()
    r = env.execute(plan)
    assert r.code == 3 and "exists" in r.err
    assert env.snapshot() == before
    if existing in ("file", "same"):
        assert d.read_bytes() == b"keep"


def test_destination_appearing_in_the_last_moment_is_not_replaced(env, monkeypatch):
    plan = write_plan(env)
    real = ta._create_new
    d = dest(env)

    def late(guard, full, data):
        d.write_bytes(b"theirs")  # created after the checks, before the link
        return real(guard, full, data)

    monkeypatch.setattr(ta, "_create_new", late)
    r = env.execute(plan)
    assert r.code == 3 and d.read_bytes() == b"theirs"
    assert not [p for p in d.parent.iterdir() if p.name.startswith(".tidyshelf-tmp")]


def test_oversize_refused(env):
    big = b"x" * (ta.MAX_WRITE_BYTES + 1)
    r = env.execute(write_plan(env, data=big))
    assert r.code == 3 and "larger" in r.err
    assert not dest(env).exists()


def test_data_at_the_cap_is_accepted(env):
    big = b"x" * ta.MAX_WRITE_BYTES
    assert env.execute(write_plan(env, data=big, orig=b"y" * 10)).code == 0
    assert dest(env).stat().st_size == ta.MAX_WRITE_BYTES


@pytest.mark.parametrize("to", ["source:Film/new.srt", "source:.tidyshelf-trash/x.srt"])
def test_write_outside_the_libraries_refused(env, to):
    r = env.execute(write_plan(env, to=to, trash=False))
    assert r.code == 3 and "only into movies or series" in r.err
    assert not (env.source / "Film" / "new.srt").exists()


def test_write_from_a_library_or_trash_refused(env):
    for src in ("movies:A/en.srt", "source:.tidyshelf-trash/source/en.srt"):
        r = env.execute(write_plan(env, trash=False, src=src))
        assert r.code == 3 and "under source" in r.err


def test_other_validation(env):
    for tweak in ({"sha256": "ABC"}, {"src_sha256": "0" * 63}, {"size": -1}, {"extra": 1}):
        assert env.execute(write_plan(env, **tweak)).code == 3, tweak
    plan = write_plan(env)
    doc = json.loads(plan.dumps())
    del doc["items"][0]["ops"][2]["src"]
    assert env.execute(json.dumps(doc)).code == 3


def test_excluded_destination_refused(env):
    env.write_config('[exclude]\npatterns = ["^movies:A"]\n')
    r = env.execute(write_plan(env))
    assert r.code == 3 and "excluded" in r.err


def test_two_writes_to_one_destination_or_from_one_source_refused(env):
    plan = write_plan(env)
    op = dict(plan.items[0].ops[2])
    plan.items[0].ops.insert(3, op)
    r = env.execute(plan)
    assert r.code == 3 and ("more than one" in r.err)


def test_symlinked_destination_folder_refused(env):
    plan = write_plan(env)
    (env.movies / "A (2000)").symlink_to(env.nas)
    r = env.execute(plan)
    assert r.code == 3
    assert not (env.nas / "Subs").exists()


def fail_first_rename(monkeypatch):
    """The move of the original into the trash fails (the write before it has happened); later renames work."""
    real, calls = os.rename, {"n": 0}

    def rename(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "Permission denied")
        return real(*a, **kw)

    monkeypatch.setattr(ta.os, "rename", rename)


def test_failed_item_rolls_the_write_back_into_the_trash(env, monkeypatch):
    plan = write_plan(env)
    fail_first_rename(monkeypatch)
    r = env.execute(plan)
    assert r.code == 1 and "rolled back" in r.err
    assert (env.source / "Film" / "en.srt").read_bytes() == ORIGINAL
    assert not dest(env).exists()  # never deleted: moved to the trash
    assert (env.source / ".tidyshelf-trash" / "movies" / "A (2000)" / "Subs" / "A (2000).en.srt").read_bytes() == CLEANED
    assert [e["t"] for e in env.journal() if e["t"] in ("rollback", "item")][-2:] == ["rollback", "item"]


def test_a_modified_written_file_is_not_touched_by_rollback(env, monkeypatch):
    plan = write_plan(env)
    fail_first_rename(monkeypatch)
    real = ta.Executor.rollback

    def edit_then_rollback(self, ctx, view):
        dest(env).write_bytes(b"edited by hand")
        return real(self, ctx, view)

    monkeypatch.setattr(ta.Executor, "rollback", edit_then_rollback)
    r = env.execute(plan)
    assert "not rolled back" in r.err and "changed since it was written" in r.err
    assert dest(env).read_bytes() == b"edited by hand"


# --- undo -----------------------------------------------------------------------------------------

def run_undo(env, *extra):
    return env.run(None, str(env.plan_path), "--undo", *extra)


def test_undo_moves_the_written_file_to_the_trash_and_restores_the_original(env):
    plan = write_plan(env)
    assert env.execute(plan).code == 0
    r = run_undo(env)
    assert r.code == 2 and "trash" in r.out and "data_b64" not in r.out
    assert dest(env).exists()  # dry-run
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 0, r.err
    assert (env.source / "Film" / "en.srt").read_bytes() == ORIGINAL
    assert not dest(env).exists()  # never deleted from the library: it is in the trash
    assert (env.source / ".tidyshelf-trash" / "movies" / "A (2000)" / "Subs" / "A (2000).en.srt").read_bytes() == CLEANED
    assert not (env.movies / "A (2000)").exists() or not any((env.movies / "A (2000)").iterdir())


def test_undo_twice_does_nothing_more(env):
    assert env.execute(write_plan(env)).code == 0
    assert run_undo(env, "--execute", "--yes").code == 0
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 0, r.err


def test_undo_with_a_modified_file_refused_and_nothing_moved(env):
    assert env.execute(write_plan(env)).code == 0
    dest(env).write_bytes(CLEANED + b"my own edit\n")
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 3 and "changed since it was written" in r.err and "nothing was undone" in r.err
    assert dest(env).read_bytes().endswith(b"my own edit\n")
    assert not (env.source / "Film" / "en.srt").exists()  # the original stays in the trash, nothing half-undone


def test_undo_trash_name_taken_gets_a_suffix(env):
    assert env.execute(write_plan(env)).code == 0
    taken = env.source / ".tidyshelf-trash" / "movies" / "A (2000)" / "Subs"
    taken.mkdir(parents=True)
    (taken / "A (2000).en.srt").write_bytes(b"older")
    assert run_undo(env, "--execute", "--yes").code == 0
    assert (taken / "A (2000).en.srt").read_bytes() == b"older"
    assert (taken / "A (2000).en.1.srt").read_bytes() == CLEANED


def test_a_planted_journal_write_line_is_refused(env):
    assert env.execute(write_plan(env)).code == 0
    lines = [json.loads(x) for x in env.journal_path.read_text().splitlines()]
    for e in lines:
        if e.get("op", {}).get("op") == "write":
            e["op"]["to"] = "movies:Other/x.srt"
    env.journal_path.write_text("".join(json.dumps(e) + "\n" for e in lines))
    r = run_undo(env, "--execute", "--yes")
    assert r.code == 3 and "not in the plan" in r.err
    assert dest(env).exists()


@pytest.mark.parametrize("to", ["movies:A (2000)/Subs/run.sh", "movies:A (2000)/Subs/A (2000).mkv"])
def test_write_only_makes_srt_files(env, to):
    r = env.execute(write_plan(env, to=to))
    assert r.code == 3 and "only makes .srt" in r.err
    assert not (env.movies / "A (2000)" / "Subs" / "run.sh").exists()


def test_write_data_must_be_utf8_text(env):
    r = env.execute(write_plan(env, data=b"\xff\xfe\x00binary"))
    assert r.code == 3 and "not UTF-8" in r.err
    assert not dest(env).exists()
