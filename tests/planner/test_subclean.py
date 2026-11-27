"""Cleaning kept .srt subtitles: ad cues removed, non-UTF-8 converted, written as a new file."""

import base64
import hashlib
import re
from pathlib import Path

import pytest

from tidyshelf import textsubs
from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
FOLDER = "Winter.Lights.2004.1080p"
LIB = "movies:Winter Lights (2004)"
TRASH = f"source:.tidyshelf-trash/source/{FOLDER}"


def stamp(seconds):
    return f"00:{seconds // 60:02}:{seconds % 60:02}"


def srt(cues):
    return "\n".join(f"{i}\n{stamp(i * 3)},000 --> {stamp(i * 3 + 2)},500\n{text}\n" for i, text in enumerate(cues, 1))


EN = [f"I will see you at the harbour when the tide comes in, number {n}." for n in range(30)]
AD_FIRST = "For best IPTV provider, please visit: WWW.EXAMPLE.CAT"
AD_LAST = "Subtitles by www.example.com"


def plan_one(lib, name="Subs/English.eng.srt", data=b""):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/{name}", data)
    plan, _ = build_plan(lib.cfg)
    (item,) = plan.to_dict()["items"]
    return item


def op_by(item, kind):
    return [op for op in item["ops"] if op["op"] == kind]


def written(op):
    return base64.b64decode(op["data_b64"]).decode("utf-8")


def test_ad_cues_removed_and_cues_renumbered(lib):
    data = srt([AD_FIRST] + EN + [AD_LAST]).encode()
    item = plan_one(lib, data=data)
    (w,) = op_by(item, "write")
    assert w["to"] == f"{LIB}/Subs/Winter Lights (2004).en.srt"
    assert w["src"] == f"source:{FOLDER}/Subs/English.eng.srt"
    assert (w["size"], w["src_sha256"]) == (len(data), hashlib.sha256(data).hexdigest())
    assert w["sha256"] == hashlib.sha256(base64.b64decode(w["data_b64"])).hexdigest()
    text = written(w)
    assert AD_FIRST not in text and AD_LAST not in text
    blocks = text.split("\n\n")
    assert len(blocks) == 30 and text.endswith("\n") and not text.endswith("\n\n")
    assert [b.split("\n")[0] for b in blocks] == [str(n) for n in range(1, 31)]
    assert blocks[0].split("\n")[1] == "00:00:06,000 --> 00:00:08,500"  # timing of the old cue 2 is untouched
    assert blocks[0].split("\n")[2] == EN[0]
    # the original is trashed (as junk is), never written to; the cleaned copy replaces the move
    assert {"op": "move", "from": w["src"], "to": f"{TRASH}/Subs/English.eng.srt", "size": w["size"],
            "mtime": w["mtime"]} in item["ops"]
    assert all(op["op"] != "move" or op["to"] != f"{LIB}/Subs/Winter Lights (2004).en.srt" for op in item["ops"])
    assert {"op": "set_mode", "path": w["to"]} in item["ops"]
    ops = item["ops"]
    assert ops.index(w) < ops.index(next(op for op in ops if op.get("from") == w["src"]))  # write, then trash
    (f,) = [f for f in item["findings"] if f["code"] == "subtitle.cleaned"]
    assert f["level"] == "info" and "2 advertising cues removed" in f["msg"] and "converted" not in f["msg"]
    assert "subtitle.ad_cues" not in [f["code"] for f in item["findings"]]


def test_cp1252_converted_to_utf8(lib):
    spanish = (SUBS / "es.srt").read_bytes()
    item = plan_one(lib, "Subs/Spanish.spa.srt", spanish)
    (w,) = op_by(item, "write")
    text = written(w)
    assert text.startswith("1\n00:00:03,000 --> 00:00:05,500\n¿Dónde estabas anoche?")
    assert text == spanish.decode("cp1252").replace("\r\n", "\n")  # same text, same cues
    (f,) = [f for f in item["findings"] if f["code"] == "subtitle.cleaned"]
    assert "converted from cp1252 to UTF-8" in f["msg"] and "advertising" not in f["msg"]


def test_clean_utf8_file_is_moved_as_before(lib):
    item = plan_one(lib, data=(SUBS / "en_plain.srt").read_bytes())
    assert op_by(item, "write") == []
    assert f"{LIB}/Subs/Winter Lights (2004).en.srt" in [op["to"] for op in op_by(item, "move")]
    assert "subtitle.cleaned" not in [f["code"] for f in item["findings"]]


def test_utf8_bom_without_ads_is_left_alone(lib):
    item = plan_one(lib, data=b"\xef\xbb\xbf" + srt(EN).encode())
    assert op_by(item, "write") == []


def test_clean_ads_off_keeps_the_report(lib):
    lib.write_config('[subtitles]\nclean_ads = false\n')
    item = plan_one(lib, data=srt([AD_FIRST] + EN).encode())
    assert op_by(item, "write") == []
    assert "subtitle.ad_cues" in [f["code"] for f in item["findings"]]


def test_to_utf8_off_leaves_encoding_but_still_cleans_ads_of_utf8(lib):
    lib.write_config('[subtitles]\nto_utf8 = false\n')
    item = plan_one(lib, "Subs/Spanish.spa.srt", (SUBS / "es.srt").read_bytes())
    assert op_by(item, "write") == []
    assert "subtitle.not_utf8" in [f["code"] for f in item["findings"]]
    lib2 = srt([AD_FIRST] + EN).encode()
    assert textsubs.clean_srt(lib2, "utf-8", [re.compile("IPTV")], True, False).ads_removed == 1


def test_both_off_changes_nothing(lib):
    lib.write_config('[subtitles]\nclean_ads = false\nto_utf8 = false\n')
    item = plan_one(lib, "Subs/Spanish.spa.srt", (SUBS / "es.srt").read_bytes())
    assert op_by(item, "write") == []


def test_cp1252_with_ads_does_both(lib):
    cues = ["¿Dónde estabas anoche? Te estuve esperando, y no vino nadie a la puerta."] * 28
    data = srt([AD_FIRST] + cues).encode("cp1252")
    item = plan_one(lib, "Subs/Spanish.spa.srt", data)
    (w,) = op_by(item, "write")
    assert written(w).count("\n\n") == 27 and "IPTV" not in written(w) and "¿Dónde" in written(w)
    (f,) = [f for f in item["findings"] if f["code"] == "subtitle.cleaned"]
    assert "1 advertising cue removed, converted from cp1252 to UTF-8" in f["msg"]


def test_ad_in_the_middle_is_not_touched(lib):
    cues = EN[:10] + [AD_FIRST] + EN[10:]
    item = plan_one(lib, data=srt(cues).encode())
    assert op_by(item, "write") == []  # only the first and last three cues count, as in the report


def test_crlf_file_is_rewritten_with_lf_only_when_ads_are_removed(lib):
    data = srt([AD_FIRST] + EN).replace("\n", "\r\n").encode()
    (w,) = op_by(plan_one(lib, data=data), "write")
    assert "\r" not in written(w)


def test_file_with_text_outside_cues_is_not_restructured():
    data = ("Intro line\n\n" + srt([AD_FIRST] + EN)).encode()
    assert textsubs.clean_srt(data, "utf-8", [re.compile("IPTV")], True, True) is None


def test_undecodable_bytes_never_convert():
    data = srt(EN).encode("cp1252") + b"\x81"  # 0x81 has no meaning in cp1252
    assert textsubs.clean_srt(data, "cp1252", [], True, True) is None


def test_unknown_language_non_utf8_is_not_converted(lib):
    data = srt(["Kako ste, dobro jutro svima."] * 25).encode("cp1250")
    item = plan_one(lib, "Subs/English.eng.srt", data)  # short text: language not recognised, encoding a guess
    assert op_by(item, "write") == []


def test_series_subtitle_is_written_too(lib):
    pack = "Harbour.Lights.S01.720p"
    lib.write("source", f"{pack}/Harbour.Lights.S01E01.mkv", b"v" * 10)
    lib.write("source", f"{pack}/Harbour.Lights.S01E01.eng.srt", srt([AD_FIRST] + EN).encode())
    plan, _ = build_plan(lib.cfg)
    (item,) = plan.to_dict()["items"]
    (w,) = op_by(item, "write")
    assert w["to"] == "series:Harbour Lights/Season 1/S01E01.en.srt"


def test_plan_id_covers_the_data(lib):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/English.eng.srt", srt([AD_FIRST] + EN).encode())
    plan, _ = build_plan(lib.cfg)
    before = plan.plan_id
    op = next(op for op in plan.items[0].ops if op["op"] == "write")
    op["data_b64"] = base64.b64encode(b"other").decode()
    assert plan.plan_id != before


def test_junk_delete_mode_deletes_the_original(lib):
    lib.write_config('[junk]\njunk_action = "delete"\n')
    item = plan_one(lib, data=srt([AD_FIRST] + EN).encode())
    (w,) = op_by(item, "write")
    assert any(op["op"] == "delete" and op["path"] == w["src"] for op in item["ops"])
    assert ops_order(item).index("write") < ops_order(item).index("delete")


def ops_order(item):
    return [op["op"] for op in item["ops"]]


def test_audit_never_cleans(lib):
    lib.write("movies", "Winter Lights (2004)/Winter Lights (2004).mkv", b"v" * 10)
    lib.write("movies", "Winter Lights (2004)/Subs/Winter Lights (2004).en.srt", srt([AD_FIRST] + EN).encode())
    plan, _ = build_plan(lib.cfg, audit=True)
    assert all(op["op"] != "write" for it in plan.items for op in it.ops)
