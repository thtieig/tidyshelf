"""tidyshelf run (plan + apply in one go, same executor) and tidyshelf undo."""

import os
from pathlib import Path

from helpers.tree import list_tree
from tidyshelf import cli

FOLDER = "Winter.Lights.2004.1080p"


def run(lib, capsys, *argv):
    capsys.readouterr()
    code = cli.main(list(argv) + ["--config", str(lib.config_path)])
    out, err = capsys.readouterr()
    return code, out, err


def film(lib):
    lib.tree("source", f"{FOLDER}/Winter.Lights.2004.1080p.mkv [100]\n{FOLDER}/readme.txt [5]")


def state_plans():
    return Path(os.environ["XDG_STATE_HOME"]) / "tidyshelf" / "plans"


def test_run_is_a_dry_run_by_default(lib, capsys):
    film(lib)
    before = list_tree(lib.nas)
    code, out, err = run(lib, capsys, "run")
    assert code == 2, err
    assert list_tree(lib.nas) == before
    (plan_file,) = state_plans().glob("plan-*.json")
    assert plan_file.name[5:17] in out and "dry-run" in out


def test_run_execute_applies_with_the_executor_and_its_journal(lib, capsys):
    film(lib)
    code, _out, err = run(lib, capsys, "run", "--execute", "--yes")
    assert code == 0, err
    assert (lib.roots["movies"] / "Winter Lights (2004)" / "Winter Lights (2004).mkv").exists()
    assert list(state_plans().glob("plan-*.journal.jsonl"))


def test_run_execute_without_yes_and_no_terminal_is_refused(lib, capsys, monkeypatch):
    import tidyshelf_apply
    monkeypatch.setattr(tidyshelf_apply, "_isatty", lambda: False)
    film(lib)
    code, _out, err = run(lib, capsys, "run", "--execute")
    assert code == 3 and (lib.roots["source"] / FOLDER).exists()


def test_undo_reverses_a_run(lib, capsys):
    film(lib)
    assert run(lib, capsys, "run", "--execute", "--yes")[0] == 0
    (plan_file,) = state_plans().glob("plan-*.json")
    code, _out, err = run(lib, capsys, "undo", str(plan_file))
    assert code == 2, err  # dry-run first
    code, _out, err = run(lib, capsys, "undo", str(plan_file), "--execute", "--yes")
    assert code == 0, err
    assert (lib.roots["source"] / FOLDER / "Winter.Lights.2004.1080p.mkv").exists()


def test_run_out_inside_a_root_refused(lib, capsys):
    film(lib)
    code, _out, err = run(lib, capsys, "run", "--out", str(lib.roots["source"] / "p.json"))
    assert code == 1 and "inside" in err


def test_run_with_an_item_needing_review_exits_4(lib, capsys):
    film(lib)
    lib.tree("source", "No.Year.Film/x.mkv")
    code, _out, _err = run(lib, capsys, "run", "--execute", "--yes")
    assert code == 4
    assert (lib.roots["movies"] / "Winter Lights (2004)").exists()


def test_run_nothing_to_do(lib, capsys):
    assert run(lib, capsys, "run")[0] == 0


def test_run_out_dash_is_refused(lib, capsys):
    film(lib)
    code, _out, err = run(lib, capsys, "run", "--out", "-")
    assert code == 1 and "file" in err and not Path("-").exists()
