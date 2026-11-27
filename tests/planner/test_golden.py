"""The golden movie: invented, but shaped like the real sample PLAN §7 describes.

A duplicate loose subtitle, an "English" file whose text is Romanian, SDH as the only real English,
an advertising cue, and a readme. Explicit assertions first, so a wrong golden file cannot be
blessed by regenerating it; then the whole plan against the stored copy.
"""

import base64
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
    # The SDH subtitle has an ad cue and the Spanish one is cp1252: both are written as cleaned copies.
    writes = {op["to"]: op for op in item["ops"] if op["op"] == "write"}
    assert sorted(writes) == ["movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.sdh.srt",
                              "movies:Winter Lights (2004)/Subs/Winter Lights (2004).es.srt"]
    write = writes["movies:Winter Lights (2004)/Subs/Winter Lights (2004).en.sdh.srt"]
    spanish = base64.b64decode(writes["movies:Winter Lights (2004)/Subs/Winter Lights (2004).es.srt"]["data_b64"])
    assert spanish.decode("utf-8").startswith("1\n00:00:03,000 --> 00:00:05,500\n¿Dónde estabas anoche?")
    assert write["src"].endswith("/Subs/English SDH.eng.srt")
    cleaned = base64.b64decode(write["data_b64"]).decode("utf-8")
    assert "www.example.com" not in cleaned and cleaned.startswith("1\n") and cleaned.endswith("\n")
    assert len(cleaned.split("\n\n")) == 38  # 39 cues, the last was the ad
    for junk in ("English.eng.srt", f"{VIDEO}.srt", "readme.txt", "English SDH.eng.srt", "Spanish.spa.srt"):
        assert moves[junk].startswith("source:.tidyshelf-trash/source/"), junk
    assert len(moves) == 6  # the video and five trashed originals: nothing else moves
    codes = {f["code"] for f in item["findings"]}
    assert {"subtitle.language_mismatch", "subtitle.duplicate", "subtitle.dropped_language", "subtitle.cleaned"} <= codes
    assert "subtitle.ad_cues" not in codes
    (mismatch,) = [f["msg"] for f in item["findings"] if f["code"] == "subtitle.language_mismatch"]
    assert mismatch.startswith("Subs/English.eng.srt is labelled en (English) but its text is ro (Romanian) (confidence 0.9")
    assert [op["path"] for op in item["ops"] if op["op"] == "delete"] == [f"source:{FOLDER}/Subs", f"source:{FOLDER}"]
    assert without_tool(plan.to_dict()) == without_tool(json.loads(GOLDEN.read_text(encoding="utf-8")))
