"""Configuration: layered TOML loading and strict validation.

The schema is derived from the shipped ``data/defaults.toml``: every key there is a known key and its
default value gives its type. The small tables below add what a default value cannot say: enums,
regexes, templates, formats and arrays of tables.
"""

from __future__ import annotations

import copy
import difflib
import os
import re
import string
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from tidyshelf import textsubs
from tidyshelf.naming import safe_name
from tidyshelf_apply import check_roots

SYSTEM_CONFIG = Path("/etc/tidyshelf.toml")
ENV_VAR = "TIDYSHELF_CONFIG"

ENUMS: dict[tuple, tuple] = {
    ("naming", "preset"): ("kodi",),
    ("subtitles", "prefer"): ("plain", "sdh"),
    ("subtitles", "detection"): ("stopwords",),
    ("junk", "junk_action"): ("delete", "trash"),
    ("conflicts", "on_exists"): ("report", "skip"),
    ("override", "kind"): ("movie", "series"),
    **{("video", "quality", f"on_{c}"): ("ok", "reject", "warn")
       for c in ("large", "tiny", "codec", "low_res", "no_audio")},
}

REGEX_KEYS: set[tuple] = {
    ("parse", "title_rules", "match"),
    ("parse", "name_rules", "match"),
    ("subtitles", "ad_patterns"),
    ("subtitles", "ad_patterns_extra"),
    ("junk", "patterns"),
    ("exclude", "patterns"),
}

_MOVIE = {"title", "year"}
TEMPLATES: dict[tuple, set] = {
    ("naming", "movie_folder"): _MOVIE,
    ("naming", "movie_file"): _MOVIE,
    ("naming", "season_folder"): {"season"},
    ("naming", "episode_file"): {"title", "season", "episode", "episodes", "episode_title"},
    ("naming", "subtitle_file"): {"video", "lang", "variant"},
}

# Arrays of tables: key -> {field: (type, required)}
ARRAY_TABLES: dict[tuple, dict] = {
    ("override",): {
        "match": (str, True), "title": (str, False), "year": (int, False),
        "kind": (str, False), "season": (int, False), "ignore": (bool, False),
    },
    ("parse", "title_rules"): {"match": (str, True), "replace": (str, True)},
    ("parse", "name_rules"): {"match": (str, True), "replace": (str, True)},
}

_LANG = re.compile(r"[a-z]{2}")
_DURATION = re.compile(r"(\d+[smhd])?")
_MODE = re.compile(r"(0?[0-7]{3})?")
FORMATS: dict[tuple, tuple] = {
    ("subtitles", "languages"): (_LANG, "must be a two-letter lowercase ISO 639-1 code"),
    ("subtitles", "default_language"): (_LANG, "must be a two-letter lowercase ISO 639-1 code"),
    ("safety", "max_age"): (_DURATION, "must be empty or a duration like 30m, 24h, 7d"),
    ("owner", "dir_mode"): (_MODE, "must be empty or an octal mode like 0775"),
    ("owner", "file_mode"): (_MODE, "must be empty or an octal mode like 0775"),
    ("video", "extensions"): (re.compile(r"\.[^\s/]*"), "must start with '.'"),
    ("subtitles", "extensions"): (re.compile(r"\.[^\s/]*"), "must start with '.'"),
}

_TYPE_NAMES = {bool: "boolean", int: "integer", float: "float", str: "string", list: "list", dict: "table"}


class ConfigError(Exception):
    """A config file could not be read or parsed."""


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def defaults_text() -> str:
    return resources.files("tidyshelf").joinpath("data/defaults.toml").read_text(encoding="utf-8")


def load_defaults() -> dict:
    return tomllib.loads(defaults_text())


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "tidyshelf" / "config.toml"


def deep_merge(base: dict, over: dict) -> dict:
    """Tables merge key by key; everything else (lists included) is replaced."""
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read(path: Path) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"{path}: {exc.strerror}") from None


def load(path: str | os.PathLike | None = None) -> tuple[dict, list[str]]:
    """Effective config and the list of layers it came from. Does not validate."""
    cfg = load_defaults()
    sources = ["defaults"]
    for layer in (Path(SYSTEM_CONFIG), user_config_path()):
        if layer.is_file():
            cfg = deep_merge(cfg, _read(layer))
            sources.append(str(layer))
    explicit = path if path is not None else os.environ.get(ENV_VAR) or None
    if explicit is not None:
        cfg = deep_merge(cfg, _read(Path(explicit)))
        sources.append(str(explicit))
    return cfg, sources


# --- validation -------------------------------------------------------------------------------

def _fmt(path: tuple) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else part)
    return out


def _key_only(path: tuple) -> tuple:
    return tuple(p for p in path if not isinstance(p, int))


def _type_name(value) -> str:
    return _TYPE_NAMES.get(type(value), type(value).__name__)


def _type_ok(value, expected: type) -> bool:
    if expected is float:
        return type(value) in (int, float)
    return type(value) is expected


def _unknown(path: tuple, key: str, known) -> str:
    hint = difflib.get_close_matches(key, list(known), n=1, cutoff=0.6)
    msg = f"{_fmt(path + (key,))}: unknown key"
    return msg + (f" (did you mean '{hint[0]}'?)" if hint else "")


def _check_template(text: str, allowed: set) -> str | None:
    if "/" in text or "\\" in text:
        return "must not contain a path separator"
    depth = 0
    for ch in text:
        depth += {"<": 1, ">": -1}.get(ch, 0)
        if depth not in (0, 1):
            return "bad template (unbalanced or nested < >)"
    if depth:
        return "bad template (unbalanced or nested < >)"
    try:
        parsed = list(string.Formatter().parse(text.replace("<", "").replace(">", "")))
    except ValueError as exc:
        return f"bad template ({exc})"
    for _literal, name, spec, conversion in parsed:
        if name is None:
            continue
        if not name or conversion is not None or not re.fullmatch(r"(0?\d+)?", spec or ""):
            return f"bad template (bad field {{{name}{':' + spec if spec else ''}}})"
        if name not in allowed:
            return f"unknown placeholder {{{name}}} (allowed: {', '.join(sorted(allowed))})"
    return None


def _check_scalar(path: tuple, value, expected: type, errors: list) -> None:
    if not _type_ok(value, expected):
        errors.append(f"{_fmt(path)}: expected {_TYPE_NAMES[expected]}, got {_type_name(value)}")
        return
    key = _key_only(path)
    if type(value) is int and value < 0:
        errors.append(f"{_fmt(path)}: must not be negative")
    if key in ENUMS and value not in ENUMS[key]:
        errors.append(f"{_fmt(path)}: must be one of {', '.join(repr(v) for v in sorted(ENUMS[key]))}")
    if key in FORMATS and not FORMATS[key][0].fullmatch(value):
        errors.append(f"{_fmt(path)}: {FORMATS[key][1]}")
    if key == ("override", "match") and value.startswith("re:"):
        key, value = ("override", "match", "re"), value[3:]
    if key in REGEX_KEYS or key == ("override", "match", "re"):
        try:
            re.compile(value)
        except re.error as exc:
            errors.append(f"{_fmt(path)}: bad regex ({exc})")
    if key in TEMPLATES:
        problem = _check_template(value, TEMPLATES[key])
        if problem:
            errors.append(f"{_fmt(path)}: {problem}")


def _check_array_table(path: tuple, value, spec: dict, errors: list) -> None:
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        errors.append(f"{_fmt(path)}: expected list of tables, got {_type_name(value)}")
        return
    for i, item in enumerate(value):
        for key in item:
            if key not in spec:
                errors.append(_unknown(path + (i,), key, spec))
        for key, (typ, required) in spec.items():
            if key in item:
                _check_scalar(path + (i, key), item[key], typ, errors)
            elif required:
                errors.append(f"{_fmt(path + (i, key))}: required")


def _check_node(path: tuple, default, value, errors: list) -> None:
    if path in ARRAY_TABLES:
        _check_array_table(path, value, ARRAY_TABLES[path], errors)
    elif isinstance(default, dict):
        if not isinstance(value, dict):
            errors.append(f"{_fmt(path)}: expected table, got {_type_name(value)}")
            return
        for key, sub in value.items():
            if key not in default:
                errors.append(_unknown(path, key, default))
            else:
                _check_node(path + (key,), default[key], sub, errors)
    elif isinstance(default, list):
        if not isinstance(value, list):
            errors.append(f"{_fmt(path)}: expected list, got {_type_name(value)}")
            return
        item_type = type(default[0]) if default else str
        for i, item in enumerate(value):
            _check_scalar(path + (i,), item, item_type, errors)
    else:
        _check_scalar(path, value, type(default), errors)


def validate(cfg: dict) -> Report:
    """Check an effective config against the schema derived from the defaults. Collects all errors."""
    report = Report()
    _check_node((), load_defaults(), cfg, report.errors)
    roots = cfg.get("roots")
    if isinstance(roots, dict):
        errs, warns = check_roots(roots)
        report.errors.extend(errs)
        report.warnings.extend(warns)
    naming = cfg.get("naming") if isinstance(cfg.get("naming"), dict) else {}
    for key in ("movie_subs_folder", "specials_folder"):
        value = naming.get(key)
        if isinstance(value, str) and (not value or safe_name(value) != value):
            report.errors.append(f"naming.{key}: must be a plain folder name (no / \\ : * ? \" < > |, "
                                 "not empty, not . or ..)")
    languages = cfg.get("subtitles", {}).get("languages") if isinstance(cfg.get("subtitles"), dict) else None
    if isinstance(languages, list):
        known = textsubs.load_wordlists()
        for lang in languages:
            if isinstance(lang, str) and lang not in known:
                report.warnings.append(f"subtitles.languages: no word list for {lang!r}; "
                                       "it is recognised only by file name tags")
    return report
