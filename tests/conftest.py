import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Tests never read the real home, the real /etc or TIDYSHELF_* from the caller's shell."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for key in list(os.environ):
        if key.startswith("TIDYSHELF_"):
            monkeypatch.delenv(key)
    try:
        from tidyshelf import config
    except ImportError:  # module not written yet; its own tests will fail loudly
        config = None
    if config is not None:
        monkeypatch.setattr(config, "SYSTEM_CONFIG", tmp_path / "etc" / "tidyshelf.toml")
    import tidyshelf_apply

    monkeypatch.setattr(tidyshelf_apply, "SYSTEM_CONFIG", tmp_path / "etc" / "tidyshelf.toml")
    yield
