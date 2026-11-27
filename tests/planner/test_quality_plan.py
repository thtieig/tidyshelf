"""Quality checks inside plans."""

from pathlib import Path

from tidyshelf import cli, quality
from tidyshelf.movies import build_plan

SUBS = Path(__file__).resolve().parent.parent / "fixtures" / "subs"
FOLDER = "Winter.Lights.2004.1080p"


def plan_items(lib, **kw):
    plan, traces = build_plan(lib.cfg, **kw)
    return plan, plan.to_dict()["items"], traces


def codes(item):
    return [f["code"] for f in item["findings"]]


def test_large_file_is_a_warning_with_a_reencode_hint(lib, probes):
    lib.tree("source", f"{FOLDER}/w.mkv")
    probes({"w.mkv": "uhd_large"})
    _plan, (item,), traces = plan_items(lib)
    assert item["status"] == "ready" and "quality.large" in codes(item)
    assert any("libx265" in line for line in traces[item["id"]])
    assert any("quality w.mkv: 2160p hevc" in line for line in traces[item["id"]])


def test_no_audio_rejects_the_movie(lib, probes):
    lib.tree("source", f"{FOLDER}/w.mkv")
    probes({"w.mkv": "no_audio"})
    _plan, (item,), _t = plan_items(lib)
    assert (item["status"], item["ops"]) == ("needs_review", [])
    assert [f["level"] for f in item["findings"] if f["code"] == "quality.no_audio"] == ["error"]


def test_no_audio_episode_rejects_the_series_item(lib, probes):
    lib.tree("source", "Harbour.Lights.S01.720p/Harbour.Lights.S01E01.mkv\nHarbour.Lights.S01.720p/Harbour.Lights.S01E02.mkv")
    probes({"Harbour.Lights.S01E01.mkv": "fhd_h264", "Harbour.Lights.S01E02.mkv": "no_audio"})
    _plan, (item,), _t = plan_items(lib)
    assert item["status"] == "needs_review" and "quality.no_audio" in codes(item)


def test_conflict_shows_both_qualities(lib, probes):
    lib.tree("movies", "Winter Lights (2004)/Winter Lights (2004).mkv")
    lib.tree("source", f"{FOLDER}/w.mkv")
    probes({"w.mkv": "uhd_large", "Winter Lights (2004).mkv": "fhd_h264"})
    _plan, (item,), _t = plan_items(lib)
    (msg,) = [f["msg"] for f in item["findings"] if f["code"] == "conflict.exists"]
    assert "1080p h264" in msg and "2160p hevc" in msg


def test_subtitle_far_beyond_the_video(lib, probes):
    lib.tree("source", f"{FOLDER}/w.mkv")
    late = (SUBS / "en_plain.srt").read_text(encoding="utf-8") + "\n99\n02:46:40,000 --> 02:46:42,000\nThe end.\n"
    lib.write("source", f"{FOLDER}/w.en.srt", late.encode("utf-8"))
    probes({"w.mkv": "fhd_h264"})  # 6000 s; the last cue ends at 10002 s
    _plan, (item,), _t = plan_items(lib)
    assert "subtitle.beyond_video" in codes(item) and item["status"] == "ready"


def test_subtitle_within_the_video_is_fine(lib, probes):
    lib.tree("source", f"{FOLDER}/w.mkv")
    lib.write("source", f"{FOLDER}/w.en.srt", (SUBS / "en_plain.srt").read_bytes())
    probes({"w.mkv": "fhd_h264"})
    _plan, (item,), _t = plan_items(lib)
    assert "subtitle.beyond_video" not in codes(item)


def test_embedded_subtitles_reported(lib, probes):
    lib.tree("source", f"{FOLDER}/w.mkv")
    probes({"w.mkv": "embedded_subs"})
    _plan, (item,), _t = plan_items(lib)
    (msg,) = [f["msg"] for f in item["findings"] if f["code"] == "quality.embedded_subs"]
    assert "eng" in msg and "spa" in msg


def test_unchecked_quality_is_one_plan_warning_not_a_finding(lib):
    lib.tree("source", f"{FOLDER}/w.mkv\nOther.Film.1999/o.mkv")
    plan, items, _t = plan_items(lib)
    assert all(not any(c.startswith("quality.") for c in codes(it)) for it in items)
    assert plan.warnings == ["ffprobe is not available: video quality was not checked"]


def test_deep_check_failure_needs_review(lib, monkeypatch):
    lib.tree("source", f"{FOLDER}/w.mkv")
    monkeypatch.setattr(quality, "deep_check", lambda path, cfg: "corrupt frame at 00:12:03")
    _plan, (item,), _t = plan_items(lib, deep=True)
    assert item["status"] == "needs_review" and "quality.corrupt" in codes(item)
    _plan, (item,), _t = plan_items(lib)
    assert item["status"] == "ready"  # --deep is off by default


def test_cli_warns_once_and_passes_deep(lib, capsys, monkeypatch):
    lib.tree("source", f"{FOLDER}/w.mkv\nOther.Film.1999/o.mkv")
    calls = []
    monkeypatch.setattr(quality, "deep_check", lambda path, cfg: calls.append(path))
    code = cli.main(["plan", "--deep", "--config", str(lib.config_path)])
    _out, err = capsys.readouterr()
    assert code == 2 and err.count("video quality was not checked") == 1 and len(calls) == 2
