"""tidyshelf_apply.py must stay one small, auditable, stdlib-only file."""

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import tidyshelf_apply as ta

APPLY = Path(ta.__file__)
ALLOWED_IMPORTS = {
    "__future__", "argparse", "base64", "binascii", "datetime", "errno", "fcntl", "grp", "hashlib", "json", "os",
    "pwd", "re", "stat", "sys", "tomllib", "difflib",
}
BANNED_NAMES = {"eval", "exec", "compile", "__import__"}
BANNED_ATTRS = {"system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp"}
BANNED_ATTR_PREFIXES = ("spawn", "exec")  # os.spawn*, os.exec*
# os functions that overwrite or remove whole trees; banned only on os (str.replace is fine)
BANNED_OS_ATTRS = {"replace", "removedirs", "renames", "rmtree", "truncate", "ftruncate"}


def tree():
    return ast.parse(APPLY.read_text(encoding="utf-8"))


def violations(source):
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in BANNED_NAMES:
            found.append(f"line {node.lineno}: {fn.id}")
        if isinstance(fn, ast.Attribute):
            on_os = isinstance(fn.value, ast.Name) and fn.value.id == "os"
            if fn.attr in BANNED_ATTRS:
                found.append(f"line {node.lineno}: {fn.attr}")
            if on_os and (fn.attr in BANNED_OS_ATTRS or fn.attr.startswith(BANNED_ATTR_PREFIXES)):
                found.append(f"line {node.lineno}: os.{fn.attr}")
        found += [f"line {node.lineno}: shell=" for kw in node.keywords if kw.arg == "shell"]
    return found


@pytest.mark.parametrize("source", [
    "os.replace(a, b)", "os.removedirs(a)", "os.execvpe(a, b, c)", "os.spawnvp(1, a, b)",
    "os.posix_spawn(a, b, c)", "eval(x)", "f(shell=True)", "os.system(x)",
])
def test_scanner_catches(source):
    assert violations(source)


def test_scanner_allows_str_replace():
    assert violations("s.replace('a', 'b')") == []


def test_imports_only_allowlisted_stdlib():
    found = set()
    for node in ast.walk(tree()):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add((node.module or "").split(".")[0])
    assert found <= ALLOWED_IMPORTS, found - ALLOWED_IMPORTS


def test_no_dangerous_calls():
    assert violations(APPLY.read_text(encoding="utf-8")) == []


def test_runs_isolated_from_a_temp_dir(env, tmp_path):
    solo = tmp_path / "solo"
    solo.mkdir()
    shutil.copy(APPLY, solo / "tidyshelf_apply.py")
    env.tree("source", "a.mkv")
    plan = {"format": 1, "items": [{"id": "i1", "kind": "movie", "status": "ready", "ops": [
        {"op": "move", "from": "source:a.mkv", "to": "movies:a.mkv",
         "size": 0, "mtime": env.stat("source:a.mkv")[1]}]}]}
    env.write_plan(json.dumps(plan))
    out = subprocess.run(
        [sys.executable, "-I", "tidyshelf_apply.py", str(env.plan_path), "--config", str(env.config_path),
         "--execute", "--yes"],
        cwd=solo, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")},
    )
    assert out.returncode == 0, out.stdout + out.stderr
    assert (env.movies / "a.mkv").exists()
    help_out = subprocess.run([sys.executable, "-I", "tidyshelf_apply.py", "--help"], cwd=solo,
                              capture_output=True, text=True)
    assert help_out.returncode == 0 and "--execute" in help_out.stdout


def test_file_stays_readable_in_one_sitting():
    lines = APPLY.read_text(encoding="utf-8").count("\n")
    assert lines < 1600, lines
