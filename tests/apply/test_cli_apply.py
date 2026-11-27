"""CLI behaviour: dry-run default, confirmation, stdin plans, exit codes, output encoding."""

import io
import json
import os
import sys

import pytest

import tidyshelf_apply as ta
from helpers.plans import movie_plan
from tidyshelf.plan import Plan


def test_dry_run_leaves_tree_identical_no_lock_no_journal(env):
    plan = movie_plan(env)
    before = env.snapshot()
    r = env.run(plan)
    assert r.code == 2
    assert plan.plan_id in r.out
    assert env.snapshot() == before
    assert not env.journal_path.exists()
    assert not (env.source / ".tidyshelf.lock").exists()


def test_empty_plan_exit_0(env):
    assert env.run({"format": 1, "items": []}).code == 0


def test_execute_without_tty_and_without_yes_refused(env, monkeypatch):
    monkeypatch.setattr(ta, "_isatty", lambda: False)
    plan = movie_plan(env)
    before = env.snapshot()
    r = env.run(plan, "--execute")
    assert r.code == 3
    assert "--yes" in r.err
    assert env.snapshot() == before


def test_interactive_confirm_with_plan_id(env, monkeypatch):
    plan = movie_plan(env)
    monkeypatch.setattr(ta, "_isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": plan.plan_id)
    r = env.run(plan, "--execute")
    assert r.code == 0, r.err
    assert (env.movies / "Some Film (1999)" / "Some Film (1999).mkv").exists()


def test_wrong_typed_id_refused(env, monkeypatch):
    plan = movie_plan(env)
    before = env.snapshot()
    monkeypatch.setattr(ta, "_isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "nope")
    r = env.run(plan, "--execute")
    assert r.code == 3
    assert env.snapshot() == before


def _stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8"))


def test_stdin_plan_needs_yes(env, monkeypatch):
    plan = movie_plan(env)
    monkeypatch.setattr(ta, "_isatty", lambda: True)
    _stdin(monkeypatch, plan.dumps())
    r = env.run(None, "-", "--execute")
    assert r.code == 3
    assert "--yes" in r.err


def test_stdin_plan_journal_in_cwd(env, monkeypatch, tmp_path):
    plan = movie_plan(env)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    _stdin(monkeypatch, plan.dumps())
    r = env.run(None, "-", "--execute", "--yes", "--approve", plan.plan_id)
    assert r.code == 0, r.err
    assert (work / f"tidyshelf-{plan.plan_id}.journal.jsonl").exists()


def test_json_output(env):
    plan = movie_plan(env)
    r = env.run(plan, "--json")
    assert r.code == 2
    doc = r.json()
    assert doc["plan_id"] == plan.plan_id
    assert doc["mode"] == "dry-run"
    assert doc["items"][0]["id"] == "movie-0001"
    assert doc["exit"] == 2


def test_quiet_dry_run_prints_nothing(env):
    r = env.run(movie_plan(env), "--quiet")
    assert r.code == 2
    assert r.out == ""


@pytest.mark.parametrize("statuses, broken, expected", [
    (["ready"], False, 2),
    (["ready", "needs_review"], False, 4),
    (["ready", "needs_review"], True, 3),
    (["skipped"], False, 0),
    (["needs_review"], False, 4),
])
def test_exit_code_precedence(env, statuses, broken, expected):
    env.tree("source", "a.mkv")
    plan = Plan()
    for i, status in enumerate(statuses):
        item = plan.item(f"i{i}", "movie", status=status)
        if status == "ready":
            size, mtime = env.stat("source:a.mkv")
            item.move("source:a.mkv", "movies:a.mkv", size + (1 if broken else 0), mtime)
    assert env.run(plan).code == expected


def test_unreadable_beats_everything(env):
    assert env.run("[").code == 1


def test_surrogate_names_round_trip_and_execute(env, monkeypatch):
    raw = b"caf\xe9.mkv"  # latin-1 byte, not valid UTF-8
    with open(os.path.join(os.fsencode(env.source), raw), "wb") as fh:
        fh.write(b"v")
    name = os.fsdecode(raw)
    plan = Plan()
    plan.item("i1", "movie").move(f"source:{name}", f"movies:{name}", *env.stat(f"source:{name}"))
    text = plan.dumps()
    assert text.isascii()
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", out)
    code = ta.main([str(env.write_plan(text)), "--config", str(env.config_path), "--execute", "--yes"])
    assert code == 0
    assert os.path.exists(os.path.join(os.fsencode(env.movies), raw))
    out.flush()
    assert b"\\udce9" in out.buffer.getvalue() or b"\\xe9" in out.buffer.getvalue()
    journal = env.journal_path.read_bytes()
    assert journal.isascii()


def test_dry_run_hides_info_findings_unless_verbose(env):
    plan = movie_plan(env)
    plan.items[0].finding("info", "x.y", "just so you know")
    plan.items[0].finding("warn", "x.z", "look at this")
    hidden = env.run(plan)
    assert "warn: x.z: look at this" in hidden.out
    assert "info: x.y" not in hidden.out and "(1 info finding hidden; --verbose to show)" in hidden.out
    assert "info: x.y: just so you know" in env.run(plan, "--verbose").out
    assert "info: x.y: just so you know" in env.run(plan, "-v").out
