"""Apply-side config: only [roots] [safety] [owner] [exclude], strict, own defaults."""

import pytest

import tidyshelf_apply as ta
from tidyshelf import config


def load(env, text):
    env.config_path.write_text(text, encoding="utf-8")
    return ta.load_apply_config(env.config_path)


def roots(env):
    return f'[roots]\nsource = "{env.source}"\nmovies = "{env.movies}"\nseries = "{env.series}"\n'


def test_builtin_defaults_equal_defaults_toml():
    defaults = config.load_defaults()
    for section in ta.APPLY_SECTIONS:
        assert ta.APPLY_DEFAULTS[section] == defaults[section], section
    assert set(ta.APPLY_DEFAULTS) == set(ta.APPLY_SECTIONS)


def test_other_sections_are_ignored(env):
    cfg = load(env, roots(env) + "[naming]\nwhatever = 1\n[subtitles]\nlanguages = 3\n")
    assert cfg["safety"]["max_delete_files"] == 200


@pytest.mark.parametrize("extra, needle", [
    ("[safety]\nmax_delete_flies = 3\n", "safety.max_delete_flies: unknown key"),
    ("[owner]\nusr = 'x'\n", "owner.usr: unknown key"),
    ("[safety]\nmax_delete_files = '3'\n", "safety.max_delete_files: expected integer"),
    ("[safety]\nrequire_approve = 'yes'\n", "safety.require_approve: expected boolean"),
    ("[safety]\nmax_delete_files = true\n", "safety.max_delete_files: expected integer"),
    ("[safety]\nmax_delete_files = -1\n", "must not be negative"),
    ("[safety]\nmax_age = '1 day'\n", "safety.max_age"),
    ("[exclude]\npatterns = ['(']\n", "exclude.patterns[0]: bad regex"),
    ("[exclude]\npatterns = 'x'\n", "exclude.patterns: expected list"),
    ("[owner]\ndir_mode = '999'\n", "owner.dir_mode"),
    ("[owner]\nuser = 'no-such-user-tidyshelf'\n", "owner.user: unknown user"),
    ("[owner]\ngroup = 'no-such-group-tidyshelf'\n", "owner.group: unknown group"),
])
def test_bad_values(env, extra, needle):
    with pytest.raises(ta.ConfigError) as exc:
        load(env, roots(env) + extra)
    assert needle in str(exc.value)


@pytest.mark.parametrize("value, needle", [("relative/x", "absolute"), ("/", "must not be /"), ("", "required")])
def test_bad_roots(env, value, needle):
    text = roots(env).replace(f'source = "{env.source}"', f'source = "{value}"')
    with pytest.raises(ta.ConfigError) as exc:
        load(env, text)
    assert needle in str(exc.value)


def test_unknown_root_key(env):
    with pytest.raises(ta.ConfigError, match="roots.moviez: unknown key"):
        load(env, roots(env) + 'moviez = "/x"\n')


def test_missing_root_dir_is_an_error(env):
    env.movies.rmdir()
    with pytest.raises(ta.ConfigError, match="does not exist"):
        ta.load_apply_config(env.config_path)


def test_nested_roots(env):
    text = roots(env).replace(f'movies = "{env.movies}"', f'movies = "{env.source}/Movies"')
    (env.source / "Movies").mkdir()
    with pytest.raises(ta.ConfigError, match="overlaps"):
        load(env, text)


def test_bad_config_exits_1(env):
    env.write_config("[safety]\nnope = 1\n")
    r = env.run({"format": 1, "items": []})
    assert r.code == 1
    assert "safety.nope" in r.err


def test_layers_and_env_var(env, monkeypatch, tmp_path):
    ta.SYSTEM_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    ta.SYSTEM_CONFIG.write_text(roots(env) + "[safety]\nmax_delete_files = 5\n", encoding="utf-8")
    user = tmp_path / "home" / ".config" / "tidyshelf" / "config.toml"
    user.parent.mkdir(parents=True)
    user.write_text("[safety]\nmax_delete_files = 6\n", encoding="utf-8")
    assert ta.load_apply_config(None)["safety"]["max_delete_files"] == 6
    other = tmp_path / "other.toml"
    other.write_text("[safety]\nmax_delete_files = 7\n", encoding="utf-8")
    monkeypatch.setenv("TIDYSHELF_CONFIG", str(other))
    assert ta.load_apply_config(None)["safety"]["max_delete_files"] == 7


@pytest.mark.parametrize("extra, needle", [
    ('[safety]\nmax_age = "1h\\n"\n', "safety.max_age"),
    ('[owner]\nfile_mode = "0644\\n"\n', "owner.file_mode"),
])
def test_trailing_newline_in_max_age_or_mode_refused(env, extra, needle):
    env.write_config(extra)
    r = env.run({"format": 1, "items": []})
    assert r.code == 1
    assert needle in r.err
