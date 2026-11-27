import pytest

from tidyshelf import config

ROOTS = {"source": "/mnt/nas/to-sort", "movies": "/mnt/nas/Videos/Movies", "series": "/mnt/nas/Videos/Series"}


def check(**over):
    cfg = config.deep_merge(config.load_defaults(), {"roots": ROOTS})
    cfg = config.deep_merge(cfg, over)
    return config.validate(cfg)


def errors(**over):
    return check(**over).errors


def test_valid_config_has_no_errors():
    assert errors() == []


@pytest.mark.parametrize("over, needle", [
    ({"nmaing": {}}, "nmaing: unknown key (did you mean 'naming'?)"),
    ({"naming": {"movie_fodler": "x"}}, "naming.movie_fodler: unknown key (did you mean 'movie_folder'?)"),
    ({"video": {"quality": {"fhd": {"max_gb_per_hr": 1.0}}}}, "video.quality.fhd.max_gb_per_hr: unknown key (did you mean 'max_gb_per_hour'?)"),
    ({"override": [{"match": "x", "titel": "y"}]}, "override[0].titel: unknown key (did you mean 'title'?)"),
    ({"parse": {"title_rules": [{"match": "x", "replace": "y", "flags": "i"}]}}, "parse.title_rules[0].flags: unknown key"),
])
def test_unknown_keys_at_every_level(over, needle):
    assert any(needle in e for e in errors(**over)), errors(**over)


def test_unknown_key_without_close_match_has_no_hint():
    errs = errors(zzzzzz=1)
    assert errs == ["zzzzzz: unknown key"]


@pytest.mark.parametrize("over, needle", [
    ({"safety": {"max_delete_files": "200"}}, "safety.max_delete_files: expected integer, got string"),
    ({"safety": {"max_delete_files": True}}, "safety.max_delete_files: expected integer, got boolean"),
    ({"safety": {"require_approve": 1}}, "safety.require_approve: expected boolean, got integer"),
    ({"safety": {"max_delete_files": -1}}, "safety.max_delete_files: must not be negative"),
    ({"subtitles": {"languages": "en"}}, "subtitles.languages: expected list, got string"),
    ({"subtitles": {"languages": ["en", 3]}}, "subtitles.languages[1]: expected string, got integer"),
    ({"naming": "kodi"}, "naming: expected table, got string"),
    ({"override": {"match": "x"}}, "override: expected list of tables, got table"),
    ({"override": [{"title": "x"}]}, "override[0].match: required"),
    ({"override": [{"match": "x", "year": "1999"}]}, "override[0].year: expected integer, got string"),
])
def test_wrong_types(over, needle):
    assert any(needle in e for e in errors(**over)), errors(**over)


def test_int_is_accepted_where_float_is_expected():
    assert errors(subtitles={"min_confidence": 1}) == []


@pytest.mark.parametrize("over, needle", [
    ({"junk": {"junk_action": "shred"}}, "junk.junk_action: must be one of 'delete', 'trash'"),
    ({"naming": {"preset": "emby"}}, "naming.preset: must be one of 'kodi', 'plex'"),
    ({"conflicts": {"on_exists": "replace"}}, "conflicts.on_exists: must be one of"),
    ({"override": [{"match": "x", "kind": "film"}]}, "override[0].kind: must be one of"),
    ({"subtitles": {"languages": ["en", "english"]}}, "subtitles.languages[1]: must be a two-letter lowercase ISO 639-1 code"),
    ({"subtitles": {"default_language": "EN"}}, "subtitles.default_language: must be a two-letter lowercase ISO 639-1 code"),
    ({"safety": {"max_age": "1 day"}}, "safety.max_age: must be empty or a duration like 30m, 24h, 7d"),
    ({"owner": {"dir_mode": "777x"}}, "owner.dir_mode: must be empty or an octal mode like 0775"),
    ({"video": {"extensions": ["mkv"]}}, "video.extensions[0]: must start with '.'"),
])
def test_enums_and_formats(over, needle):
    assert any(needle in e for e in errors(**over)), errors(**over)


@pytest.mark.parametrize("over, needle", [
    ({"exclude": {"patterns": ["("]}}, "exclude.patterns[0]: bad regex"),
    ({"junk": {"patterns": ["[a-"]}}, "junk.patterns[0]: bad regex"),
    ({"subtitles": {"ad_patterns_extra": ["*x"]}}, "subtitles.ad_patterns_extra[0]: bad regex"),
    ({"parse": {"title_rules": [{"match": "(", "replace": ""}]}}, "parse.title_rules[0].match: bad regex"),
])
def test_bad_regex(over, needle):
    assert any(needle in e for e in errors(**over)), errors(**over)


def test_override_match_is_a_glob_not_validated_as_regex():
    assert errors(override=[{"match": "Some.Folder*"}]) == []


@pytest.mark.parametrize("over, needle", [
    ({"naming": {"movie_folder": "{titel} ({year})"}}, "naming.movie_folder: unknown placeholder {titel}"),
    ({"naming": {"season_folder": "Season {episode}"}}, "naming.season_folder: unknown placeholder {episode}"),
    ({"naming": {"movie_file": "{title"}}, "naming.movie_file: bad template"),
    ({"naming": {"movie_folder": "{title}/{year}"}}, "naming.movie_folder: must not contain a path separator"),
    ({"naming": {"movie_file": "{title}\\{year}"}}, "naming.movie_file: must not contain a path separator"),
    ({"naming": {"episode_file": "S{season:02}{episodes}< - {episode_title}"}}, "naming.episode_file: bad template"),
    ({"naming": {"movie_folder": "{year:x}"}}, "naming.movie_folder: bad template"),
])
def test_bad_templates(over, needle):
    assert any(needle in e for e in errors(**over)), errors(**over)


def test_template_with_format_spec_and_optional_segment_is_fine():
    assert errors(naming={"episode_file": "{title} - S{season:02}E{episode:02}< - {episode_title}>"}) == []


@pytest.mark.parametrize("roots, needle", [
    ({"movies": "/mnt/nas/to-sort/Movies"}, "roots.movies: overlaps roots.source"),
    ({"series": "/mnt/nas/Videos/Movies"}, "roots.series: overlaps roots.movies"),
    ({"source": "/mnt/nas/Videos"}, "overlaps roots.source"),
    ({"source": "relative/path"}, "roots.source: must be an absolute path"),
    ({"source": "/"}, "roots.source: must not be /"),
    ({"source": ""}, "roots.source: required"),
    ({"moviez": "/x"}, "roots.moviez: unknown key (did you mean 'movies'?)"),
])
def test_bad_roots(roots, needle):
    errs = errors(roots=roots)
    assert any(needle in e for e in errs), errs


def test_sibling_prefix_roots_do_not_overlap():
    assert errors(roots={"movies": "/mnt/nas/Videos/Movies", "series": "/mnt/nas/Videos/Movies2"}) == []


def test_overlap_uses_realpaths_when_they_exist(tmp_path):
    (tmp_path / "real" / "Movies").mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real")
    (tmp_path / "series").mkdir()
    errs = errors(roots={"source": str(tmp_path / "real"), "movies": str(tmp_path / "link" / "Movies"),
                         "series": str(tmp_path / "series")})
    assert any("roots.movies: overlaps roots.source" in e for e in errs), errs


def test_missing_roots_are_warnings_not_errors(tmp_path):
    report = check(roots={"source": str(tmp_path / "nope"), "movies": str(tmp_path / "m"), "series": str(tmp_path / "s")})
    assert report.errors == []
    assert any("roots.source" in w and "does not exist" in w for w in report.warnings)


def test_all_errors_are_collected():
    errs = errors(nmaing={}, safety={"max_delete_files": "x"}, junk={"junk_action": "shred"})
    assert len(errs) == 3


@pytest.mark.parametrize("over, needle", [
    ({"safety": {"max_age": "1h\n"}}, "safety.max_age"),
    ({"owner": {"dir_mode": "0775\n"}}, "owner.dir_mode"),
    ({"subtitles": {"default_language": "en\n"}}, "subtitles.default_language"),
])
def test_formats_reject_a_trailing_newline(over, needle):
    assert any(needle in e for e in errors(**over)), errors(**over)


def test_allowed_language_without_word_list_warns():
    report = check(subtitles={"languages": ["en", "pl"]})
    assert report.errors == []
    assert any("'pl'" in w for w in report.warnings), report.warnings


def test_name_rules_are_checked():
    assert check(parse={"name_rules": [{"match": "(", "replace": ""}]}).errors
    assert check(parse={"name_rules": [{"match": "^x", "replace": ""}]}).errors == []


@pytest.mark.parametrize("value", ["../x", "Sub:s", "a/b"])
def test_movie_subs_folder_must_be_a_plain_name(value):
    assert any("movie_subs_folder" in e for e in check(naming={"movie_subs_folder": value}).errors)
