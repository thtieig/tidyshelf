"""Build fixture trees from a short text listing.

One entry per line, relative to the root:

    Some.Folder/                 a directory
    Some.Folder/file.mkv         an empty file
    Some.Folder/file.mkv [1500]  a file of 1500 bytes
    link -> Some.Folder          a symlink (target written as given)

Blank lines and lines starting with ``#`` are ignored. Parent directories are created as needed.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_SIZED = re.compile(r"^(?P<path>.+?)\s+\[(?P<size>\d+)\]$")


def make_tree(root: Path, listing: str) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for raw in listing.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " -> " in line:
            name, target = (part.strip() for part in line.split(" -> ", 1))
            link = root / name
            link.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, link)
            continue
        if line.endswith("/"):
            (root / line).mkdir(parents=True, exist_ok=True)
            continue
        size = 0
        match = _SIZED.match(line)
        if match:
            line, size = match["path"], int(match["size"])
        path = root / line
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    return root


def list_tree(root: Path) -> list[str]:
    """Sorted listing of everything under root, dirs with a trailing slash, for comparisons."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        for d in dirnames:
            out.append(f"{(rel / d).as_posix()}/")
        for f in filenames:
            out.append((rel / f).as_posix())
    return sorted(out)
