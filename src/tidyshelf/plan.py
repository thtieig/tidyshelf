"""Producer-side plan model: the only place tidyshelf builds plan documents.

Validation and the plan id live in ``tidyshelf_apply`` (the executor owns the format); this module only
assembles documents in the shape it expects.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os

import tidyshelf_apply
from tidyshelf import __version__

TRASH_DIR = ".tidyshelf-trash"


def file_stat(path) -> tuple[int, int]:
    """(size, mtime) as recorded in plans: whole seconds, the same on both sides."""
    st = os.lstat(path)
    return st.st_size, st.st_mtime_ns // 10**9


def trash_ref(ref: str) -> str:
    """Where junk from ``ref`` goes: source:.tidyshelf-trash/<root>/<original relative path>."""
    root, rel = ref.split(":", 1)
    return f"source:{TRASH_DIR}/{root}/{rel}"


class Item:
    def __init__(self, item_id: str, kind: str, status: str = "ready", source: str | None = None):
        self.id, self.kind, self.status, self.source = item_id, kind, status, source
        self.findings: list[dict] = []
        self.ops: list[dict] = []

    def finding(self, level: str, code: str, msg: str) -> "Item":
        self.findings.append({"level": level, "code": code, "msg": msg})
        return self

    def mkdir(self, path: str) -> "Item":
        self.ops.append({"op": "mkdir", "path": path})
        return self

    def move(self, src: str, dst: str, size: int, mtime: int) -> "Item":
        self.ops.append({"op": "move", "from": src, "to": dst, "size": size, "mtime": mtime})
        return self

    def write(self, to: str, data: bytes, src: str, src_sha256: str, size: int, mtime: int) -> "Item":
        """A new file with exactly these bytes (a cleaned copy of `src`, whose size, mtime and hash it checks)."""
        self.ops.append({"op": "write", "to": to, "data_b64": base64.b64encode(data).decode("ascii"),
                         "sha256": hashlib.sha256(data).hexdigest(), "src": src, "src_sha256": src_sha256,
                         "size": size, "mtime": mtime})
        return self

    def delete_file(self, path: str, size: int, mtime: int, reason: str) -> "Item":
        self.ops.append({"op": "delete", "path": path, "size": size, "mtime": mtime, "reason": reason})
        return self

    def delete_dir(self, path: str, reason: str) -> "Item":
        self.ops.append({"op": "delete", "path": path, "dir": True, "reason": reason})
        return self

    def set_mode(self, path: str) -> "Item":
        self.ops.append({"op": "set_mode", "path": path})
        return self

    def to_dict(self) -> dict:
        out = {"id": self.id, "kind": self.kind, "status": self.status}
        if self.source is not None:
            out["source"] = self.source
        out["findings"] = list(self.findings)
        out["ops"] = list(self.ops)
        return out


class Plan:
    def __init__(self, created: str | None = None):
        self.created = created or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.items: list[Item] = []
        self.warnings: list[str] = []  # for the person running the planner; not part of the plan file

    def item(self, item_id: str, kind: str, status: str = "ready", source: str | None = None) -> Item:
        it = Item(item_id, kind, status, source)
        self.items.append(it)
        return it

    def to_dict(self) -> dict:
        return {"format": tidyshelf_apply.FORMAT, "tool": f"tidyshelf {__version__}",
                "created": self.created, "items": [it.to_dict() for it in self.items]}

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=True) + "\n"

    def validate(self) -> None:
        """The executor's own check; a planner bug shows up here, not on the NAS."""
        tidyshelf_apply.validate_plan(self.to_dict())

    @property
    def plan_id(self) -> str:
        return tidyshelf_apply.plan_id(self.to_dict())
