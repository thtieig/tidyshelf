"""Fixtures for planner tests."""

from __future__ import annotations

import pytest

from tidyshelf import config


@pytest.fixture
def cfg(tmp_path):
    """Built-in defaults with three existing roots under tmp_path/nas."""
    roots = {name: tmp_path / "nas" / name for name in ("source", "movies", "series")}
    for path in roots.values():
        path.mkdir(parents=True)
    return config.deep_merge(config.load_defaults(), {"roots": {k: str(v) for k, v in roots.items()}})


class Lib:
    """A fake NAS with the three roots, a config file pointing at them, and fixture writers."""

    def __init__(self, tmp_path):
        from helpers.tree import make_tree, write_file

        self._make_tree, self._write_file = make_tree, write_file
        self.nas = tmp_path / "nas"
        self.roots = {"source": self.nas / "to-sort", "movies": self.nas / "Movies", "series": self.nas / "Series"}
        for path in self.roots.values():
            path.mkdir(parents=True)
        self.config_path = tmp_path / "tidyshelf.toml"
        self.write_config()

    def write_config(self, extra: str = "") -> None:
        roots = "".join(f'{name} = "{path}"\n' for name, path in self.roots.items())
        self.config_path.write_text("[roots]\n" + roots + extra, encoding="utf-8")

    @property
    def cfg(self) -> dict:
        return config.load(self.config_path)[0]

    def tree(self, root: str, listing: str):
        return self._make_tree(self.roots[root], listing)

    def write(self, root: str, rel: str, data: bytes):
        return self._write_file(self.roots[root], rel, data)


@pytest.fixture
def lib(tmp_path):
    return Lib(tmp_path)


@pytest.fixture(autouse=True)
def _no_ffprobe(monkeypatch):
    """Planner tests never depend on whether ffprobe is installed: no probe unless a test asks."""
    from tidyshelf import quality

    monkeypatch.setattr(quality, "run_ffprobe", lambda path, cfg: None)


@pytest.fixture
def probes(monkeypatch):
    """probes({"file name": "fixture name"}): recorded ffprobe output per video file name."""
    import json
    import os
    from pathlib import Path

    from tidyshelf import quality

    folder = Path(__file__).resolve().parent.parent / "fixtures" / "ffprobe"

    def install(mapping):
        def fake(path, cfg):
            name = mapping.get(os.path.basename(path))
            return None if name is None else json.loads((folder / f"{name}.json").read_text())
        monkeypatch.setattr(quality, "run_ffprobe", fake)

    return install
