"""Regenerate the recorded ffprobe outputs in this folder: python tests/fixtures/ffprobe/generate.py

They have the shape of `ffprobe -v error -print_format json -show_format -show_streams FILE`
for invented files; the numbers are chosen to hit each quality rule.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent


def video(codec, width, height, kbps):
    return {"index": 0, "codec_type": "video", "codec_name": codec, "width": width, "height": height,
            "bit_rate": str(kbps * 1000)}


def audio(index=1, channels=2, lang="eng"):
    return {"index": index, "codec_type": "audio", "codec_name": "aac", "channels": channels, "tags": {"language": lang}}


def sub(index, lang):
    return {"index": index, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": lang}}


def doc(streams, seconds, size, container="matroska,webm"):
    return {"streams": streams, "format": {"format_name": container, "duration": f"{seconds:.6f}",
                                           "size": str(size), "bit_rate": str(int(size * 8 / seconds))}}


FIXTURES = {
    "fhd_h264": doc([video("h264", 1920, 1080, 2200), audio()], 6000, 1_800_000_000),
    "scope": doc([video("h264", 1920, 816, 2300), audio()], 6600, 2_000_000_000, "mov,mp4,m4a,3gp,3g2,mj2"),
    "uhd_large": doc([video("hevc", 3840, 2160, 60000), audio(channels=8)], 7200, 60_000_000_000),
    "sd_tiny": doc([video("mpeg4", 720, 480, 140), audio()], 5400, 100_000_000, "avi"),
    "no_audio": doc([video("h264", 1280, 720, 2600)], 2700, 900_000_000),
    "odd_codec": doc([video("wmv3", 1280, 720, 2000), audio()], 2700, 700_000_000, "asf"),
    "low_res": doc([video("h264", 480, 360, 1000), audio()], 3600, 500_000_000),
    "broken": {"streams": [], "format": {}},
    "embedded_subs": doc([video("h264", 1920, 1080, 2200), audio(), sub(2, "eng"), sub(3, "spa")], 6000, 1_800_000_000),
}

if __name__ == "__main__":
    for name, data in FIXTURES.items():
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
