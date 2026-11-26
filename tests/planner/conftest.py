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
