"""Golden audit plan on an invented library with a few inconsistencies; the consistent parts
produce no item."""

import json
from pathlib import Path

from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "audit.plan.json"


def golden_tree(lib):
    lib.write("movies", "Bright Harbour (2011)/bright.harbour.2011.1080p.mkv", b"v" * 500)
    lib.write("movies", "Bright Harbour (2011)/English.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.write("movies", "Bright Harbour (2011)/cover.jpg", b"j" * 40)
    lib.write("movies", "Winter.Lights.2004.720p/Winter.Lights.2004.720p.mkv", b"v" * 400)
    lib.write("movies", "Old Mill (1987)/Old Mill (1987).mkv", b"v" * 300)
    lib.write("series", "Harbour Lights/Season 01/Harbour.Lights.S01E01.Pilot.mkv", b"v" * 200)
    lib.write("series", "Harbour Lights/Season 1/S01E02 - The Storm.mkv", b"v" * 200)


def without_tool(doc):
    return {k: v for k, v in doc.items() if k != "tool"}


def test_golden_audit(lib):
    golden_tree(lib)
    plan, _ = build_plan(lib.cfg, audit=True, created="2026-11-27T11:00:00Z")
    items = plan.to_dict()["items"]
    assert [it["source"] for it in items] == ["movies:Bright Harbour (2011)", "movies:Winter.Lights.2004.720p",
                                              "series:Harbour Lights"]
    assert all(it["status"] == "ready" for it in items)
    assert not any(op["op"] == "delete" for it in items for op in it["ops"])
    empty = [f["msg"] for it in items for f in it["findings"] if f["code"] == "audit.empty_folder"]
    assert len(empty) == 2 and "Winter.Lights.2004.720p" in empty[0] and "Season 01" in empty[1]
    assert without_tool(plan.to_dict()) == without_tool(json.loads(GOLDEN.read_text(encoding="utf-8")))
