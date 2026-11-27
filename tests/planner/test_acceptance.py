"""Acceptance: a library that follows the standard audits to an empty plan (PLAN §10).

The listing holds names only. Subtitle files get real text in the language their name claims,
so the content checks see what a real library would hold.
"""

import os
from pathlib import Path

import pytest

from tidyshelf.movies import build_plan

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TEXT = {"en": "en_plain.srt", "es": "es.srt", "it": "it.srt", "ro": "ro.srt"}


def build_from_listing(lib, listing: str) -> int:
    count = 0
    for raw in listing.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        root, rel = line.split("/", 1)
        tokens = rel.rsplit("/", 1)[-1].split(".")
        ext = "." + tokens[-1].lower()
        lang = next((t for t in reversed(tokens[:-1]) if t in TEXT), "en")
        if ext in (".srt", ".ass", ".ssa", ".vtt"):
            data = (FIXTURES / "subs" / TEXT[lang]).read_bytes()
        elif ext == ".idx":
            data = f"id: {lang}, index: 0\n".encode()
        else:
            data = b""
        lib.write({"movies": "movies", "series": "series"}[root], rel, data)
        count += 1
    return count


def test_synthetic_library_audits_to_an_empty_plan(lib):
    assert build_from_listing(lib, (FIXTURES / "library-listing.txt").read_text(encoding="utf-8")) > 15
    plan, traces = build_plan(lib.cfg, audit=True)
    assert plan.to_dict()["items"] == [], {i.id: (i.findings, i.ops) for i in plan.items}


@pytest.mark.skipif(not os.environ.get("TIDYSHELF_LIBRARY_LISTING"), reason="set TIDYSHELF_LIBRARY_LISTING to a "
                    "listing of your own library (kept outside the repo) to run this")
def test_owners_library_listing_audits_to_an_empty_plan(lib):
    listing = Path(os.environ["TIDYSHELF_LIBRARY_LISTING"]).read_text(encoding="utf-8")
    build_from_listing(lib, listing)
    plan, _ = build_plan(lib.cfg, audit=True)
    problems = {i.source: [f["code"] for f in i.findings] or [op["op"] for op in i.ops] for i in plan.items}
    assert problems == {}
