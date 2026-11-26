import re
import tomllib

from tidyshelf import config

ROOTS = {"source": "/mnt/nas/to-sort", "movies": "/mnt/nas/Videos/Movies", "series": "/mnt/nas/Videos/Series"}


def _with_roots(cfg):
    cfg = config.deep_merge(cfg, {"roots": ROOTS})
    return cfg


def test_defaults_parse_as_toml():
    data = tomllib.loads(config.defaults_text())
    assert data == config.load_defaults()


def test_defaults_validate_clean_once_roots_are_set():
    report = config.validate(_with_roots(config.load_defaults()))
    assert report.errors == []


def test_defaults_alone_report_missing_roots():
    report = config.validate(config.load_defaults())
    assert any("roots.source" in e for e in report.errors)


def test_every_special_rule_names_a_real_key():
    defaults = config.load_defaults()

    def exists(path):
        for n in range(1, len(path)):
            if path[:n] in config.ARRAY_TABLES:
                return exists(path[:n]) and path[n] in config.ARRAY_TABLES[path[:n]]
        node = defaults
        for part in path:
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return True

    for path in (*config.ENUMS, *config.REGEX_KEYS, *config.TEMPLATES, *config.ARRAY_TABLES):
        assert exists(path), f"special rule for missing key {'.'.join(path)}"


def test_every_section_from_plan_is_present():
    defaults = config.load_defaults()
    for section in ("roots", "naming", "parse", "video", "subtitles", "junk", "exclude",
                    "conflicts", "safety", "owner", "override"):
        assert section in defaults, section


def test_every_default_key_is_commented():
    """Each `key = value` line is preceded by a comment line or has a trailing comment."""
    lines = config.defaults_text().splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^[a-z_0-9]+\s*=", line):
            prev = lines[i - 1].strip() if i else ""
            assert prev.startswith("#") or "#" in line, f"uncommented key: {line!r}"


def test_defaults_follow_naming_standard():
    d = config.load_defaults()
    n = d["naming"]
    assert n["movie_folder"] == "{title} ({year})"
    assert n["movie_file"] == "{title} ({year})"
    assert n["season_folder"] == "Season {season}"
    assert n["specials_folder"] == "Season 00"
    assert n["episode_file"] == "S{season:02}{episodes}< - {episode_title}>"
    assert n["subtitle_file"] == "{video}.{lang}<.{variant}>"
    assert n["movie_subs_folder"] == "Subs"
    assert d["subtitles"]["languages"] == ["en", "es", "it"]
    assert d["subtitles"]["default_language"] == "en"
    assert d["subtitles"]["prefer"] == "plain"
    assert d["junk"]["junk_action"] == "trash"
    assert d["video"]["extensions"] == [".mkv", ".mp4", ".avi", ".m4v", ".wmv", ".ts", ".mpg", ".mpeg", ".mov", ".flv"]
    assert d["subtitles"]["extensions"] == [".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"]
    assert d["safety"]["max_delete_files"] == 200
    assert d["safety"]["max_delete_bytes"] == 50 * 1024 * 1024
    assert d["override"] == []


def test_default_regexes_compile():
    report = config.validate(_with_roots(config.load_defaults()))
    assert not [e for e in report.errors if "regex" in e]
