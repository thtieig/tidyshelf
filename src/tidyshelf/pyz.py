"""Build tidyshelf-apply.pyz: the stdlib-only executor as one file, for the machine that holds the files."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import tidyshelf_apply

SHEBANG = b"#!/usr/bin/env python3\n"
_STAMP = (1980, 1, 1, 0, 0, 0)  # fixed, so the same source always gives the same file


def build(out: Path) -> Path:
    source = Path(tidyshelf_apply.__file__).read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("__main__.py", date_time=_STAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zf.writestr(info, source)
    out = Path(out)
    tmp = out.with_name(f".{out.name}.tmp")
    tmp.write_bytes(SHEBANG + buf.getvalue())
    os.chmod(tmp, 0o755)
    os.replace(tmp, out)
    return out
