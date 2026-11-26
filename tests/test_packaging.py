import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_wheel_ships_defaults_and_apply_module(tmp_path):
    src = tmp_path / "src-copy"
    src.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy(REPO / name, src / name)
    shutil.copytree(REPO / "src", src / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    out = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path / "dist"), str(src)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
    (wheel,) = (tmp_path / "dist").glob("tidyshelf-*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    assert "tidyshelf/data/defaults.toml" in names
    assert "tidyshelf_apply.py" in names
    entry_points = next(n for n in names if n.endswith("entry_points.txt"))
    text = zipfile.ZipFile(wheel).read(entry_points).decode()
    assert "tidyshelf = tidyshelf.cli:main" in text
    assert "tidyshelf-apply = tidyshelf_apply:main" in text
