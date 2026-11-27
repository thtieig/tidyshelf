"""Video quality: parsing ffprobe output and checking it against [video.quality]."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tidyshelf import quality
from tidyshelf.quality import check, parse_ffprobe, summary

PROBES = Path(__file__).resolve().parent.parent / "fixtures" / "ffprobe"


def load(name):
    return parse_ffprobe(json.loads((PROBES / f"{name}.json").read_text()), 0)


def codes(p, cfg):
    return [(action, code) for action, code, _msg in check(p, cfg)]


def test_parse_fields():
    p = load("fhd_h264")
    assert (p.codec, p.width, p.height, p.duration, p.size, p.audio_streams) == ("h264", 1920, 1080, 6000.0, 1_800_000_000, 1)
    assert p.video_kbps == 2200 and p.container == "matroska,webm"
    assert round(p.gb_per_hour, 2) == 1.08 and p.res_class == "fhd"


def test_scope_counts_as_full_hd(cfg):
    p = load("scope")
    assert p.res_class == "fhd" and codes(p, cfg) == []


@pytest.mark.parametrize("name, expected", [
    ("fhd_h264", []),
    ("uhd_large", [("warn", "quality.large")]),
    ("sd_tiny", [("warn", "quality.tiny")]),
    ("no_audio", [("reject", "quality.no_audio")]),
    ("odd_codec", [("warn", "quality.codec")]),
    ("low_res", [("warn", "quality.low_res")]),
    ("broken", [("warn", "quality.suspect")]),
])
def test_policy(cfg, name, expected):
    assert codes(load(name), cfg) == expected


def test_actions_come_from_config(cfg):
    cfg["video"]["quality"]["on_large"] = "reject"
    cfg["video"]["quality"]["on_no_audio"] = "ok"
    assert codes(load("uhd_large"), cfg) == [("reject", "quality.large")]
    assert codes(load("no_audio"), cfg) == []


def test_audio_not_required(cfg):
    cfg["video"]["quality"]["audio_required"] = False
    assert codes(load("no_audio"), cfg) == []


def test_embedded_subtitles_listed():
    assert load("embedded_subs").subtitle_langs == ["eng", "spa"]


def test_summary():
    assert summary(load("fhd_h264")) == "1080p h264, 1.08 GB/h, 2200 kbit/s, 1 audio"
    assert summary(load("broken")) == "no usable video stream"


def test_reencode_hint_is_only_text():
    hint = quality.reencode_hint("A Film (2000).mkv", load("uhd_large"))
    assert hint.startswith("ffmpeg -i 'A Film (2000).mkv'") and "libx265" in hint


def test_unavailable_ffprobe_gives_none(cfg, monkeypatch):
    cfg["video"]["ffprobe"] = ""
    assert quality.probe("/nonexistent.mkv", cfg) is None
    cfg["video"]["ffprobe"] = "no-such-ffprobe-binary"
    assert quality.probe("/nonexistent.mkv", cfg) is None


def test_ffprobe_called_without_shell_on_an_absolute_path(cfg, monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0, (PROBES / "fhd_h264.json").read_text(), "")

    monkeypatch.setattr(quality.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(quality.subprocess, "run", fake_run)
    (tmp_path / "-odd.mkv").write_bytes(b"x")
    p = quality.probe(str(tmp_path / "-odd.mkv"), cfg)
    assert p.codec == "h264"
    assert seen["argv"][0] == "/usr/bin/ffprobe" and seen["argv"][-1] == str(tmp_path / "-odd.mkv")
    assert seen.get("shell", False) is False and seen["timeout"] > 0


def test_ffprobe_failure_is_suspect(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(quality.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(quality.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "Invalid data"))
    (tmp_path / "a.mkv").write_bytes(b"x")
    assert codes(quality.probe(str(tmp_path / "a.mkv"), cfg), cfg) == [("warn", "quality.suspect")]


def test_deep_check_reports_decoder_errors(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(quality.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(quality.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "corrupt frame"))
    assert "corrupt frame" in quality.deep_check(str(tmp_path / "a.mkv"), cfg)
    monkeypatch.setattr(quality.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    assert quality.deep_check(str(tmp_path / "a.mkv"), cfg) is None


@pytest.mark.skipif(not (shutil.which("ffprobe") and shutil.which("ffmpeg")), reason="needs ffprobe and ffmpeg")
def test_real_ffprobe_on_a_generated_clip(cfg, tmp_path):
    clip = tmp_path / "clip.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25:duration=2",
                    "-f", "lavfi", "-i", "sine=duration=2", "-shortest", str(clip)], check=True)
    p = quality.probe(str(clip), cfg)
    assert (p.width, p.height, p.audio_streams) == (1280, 720, 1)
