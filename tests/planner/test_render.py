"""plan --emit text|sh. The shell script is run with bash on fixture trees."""

import os
import subprocess
from pathlib import Path

import pytest

from helpers.tree import list_tree
from tidyshelf import cli
from tidyshelf.movies import build_plan
from tidyshelf.render import to_sh, to_text

FOLDER = "Winter.Lights.2004.1080p"


def film(lib):
    lib.tree("source", f"{FOLDER}/Winter.Lights.2004.1080p.mkv [100]\n{FOLDER}/readme.txt [5]")


def script(lib, tmp_path, **extra_cfg):
    cfg = lib.cfg
    for section, values in extra_cfg.items():
        cfg[section].update(values)
    plan, _ = build_plan(cfg)
    path = tmp_path / "plan.sh"
    path.write_bytes(to_sh(plan.to_dict(), cfg))
    return path


def bash(path, *args, env=None):
    return subprocess.run(["bash", str(path), *args], capture_output=True, text=True, errors="replace",
                          env={**os.environ, **(env or {})})


def test_text_lists_items_and_ops(lib):
    film(lib)
    text = to_text(build_plan(lib.cfg)[0].to_dict())
    assert "movie-0001  ready" in text and "move     source:" in text and "-> movies:Winter Lights (2004)/" in text


def test_script_dry_run_changes_nothing(lib, tmp_path):
    film(lib)
    path = script(lib, tmp_path)
    before = list_tree(lib.nas)
    r = bash(path)
    assert r.returncode == 0, r.stderr
    assert "would:" in r.stdout and list_tree(lib.nas) == before


def test_script_executes_and_is_rerunnable(lib, tmp_path):
    film(lib)
    path = script(lib, tmp_path)
    r = bash(path, "--execute")
    assert r.returncode == 0, r.stderr
    assert (lib.roots["movies"] / "Winter Lights (2004)" / "Winter Lights (2004).mkv").exists()
    assert (lib.roots["source"] / ".tidyshelf-trash" / "source" / FOLDER / "readme.txt").exists()
    assert not (lib.roots["source"] / FOLDER).exists()
    r = bash(path, "--execute")  # again: everything is already done
    assert r.returncode == 0, r.stderr


def test_script_never_overwrites_and_checks_everything_first(lib, tmp_path):
    film(lib)
    path = script(lib, tmp_path)
    (lib.roots["source"] / ".tidyshelf-trash" / "source" / FOLDER).mkdir(parents=True)
    (lib.roots["source"] / ".tidyshelf-trash" / "source" / FOLDER / "readme.txt").write_bytes(b"other")
    before = list_tree(lib.nas)
    r = bash(path, "--execute")
    assert r.returncode == 3 and "exists" in r.stderr
    assert list_tree(lib.nas) == before  # the check pass stopped it before the first step


def test_script_refuses_a_changed_file(lib, tmp_path):
    film(lib)
    path = script(lib, tmp_path)
    (lib.roots["source"] / FOLDER / "Winter.Lights.2004.1080p.mkv").write_bytes(b"y" * 101)
    r = bash(path, "--execute")
    assert r.returncode == 3 and "changed" in r.stderr
    assert not (lib.roots["movies"] / "Winter Lights (2004)").exists()


def test_script_refuses_a_symlinked_parent(lib, tmp_path):
    film(lib)
    path = script(lib, tmp_path)
    real = lib.roots["source"] / FOLDER
    os.rename(real, tmp_path / "elsewhere")
    os.symlink(tmp_path / "elsewhere", real)
    r = bash(path, "--execute")
    assert r.returncode == 3 and "symlink" in r.stderr
    assert (tmp_path / "elsewhere" / "Winter.Lights.2004.1080p.mkv").exists()


def test_script_delete_cap(lib, tmp_path):
    lib.write_config('[junk]\njunk_action = "delete"\n[safety]\nmax_delete_files = 0\n')
    film(lib)
    path = script(lib, tmp_path)
    r = bash(path, "--execute")
    assert r.returncode == 3 and "delete cap" in r.stderr
    assert (lib.roots["source"] / FOLDER / "readme.txt").exists()
    assert bash(path, "--execute", env={"ALLOW_LARGE_DELETE": "1"}).returncode == 0
    assert not (lib.roots["source"] / FOLDER).exists()


def test_script_modes_only_from_its_own_variables(lib, tmp_path):
    film(lib)
    path = script(lib, tmp_path, owner={"file_mode": "0600", "dir_mode": "0700"})
    assert bash(path, "--execute").returncode == 0
    movie = lib.roots["movies"] / "Winter Lights (2004)"
    assert (movie.stat().st_mode & 0o777, (movie / "Winter Lights (2004).mkv").stat().st_mode & 0o777) == (0o700, 0o600)


def test_every_move_checks_the_filesystem(lib, tmp_path):
    film(lib)
    text = script(lib, tmp_path).read_text()
    assert text.count("do_move ") >= 2 and "same_fs" in text and "set -euo pipefail" in text


def test_names_that_are_not_utf8_survive(lib, tmp_path):
    src = os.fsencode(lib.roots["source"])
    os.makedirs(os.path.join(src, b"Caf\xe9.Noir.2003"))
    with open(os.path.join(src, b"Caf\xe9.Noir.2003", b"f.mkv"), "wb") as fh:
        fh.write(b"v")
    path = script(lib, tmp_path)
    assert bash(path, "--execute").returncode == 0
    assert os.listdir(os.fsencode(lib.roots["movies"])) == [b"Caf\xe9 Noir (2003)"]


def test_cli_emit(lib, capsys, tmp_path):
    film(lib)
    assert cli.main(["plan", "--emit", "sh", "--out", str(tmp_path / "p.sh"), "--config", str(lib.config_path)]) == 2
    assert (tmp_path / "p.sh").read_text().startswith("#!/usr/bin/env bash")
    capsys.readouterr()
    assert cli.main(["plan", "--emit", "text", "--config", str(lib.config_path)]) == 2
    out, err = capsys.readouterr()
    assert "move     source:" in out and "plan " in err


def test_a_newline_in_a_name_cannot_inject_a_command(lib, tmp_path):
    name = "Evil.Film.2001\ntouch injected\n"
    (lib.roots["source"] / name).mkdir()
    (lib.roots["source"] / name / "f.mkv").write_bytes(b"v")
    path = script(lib, tmp_path)
    for args in ((), ("--execute",)):
        r = subprocess.run(["bash", str(path), *args], cwd=tmp_path, capture_output=True, text=True, errors="replace")
        assert r.returncode == 0, r.stderr
    assert not (tmp_path / "injected").exists()


GOLDEN_SH = Path(__file__).resolve().parent.parent / "golden" / "winter-lights.sh"


def test_golden_script(lib):
    from test_golden import golden_tree
    golden_tree(lib)
    cfg = lib.cfg
    cfg["roots"] = {"source": "/srv/example/to-sort", "movies": "/srv/example/Movies", "series": "/srv/example/Series"}
    plan, _ = build_plan(lib.cfg, created="2026-11-26T20:15:00Z")
    text = to_sh(plan.to_dict(), cfg).decode("utf-8")
    golden = GOLDEN_SH.read_text(encoding="utf-8")
    strip = lambda t: "\n".join(line for line in t.splitlines() if not line.startswith("# tidyshelf plan"))  # noqa: E731
    assert strip(text) == strip(golden)
