"""tidyshelf build-apply: a single-file tidyshelf-apply.pyz that runs anywhere with Python 3.11+."""

import json
import subprocess
import sys

import tidyshelf_apply
from tidyshelf import cli


def build(tmp_path, capsys, name="tidyshelf-apply.pyz"):
    out = tmp_path / name
    assert cli.main(["build-apply", "--out", str(out)]) == 0
    capsys.readouterr()
    return out


def test_pyz_runs_from_an_empty_folder(tmp_path, capsys):
    pyz = build(tmp_path, capsys)
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run([sys.executable, "-I", str(pyz), "--version"], cwd=empty, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0 and r.stdout.strip() == f"tidyshelf-apply {tidyshelf_apply.VERSION}"


def test_pyz_is_executable_and_reproducible(tmp_path, capsys):
    a = build(tmp_path, capsys, "a.pyz")
    b = build(tmp_path, capsys, "b.pyz")
    assert a.read_bytes() == b.read_bytes()
    assert a.read_bytes().startswith(b"#!/usr/bin/env python3\n") and a.stat().st_mode & 0o111


def test_pyz_dry_runs_a_plan(tmp_path, capsys):
    pyz = build(tmp_path, capsys)
    roots = {n: tmp_path / "nas" / n for n in ("source", "movies", "series")}
    for p in roots.values():
        p.mkdir(parents=True)
    (roots["source"] / "a.mkv").write_bytes(b"v")
    conf = tmp_path / "apply.toml"
    conf.write_text("[roots]\n" + "".join(f'{k} = "{v}"\n' for k, v in roots.items()), encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"format": 1, "items": [{"id": "i1", "kind": "movie", "status": "ready", "ops": [
        {"op": "move", "from": "source:a.mkv", "to": "movies:a.mkv", "size": 1,
         "mtime": (roots["source"] / "a.mkv").stat().st_mtime_ns // 10**9}]}]}), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", str(pyz), str(plan), "--config", str(conf)], capture_output=True,
                       text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 2, r.stderr
    assert (roots["source"] / "a.mkv").exists()
