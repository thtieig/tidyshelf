"""Producer-side plan builder agrees with the executor's validator and plan id."""

import json
import os

import tidyshelf_apply as ta
from tidyshelf.plan import Plan, file_stat, trash_ref


def sample():
    plan = Plan(created="2026-11-26T20:15:00Z")
    item = plan.item("movie-0001", "movie", source="source:A")
    item.finding("warn", "subtitle.language_mismatch", "labelled English, text is Romanian")
    item.mkdir("movies:A (2000)")
    item.move("source:A/a.mkv", "movies:A (2000)/A (2000).mkv", 10, 20)
    item.set_mode("movies:A (2000)/A (2000).mkv")
    item.delete_file("source:A/x.txt", 1, 2, reason="junk")
    item.delete_dir("source:A", reason="empty")
    plan.item("movie-0002", "movie", status="needs_review").finding("warn", "year.missing", "no year")
    return plan


def test_builder_output_validates_and_ids_agree():
    plan = sample()
    doc = ta.parse_plan(plan.dumps())
    ta.validate_plan(doc)
    assert ta.plan_id(doc) == plan.plan_id


def test_dumps_is_ascii_and_deterministic():
    a, b = sample().dumps(), sample().dumps()
    assert a == b and a.isascii()
    assert json.loads(a)["format"] == 1
    assert json.loads(a)["tool"].startswith("tidyshelf ")


def test_created_defaults_to_now_utc():
    created = Plan().to_dict()["created"]
    assert created.endswith("Z") and "T" in created


def test_file_stat_uses_whole_seconds(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    os.utime(p, ns=(1_700_000_000_999_999_999, 1_700_000_000_999_999_999))
    assert file_stat(p) == (3, 1_700_000_000)


def test_trash_ref_mirrors_origin():
    assert trash_ref("movies:A/poster.jpg") == "source:.tidyshelf-trash/movies/A/poster.jpg"
    assert trash_ref("source:A/readme.txt") == "source:.tidyshelf-trash/source/A/readme.txt"
