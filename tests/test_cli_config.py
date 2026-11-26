import json
import tomllib

from tidyshelf import cli, config


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_init_writes_only_roots(tmp_path, capsys):
    target = tmp_path / "c.toml"
    code, out, err = run(capsys, "init", "--out", str(target))
    assert code == 0, err
    data = tomllib.loads(target.read_text(encoding="utf-8"))
    assert list(data) == ["roots"]
    assert set(data["roots"]) == {"source", "movies", "series"}
    assert "#" in target.read_text(encoding="utf-8"), "init output is commented"


def test_init_default_location_is_user_config(capsys):
    code, out, err = run(capsys, "init")
    assert code == 0, err
    assert config.user_config_path().exists()


def test_init_refuses_overwrite_without_force(tmp_path, capsys):
    target = tmp_path / "c.toml"
    target.write_text("# mine\n", encoding="utf-8")
    code, out, err = run(capsys, "init", "--out", str(target))
    assert code == 1
    assert "exists" in err
    assert target.read_text(encoding="utf-8") == "# mine\n"
    code, out, err = run(capsys, "init", "--out", str(target), "--force")
    assert code == 0
    assert "[roots]" in target.read_text(encoding="utf-8")


def test_init_output_passes_check(tmp_path, capsys):
    target = tmp_path / "c.toml"
    assert run(capsys, "init", "--out", str(target))[0] == 0
    code, out, err = run(capsys, "config", "check", "--config", str(target))
    assert code == 0, out + err
    assert "does not exist" in err  # example paths: warnings, not errors


def test_check_reports_errors_with_exit_1(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('[roots]\nsource = "x"\n[naming]\nmovie_fodler = "y"\n', encoding="utf-8")
    code, out, err = run(capsys, "config", "check", "--config", str(bad))
    assert code == 1
    assert "naming.movie_fodler: unknown key" in err
    assert "roots.source: must be an absolute path" in err


def test_check_json(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('[nmaing]\n', encoding="utf-8")
    code, out, err = run(capsys, "--json", "config", "check", "--config", str(bad))
    assert code == 1
    doc = json.loads(out)
    assert doc["ok"] is False
    assert any("nmaing" in e for e in doc["errors"])
    assert isinstance(doc["warnings"], list)
    assert doc["sources"][0] == "defaults"


def test_check_toml_syntax_error_exit_1(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[roots\n", encoding="utf-8")
    code, out, err = run(capsys, "config", "check", "--config", str(bad))
    assert code == 1
    assert "bad.toml" in err


def test_show_prints_effective_config_as_json(tmp_path, capsys):
    target = tmp_path / "c.toml"
    run(capsys, "init", "--out", str(target))
    code, out, err = run(capsys, "config", "show", "--config", str(target))
    assert code == 0, err
    doc = json.loads(out)
    assert doc["roots"]["source"] == "/mnt/nas/to-sort"
    assert doc["subtitles"]["languages"] == ["en", "es", "it"]


def test_show_refuses_invalid_config(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('[nmaing]\n', encoding="utf-8")
    code, out, err = run(capsys, "config", "show", "--config", str(bad))
    assert code == 1
    assert out == ""


def test_config_flag_accepted_before_subcommand(tmp_path, capsys):
    target = tmp_path / "c.toml"
    run(capsys, "init", "--out", str(target))
    code, out, err = run(capsys, "--config", str(target), "config", "check")
    assert code == 0, err


def test_env_var_used_by_cli(tmp_path, capsys, monkeypatch):
    target = tmp_path / "c.toml"
    run(capsys, "init", "--out", str(target))
    monkeypatch.setenv("TIDYSHELF_CONFIG", str(target))
    code, out, err = run(capsys, "config", "check")
    assert code == 0, err


def test_quiet_suppresses_warnings(tmp_path, capsys):
    target = tmp_path / "c.toml"
    run(capsys, "init", "--out", str(target))
    code, out, err = run(capsys, "--quiet", "config", "check", "--config", str(target))
    assert code == 0
    assert out == "" and err == ""


def test_no_args_prints_usage_exit_1(capsys):
    code, out, err = run(capsys)
    assert code == 1
    assert "usage" in (out + err).lower()


def test_usage_error_is_exit_1_not_2(capsys):
    """Exit 2 means "plan has changes"; a typo on the command line must not look like that."""
    code, out, err = run(capsys, "config", "check", "--no-such-flag")
    assert code == 1
    assert "usage" in err.lower()
