"""Video quality: read a file with ffprobe and check it against [video.quality] (PLAN §7).

Read-only. The programs run without a shell, on absolute paths, with a timeout. Nothing is ever
re-encoded; a large file only gets a suggested command.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field

PROBE_TIMEOUT = 120
DEEP_TIMEOUT = 4 * 3600
CLASSES = (("sd", 0), ("hd", 720), ("fhd", 1080), ("uhd", 2160))  # class, lowest effective height


@dataclass
class Probe:
    codec: str | None = None
    width: int = 0
    height: int = 0
    duration: float = 0.0  # seconds
    size: int = 0
    video_kbps: int = 0
    audio_streams: int = 0
    audio_channels: int = 0
    container: str = ""
    subtitle_langs: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.codec is not None and self.duration > 0 and self.error is None

    @property
    def eff_height(self) -> int:
        return max(self.height, round(self.width * 9 / 16))

    @property
    def res_class(self) -> str:
        return [name for name, low in CLASSES if self.eff_height >= low][-1]

    @property
    def gb_per_hour(self) -> float:
        return self.size / 1e9 / (self.duration / 3600) if self.duration else 0.0


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_ffprobe(data: dict, size: int) -> Probe:
    """A Probe from ffprobe's JSON (-show_format -show_streams). `size` is used if ffprobe has none."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    p = Probe(duration=float(fmt.get("duration") or 0), size=_int(fmt.get("size"), size),
              container=str(fmt.get("format_name") or ""))
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    p.subtitle_langs = [str((s.get("tags") or {}).get("language", "und")) for s in streams
                        if s.get("codec_type") == "subtitle"]
    p.audio_streams = len(audio)
    p.audio_channels = max((_int(s.get("channels")) for s in audio), default=0)
    if video is not None:
        p.codec = str(video.get("codec_name") or "unknown")
        p.width, p.height = _int(video.get("width")), _int(video.get("height"))
        kbps = _int(video.get("bit_rate")) // 1000
        if not kbps and p.duration:  # no per-stream rate (common in mkv): the whole file's
            kbps = int(p.size * 8 / p.duration / 1000)
        p.video_kbps = kbps
    return p


def _program(cfg: dict, key: str) -> str | None:
    name = cfg["video"].get(key) or ""
    return shutil.which(name) if name else None


def run_ffprobe(path: str, cfg: dict) -> dict | None:
    """ffprobe's JSON for `path`; None when ffprobe is not available. Raises RuntimeError on failure."""
    exe = _program(cfg, "ffprobe")
    if exe is None:
        return None
    argv = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", os.path.abspath(path)]
    try:
        out = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ffprobe failed: {exc}") from None
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:200] or out.returncode}")
    try:
        return json.loads(out.stdout)
    except ValueError:
        raise RuntimeError("ffprobe output is not JSON") from None


def probe(path: str, cfg: dict) -> Probe | None:
    """None when quality cannot be checked here; a Probe (maybe with .error) otherwise."""
    try:
        data = run_ffprobe(path, cfg)
    except RuntimeError as exc:
        return Probe(size=os.lstat(path).st_size, error=str(exc))
    if data is None:
        return None
    return parse_ffprobe(data, os.lstat(path).st_size)


def check(p: Probe, cfg: dict) -> list[tuple[str, str, str]]:
    """(action, code, message) for each rule the file breaks; action is "warn" or "reject"."""
    q = cfg["video"]["quality"]
    if not p.usable:
        return [("warn", "quality.suspect", p.error or "no usable video stream or no duration")]
    limits = q[p.res_class]
    out = []

    def rule(action_key: str, code: str, msg: str) -> None:
        if q[action_key] != "ok":
            out.append((q[action_key], code, msg))

    if p.gb_per_hour > limits["max_gb_per_hour"] or p.video_kbps > limits["max_video_kbps"]:
        rule("on_large", "quality.large", f"{summary(p)}: above the {p.res_class} limits "
             f"({limits['max_gb_per_hour']} GB/h, {limits['max_video_kbps']} kbit/s)")
    elif p.gb_per_hour < limits["min_gb_per_hour"]:
        rule("on_tiny", "quality.tiny", f"{summary(p)}: below {limits['min_gb_per_hour']} GB/h for {p.res_class}")
    if p.codec not in q["allowed_codecs"]:
        rule("on_codec", "quality.codec", f"video codec {p.codec} is not in allowed_codecs")
    if p.eff_height < q["min_height"]:
        rule("on_low_res", "quality.low_res", f"{p.width}x{p.height} is below min_height {q['min_height']}")
    if q["audio_required"] and p.audio_streams == 0:
        rule("on_no_audio", "quality.no_audio", "no audio track")
    return out


def summary(p: Probe) -> str:
    if not p.usable:
        return "no usable video stream"
    return f"{p.eff_height}p {p.codec}, {p.gb_per_hour:.2f} GB/h, {p.video_kbps} kbit/s, {p.audio_streams} audio"


def reencode_hint(name: str, p: Probe) -> str:
    """A suggested command, shown only; tidyshelf never runs it."""
    stem = os.path.splitext(name)[0]
    return f"ffmpeg -i {shlex.quote(name)} -map 0 -c:v libx265 -crf 22 -preset slow -c:a copy -c:s copy " \
           f"{shlex.quote(stem + '.x265.mkv')}"


def deep_check(path: str, cfg: dict) -> str | None:
    """Decode every frame; the first errors as text, or None when clean or ffmpeg is unavailable."""
    exe = _program(cfg, "ffmpeg")
    if exe is None:
        return None
    argv = [exe, "-v", "error", "-xerror", "-nostdin", "-i", os.path.abspath(path), "-f", "null", "-"]
    try:
        out = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=DEEP_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"ffmpeg failed: {exc}"
    if out.returncode != 0 or out.stderr.strip():
        return out.stderr.strip()[:300] or f"ffmpeg exited with {out.returncode}"
    return None
