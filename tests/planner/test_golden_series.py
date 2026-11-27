"""Golden series plan on invented shows: a season pack (multi-episode file, subtitles in Subs/, an
extras folder, junk) and a show folder with a season folder and specials."""

import json
from pathlib import Path

from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "harbour-lights.plan.json"
PACK = "Harbour.Lights.S01.720p"
S1 = "series:Harbour Lights/Season 1"


def golden_tree(lib):
    for name, size in (("Harbour.Lights.S01E01.Pilot.720p.mkv", 300), ("Harbour.Lights.S01E02.The.Storm.720p.mkv", 300),
                       ("Harbour.Lights.S01E03E04.Two.Parter.720p.mkv", 600), ("Featurettes/making-of.mkv", 50),
                       ("info.nfo", 20)):
        lib.write("source", f"{PACK}/{name}", b"v" * size)
    lib.write("source", f"{PACK}/Subs/Harbour.Lights.S01E01.eng.srt", (SUBS / "en_plain.srt").read_bytes())
    lib.write("source", f"{PACK}/Subs/Harbour.Lights.S01E02.eng.srt", (SUBS / "en_sdh.srt").read_bytes())
    lib.write("source", f"{PACK}/Subs/Harbour.Lights.S01E02.spa.srt", (SUBS / "es.srt").read_bytes())
    lib.write("source", "Old Coast/Season 2/Old Coast - 2x03 - The Lighthouse.mkv", b"v" * 200)
    lib.write("source", "Old Coast/Specials/Old.Coast.S00E01.Christmas.mkv", b"v" * 100)


def without_tool(doc):
    return {k: v for k, v in doc.items() if k != "tool"}


def test_golden_series(lib):
    golden_tree(lib)
    plan, _ = build_plan(lib.cfg, created="2026-11-27T09:00:00Z")
    pack, coast = plan.to_dict()["items"]
    assert (pack["id"], pack["status"], coast["id"], coast["status"]) == ("series-0001", "ready", "series-0002", "ready")
    moves = {op["from"].rsplit("/", 1)[-1]: op["to"] for op in pack["ops"] if op["op"] == "move"}
    # The SDH subtitle has an ad cue and the Spanish one is cp1252: both are written as cleaned copies.
    writes = {op["to"]: op["src"] for op in pack["ops"] if op["op"] == "write"}
    assert writes == {f"{S1}/S01E02 - The Storm.en.sdh.srt": f"source:{PACK}/Subs/Harbour.Lights.S01E02.eng.srt",
                      f"{S1}/S01E02 - The Storm.es.srt": f"source:{PACK}/Subs/Harbour.Lights.S01E02.spa.srt"}
    for original in ("Harbour.Lights.S01E02.eng.srt", "Harbour.Lights.S01E02.spa.srt"):
        assert moves[original].startswith("source:.tidyshelf-trash/source/")
    assert moves["Harbour.Lights.S01E01.Pilot.720p.mkv"] == f"{S1}/S01E01 - Pilot.mkv"
    assert moves["Harbour.Lights.S01E02.The.Storm.720p.mkv"] == f"{S1}/S01E02 - The Storm.mkv"
    assert moves["Harbour.Lights.S01E03E04.Two.Parter.720p.mkv"] == f"{S1}/S01E03-E04 - Two Parter.mkv"
    assert moves["Harbour.Lights.S01E01.eng.srt"] == f"{S1}/S01E01 - Pilot.en.srt"
    assert moves["info.nfo"].startswith("source:.tidyshelf-trash/source/")
    assert "making-of.mkv" not in moves
    assert [op["path"] for op in pack["ops"] if op["op"] == "delete"] == [f"source:{PACK}/Subs"]  # Featurettes stays
    coast_moves = sorted(op["to"] for op in coast["ops"] if op["op"] == "move")
    assert coast_moves == ["series:Old Coast/Season 00/S00E01 - Christmas.mkv",
                           "series:Old Coast/Season 2/S02E03 - The Lighthouse.mkv"]
    assert without_tool(plan.to_dict()) == without_tool(json.loads(GOLDEN.read_text(encoding="utf-8")))
