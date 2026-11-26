"""The golden movie: invented, but shaped like the real sample PLAN §7 describes.

A duplicate loose subtitle, an "English" file whose text is Romanian, SDH as the only real English,
an advertising cue, and a readme. Explicit assertions first, so a wrong golden file cannot be
blessed by regenerating it; then the whole plan against the stored copy.
"""

import json
from pathlib import Path

from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "winter-lights.plan.json"
FOLDER = "www.example.com - Winter Lights (2004) 1080p BluRay-GRP"
VIDEO = "Winter.Lights.2004.1080p.BluRay.x264.AAC-GRP"


def golden_tree(lib):
    lib.write("source", f"{FOLDER}/{VIDEO}.mp4", b"v" * 1000)
    lib.write("source", f"{FOLDER}/{VIDEO}.srt", (SUBS / "ro.srt").read_bytes())
    lib.write("source", f"{FOLDER}/Subs/English.eng.srt", (SUBS / "ro.srt").read_bytes())
    lib.write("source", f"{FOLDER}/Subs/English SDH.eng.srt", (SUBS / "en_sdh.srt").read_bytes())
    lib.write("source", f"{FOLDER}/Subs/Spanish.spa.srt", (SUBS / "es.srt").read_bytes())
    lib.write("source", f"{FOLDER}/readme.txt", b"r" * 86)


def without_tool(doc):
    return {k: v for k, v in doc.items() if k != "tool"}  # the version string is not part of the golden


def test_golden_winter_lights(lib):
    golden_tree(lib)
    plan, _ = build_plan(lib.cfg, created="2026-11-26T20:15:00Z")
    (item,) = plan.to_dict()["items"]
    assert item["status"] == "ready"
    moves = {op["from"].split("/")[-1]: op["to"] for op in item["ops"] if op["op"] == "move"}
    assert moves[f"{VIDEO}.mp4"] == "movies:Winter Lights (2004)/Winter Lights (2004).mp4"
    assert moves["English SDH.eng.srt"] == "movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.sdh.srt"
    assert moves["Spanish.spa.srt"] == "movies:Winter Lights (2004)/Subs/Winter Lights (2004).es.srt"
    for junk in ("English.eng.srt", f"{VIDEO}.srt", "readme.txt"):
        assert moves[junk].startswith("source:.tidyshelf-trash/source/"), junk
    assert len(moves) == 6
    codes = {f["code"] for f in item["findings"]}
    assert {"subtitle.language_mismatch", "subtitle.duplicate", "subtitle.dropped_language", "subtitle.ad_cues"} <= codes
    (mismatch,) = [f["msg"] for f in item["findings"] if f["code"] == "subtitle.language_mismatch"]
    assert mismatch.startswith("Subs/English.eng.srt is labelled English but its text is Romanian (confidence 0.9")
    assert [op["path"] for op in item["ops"] if op["op"] == "delete"] == [f"source:{FOLDER}/Subs", f"source:{FOLDER}"]
    assert without_tool(plan.to_dict()) == without_tool(json.loads(GOLDEN.read_text(encoding="utf-8")))
