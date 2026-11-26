"""set_mode: owner/modes only from config, chown only when privileged."""

import grp
import os
import pwd
import stat

import tidyshelf_apply as ta
from tidyshelf.plan import Plan


def me():
    return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name


def mode_plan(env):
    env.tree("movies", "A/a.mkv")
    plan = Plan()
    item = plan.item("i1", "movie")
    item.set_mode("movies:A")
    item.set_mode("movies:A/a.mkv")
    return plan


def test_modes_from_config(env):
    env.write_config('[owner]\ndir_mode = "0750"\nfile_mode = "0640"\n')
    r = env.execute(mode_plan(env))
    assert r.code == 0, r.err
    assert stat.S_IMODE(os.stat(env.movies / "A").st_mode) == 0o750
    assert stat.S_IMODE(os.stat(env.movies / "A" / "a.mkv").st_mode) == 0o640


def test_nothing_configured_changes_nothing(env):
    plan = mode_plan(env)
    os.chmod(env.movies / "A" / "a.mkv", 0o600)
    assert env.execute(plan).code == 0
    assert stat.S_IMODE(os.stat(env.movies / "A" / "a.mkv").st_mode) == 0o600


def test_dry_run_changes_no_mode(env):
    env.write_config('[owner]\nfile_mode = "0640"\n')
    plan = mode_plan(env)
    os.chmod(env.movies / "A" / "a.mkv", 0o600)
    assert env.run(plan).code == 2
    assert stat.S_IMODE(os.stat(env.movies / "A" / "a.mkv").st_mode) == 0o600


def test_chown_skipped_when_not_privileged(env, monkeypatch):
    env.write_config('[owner]\nuser = "root"\ngroup = "root"\n')
    calls = []
    monkeypatch.setattr(ta.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(ta.os, "chown", lambda *a, **kw: calls.append(a))
    r = env.execute(mode_plan(env))
    assert r.code == 0, r.err
    assert calls == []
    assert "not privileged" in r.err


def test_chown_with_configured_ids_when_privileged(env, monkeypatch):
    env.write_config('[owner]\nuser = "root"\ngroup = "root"\n')
    calls = []
    monkeypatch.setattr(ta.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ta.os, "chown", lambda path, uid, gid, **kw: calls.append((os.path.basename(path), uid, gid)))
    r = env.execute(mode_plan(env))
    assert r.code == 0, r.err
    assert calls == [("A", 0, 0), ("a.mkv", 0, 0)]


def test_matching_owner_and_mode_is_already_done(env):
    user, group = me()
    env.write_config(f'[owner]\nuser = "{user}"\ngroup = "{group}"\nfile_mode = "0640"\ndir_mode = "0750"\n')
    plan = mode_plan(env)
    os.chmod(env.movies / "A", 0o750)
    os.chmod(env.movies / "A" / "a.mkv", 0o640)
    assert env.run(plan).code == 0


def test_set_mode_on_symlink_refused(env):
    env.tree("movies", "a.mkv\nl.mkv -> a.mkv")
    plan = Plan()
    plan.item("i1", "movie").set_mode("movies:l.mkv")
    assert env.run(plan).code == 3
