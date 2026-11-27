"""Golden plan with recorded quality: a large UHD movie (warning) and a show whose second episode
has no audio (rejected, so the item needs review)."""

import json
from pathlib import Path

from tidyshelf.movies import build_plan

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "quality.plan.json"
MOVIE = "Bright.Harbour.2011.2160p"
PROBES = {"Bright.Harbour.2011.2160p.mkv": "uhd_large",
          "Old Coast - 1x01 - Arrival.mkv": "fhd_h264", "Old Coast - 1x02 - Silence.mkv": "no_audio"}


def golden_tree(lib):
    lib.write("source", f"{MOVIE}/Bright.Harbour.2011.2160p.mkv", b"v" * 900)
    lib.write("source", "Old Coast/Season 1/Old Coast - 1x01 - Arrival.mkv", b"v" * 300)
    lib.write("source", "Old Coast/Season 1/Old Coast - 1x02 - Silence.mkv", b"v" * 300)


def without_tool(doc):
    return {k: v for k, v in doc.items() if k != "tool"}


def test_golden_quality(lib, probes):
    golden_tree(lib)
    probes(PROBES)
    plan, traces = build_plan(lib.cfg, created="2026-11-27T10:00:00Z")
    movie, show = plan.to_dict()["items"]
    assert (movie["status"], show["status"]) == ("ready", "needs_review")
    assert [f["code"] for f in movie["findings"]] == ["quality.large"]
    assert show["ops"] == [] and [(f["level"], f["code"]) for f in show["findings"]] == [("error", "quality.no_audio")]
    assert any("libx265" in line for line in traces[movie["id"]])
    assert plan.warnings == []
    assert without_tool(plan.to_dict()) == without_tool(json.loads(GOLDEN.read_text(encoding="utf-8")))
