import pytest

from tidyshelf import config

ROOTS_TOML = """
[roots]
source = "/mnt/nas/to-sort"
movies = "/mnt/nas/Videos/Movies"
series = "/mnt/nas/Videos/Series"
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_deep_merge_tables_merge_and_lists_replace():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 1}
    over = {"a": {"y": [3]}, "c": 2}
    assert config.deep_merge(base, over) == {"a": {"x": 1, "y": [3]}, "b": 1, "c": 2}
    assert base == {"a": {"x": 1, "y": [1, 2]}, "b": 1}, "merge must not mutate inputs"


def test_defaults_only_when_no_files(tmp_path):
    cfg, sources = config.load()
    assert cfg == config.load_defaults()
    assert sources == ["defaults"]


def test_layers_in_order_last_wins(tmp_path, monkeypatch):
    write(config.SYSTEM_CONFIG, ROOTS_TOML + '\n[subtitles]\nlanguages = ["en"]\ndefault_language = "en"\n')
    user = write(config.user_config_path(), '[subtitles]\nlanguages = ["it", "en"]\n')
    explicit = write(tmp_path / "x.toml", '[subtitles]\ndefault_language = "it"\n')
    cfg, sources = config.load(explicit)
    assert cfg["roots"]["source"] == "/mnt/nas/to-sort"
    assert cfg["subtitles"]["languages"] == ["it", "en"]
    assert cfg["subtitles"]["default_language"] == "it"
    assert cfg["subtitles"]["prefer"] == "plain"  # untouched default survives
    assert sources == ["defaults", str(config.SYSTEM_CONFIG), str(user), str(explicit)]


def test_user_config_honours_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config.user_config_path() == tmp_path / "xdg" / "tidyshelf" / "config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert config.user_config_path().parts[-3:] == (".config", "tidyshelf", "config.toml")


def test_env_var_names_the_explicit_file(tmp_path, monkeypatch):
    env_file = write(tmp_path / "env.toml", '[conflicts]\non_exists = "skip"\n')
    monkeypatch.setenv("TIDYSHELF_CONFIG", str(env_file))
    cfg, sources = config.load()
    assert cfg["conflicts"]["on_exists"] == "skip"
    assert sources[-1] == str(env_file)


def test_explicit_path_beats_env_var(tmp_path, monkeypatch):
    env_file = write(tmp_path / "env.toml", '[conflicts]\non_exists = "skip"\n')
    cli_file = write(tmp_path / "cli.toml", '[conflicts]\non_exists = "report"\n')
    monkeypatch.setenv("TIDYSHELF_CONFIG", str(env_file))
    cfg, sources = config.load(cli_file)
    assert cfg["conflicts"]["on_exists"] == "report"
    assert str(env_file) not in sources


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(config.ConfigError) as exc:
        config.load(tmp_path / "nope.toml")
    assert "nope.toml" in str(exc.value)


def test_toml_syntax_error_names_the_file(tmp_path):
    bad = write(tmp_path / "bad.toml", "[roots\n")
    with pytest.raises(config.ConfigError) as exc:
        config.load(bad)
    assert "bad.toml" in str(exc.value)


def test_override_array_of_tables(tmp_path):
    f = write(tmp_path / "c.toml", ROOTS_TOML + """
[[override]]
match = "Some.Folder*"
title = "Some Film"
year = 1999

[[override]]
match = "^Other/"
ignore = true
""")
    cfg, _ = config.load(f)
    assert cfg["override"] == [
        {"match": "Some.Folder*", "title": "Some Film", "year": 1999},
        {"match": "^Other/", "ignore": True},
    ]
    assert config.validate(cfg).errors == []


def test_title_rules_array_of_tables(tmp_path):
    f = write(tmp_path / "c.toml", ROOTS_TOML + """
[[parse.title_rules]]
match = "_"
replace = " "
""")
    cfg, _ = config.load(f)
    assert cfg["parse"]["title_rules"] == [{"match": "_", "replace": " "}]
    assert cfg["parse"]["junk_tokens"], "other parse defaults survive"
    assert config.validate(cfg).errors == []


def test_required_roots(tmp_path):
    f = write(tmp_path / "c.toml", '[roots]\nsource = "/mnt/nas/to-sort"\n')
    cfg, _ = config.load(f)
    errors = config.validate(cfg).errors
    assert any("roots.movies" in e for e in errors)
    assert any("roots.series" in e for e in errors)
    assert not any("roots.source" in e for e in errors)
