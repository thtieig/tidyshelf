"""Plan hygiene: strict parsing, schema, plan id, approve / max-age / require_approve."""

import copy
import datetime as dt
import json

import pytest

import tidyshelf_apply as ta

from helpers.plans import movie_plan


def base_doc():
    return {
        "format": 1, "tool": "tidyshelf test", "created": "2026-11-26T20:15:00Z",
        "items": [{
            "id": "movie-0001", "kind": "movie", "status": "ready", "source": "source:A",
            "findings": [{"level": "warn", "code": "x.y", "msg": "hello"}],
            "ops": [
                {"op": "mkdir", "path": "movies:A (2000)"},
                {"op": "move", "from": "source:A/a.mkv", "to": "movies:A (2000)/A (2000).mkv", "size": 1, "mtime": 2},
                {"op": "set_mode", "path": "movies:A (2000)/A (2000).mkv"},
                {"op": "delete", "path": "source:A/x.txt", "size": 1, "mtime": 2, "reason": "junk"},
                {"op": "delete", "path": "source:A", "dir": True, "reason": "empty"},
            ],
        }],
    }


def check(doc):
    ta.validate_plan(doc)


def bad(doc, needle):
    with pytest.raises(ta.PlanError) as exc:
        check(doc)
    assert needle in str(exc.value), str(exc.value)


def test_valid_plan_passes():
    check(base_doc())


def test_parse_rejects_duplicate_keys():
    with pytest.raises(ta.PlanError, match="duplicate key"):
        ta.parse_plan('{"format": 1, "format": 1, "items": []}')


@pytest.mark.parametrize("text", ['{"format": 1.0, "items": []}', '{"format": NaN, "items": []}',
                                  '{"format": 1e0, "items": []}'])
def test_parse_rejects_floats_and_constants(text):
    with pytest.raises(ta.PlanError):
        ta.parse_plan(text)


def test_parse_rejects_non_json():
    with pytest.raises(ta.ReadError):
        ta.parse_plan("not json")


def test_unknown_op_type():
    doc = base_doc()
    doc["items"][0]["ops"].insert(0, {"op": "copy", "from": "source:a", "to": "movies:b"})
    bad(doc, "unknown op 'copy'")


@pytest.mark.parametrize("op_index, mutate, needle", [
    (1, lambda op: op.update(extra=1), "unknown field 'extra'"),
    (1, lambda op: op.pop("size"), "missing field 'size'"),
    (0, lambda op: op.update(mode="0775"), "unknown field 'mode'"),
    (2, lambda op: op.update(mode="0775"), "unknown field 'mode'"),
    (3, lambda op: op.pop("reason"), "missing field 'reason'"),
    (4, lambda op: op.update(dir=False), "'dir' must be true"),
    (4, lambda op: op.update(size=1), "unknown field 'size'"),
    (1, lambda op: op.update(size=True), "must be a non-negative integer"),
    (1, lambda op: op.update(size=-1), "must be a non-negative integer"),
    (1, lambda op: op.update(mtime="2"), "must be a non-negative integer"),
    (1, lambda op: op.update(to=5), "must be a string"),
])
def test_op_fields(op_index, mutate, needle):
    doc = base_doc()
    mutate(doc["items"][0]["ops"][op_index])
    bad(doc, needle)


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d.update(format=2), "unsupported format"),
    (lambda d: d.update(format=True), "unsupported format"),
    (lambda d: d.pop("format"), "missing field 'format'"),
    (lambda d: d.update(plan_id="abc"), "unknown field 'plan_id'"),
    (lambda d: d.update(items={}), "'items' must be a list"),
    (lambda d: d["items"][0].update(colour="red"), "unknown field 'colour'"),
    (lambda d: d["items"][0].pop("status"), "missing field 'status'"),
    (lambda d: d["items"][0].update(status="maybe"), "status must be one of"),
    (lambda d: d["items"][0].update(kind="Movie"), "kind"),
    (lambda d: d["items"][0].update(id="a b"), "id"),
    (lambda d: d["items"][0]["findings"][0].update(extra=1), "unknown field 'extra'"),
    (lambda d: d["items"].append(copy.deepcopy(d["items"][0])), "duplicate item id 'movie-0001'"),
])
def test_plan_and_item_fields(mutate, needle):
    doc = base_doc()
    mutate(doc)
    bad(doc, needle)


@pytest.mark.parametrize("status", ["needs_review", "skipped"])
def test_non_ready_items_must_have_no_ops(status):
    doc = base_doc()
    doc["items"][0]["status"] = status
    bad(doc, "must have no ops")
    doc["items"][0]["ops"] = []
    check(doc)


def test_deletes_must_come_last():
    doc = base_doc()
    ops = doc["items"][0]["ops"]
    ops.append(ops.pop(1))
    bad(doc, "deletes must come last")


def test_duplicate_destination_across_items():
    doc = base_doc()
    second = copy.deepcopy(doc["items"][0])
    second["id"] = "movie-0002"
    second["ops"] = [{"op": "move", "from": "source:B/b.mkv", "to": "movies:A (2000)/A (2000).mkv", "size": 1, "mtime": 2}]
    doc["items"].append(second)
    bad(doc, "more than one op")


def test_mkdir_of_a_root_is_refused():
    doc = base_doc()
    doc["items"][0]["ops"][0]["path"] = "movies:"
    bad(doc, "empty path")


def test_id_stable_under_key_order_and_whitespace():
    doc = base_doc()
    reordered = json.loads(json.dumps(doc, sort_keys=True))
    text = json.dumps(doc, indent=7)
    assert ta.plan_id(doc) == ta.plan_id(reordered) == ta.plan_id(ta.parse_plan(text))


def test_id_ignores_findings_created_and_tool():
    doc = base_doc()
    other = copy.deepcopy(doc)
    other["created"] = "2020-01-01T00:00:00Z"
    other["tool"] = "something else"
    other["items"][0]["findings"] = []
    other["items"][0]["source"] = "source:Z"
    other["items"][0]["kind"] = "series"
    assert ta.plan_id(doc) == ta.plan_id(other)


@pytest.mark.parametrize("mutate", [
    lambda d: d["items"][0]["ops"][1].update(size=99),
    lambda d: d["items"][0]["ops"][1].update(to="movies:A (2000)/B.mkv"),
    lambda d: d["items"][0]["ops"].reverse(),
    lambda d: d["items"][0].update(status="skipped"),
    lambda d: d["items"][0].update(id="movie-0002"),
])
def test_id_changes_on_any_op_edit(mutate):
    doc = base_doc()
    other = copy.deepcopy(doc)
    mutate(other)
    assert ta.plan_id(doc) != ta.plan_id(other)


def test_golden_id_vector():
    # Changing this value means every plan id people have reviewed changes: only with a format bump.
    assert ta.plan_id(base_doc()) == GOLDEN_ID
    assert len(GOLDEN_ID) == 12 and int(GOLDEN_ID, 16) >= 0


GOLDEN_ID = "bde7ed011ec9"


# --- through the CLI -----------------------------------------------------------------------------

def test_invalid_plan_exit_3(env):
    r = env.run({"format": 1, "items": [{"id": "x"}]})
    assert r.code == 3
    assert "missing field" in r.err


def test_unreadable_plan_exit_1(env):
    r = env.run("{not json")
    assert r.code == 1


def test_missing_plan_file_exit_1(env):
    r = env.run(None, str(env.plans / "nope.json"))
    assert r.code == 1


def test_approve_mismatch_refused(env):
    plan = movie_plan(env)
    r = env.execute(plan, "--approve", "000000000000")
    assert r.code == 3
    assert "does not match" in r.err
    r = env.execute(plan, "--approve", plan.plan_id)
    assert r.code == 0, r.err


def test_approve_checks_full_plan_before_only(env):
    plan = movie_plan(env)
    r = env.run(plan, "--only", "movie-0001", "--approve", plan.plan_id)
    assert r.code == 2, r.err


def _aged(plan, hours):
    created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    doc = plan.to_dict()
    doc["created"] = created.strftime("%Y-%m-%dT%H:%M:%SZ")
    return doc


def test_max_age(env):
    plan = movie_plan(env)
    assert env.run(_aged(plan, 25), "--max-age", "24h").code == 3
    assert env.run(_aged(plan, 1), "--max-age", "24h").code == 2


@pytest.mark.parametrize("created", [None, "yesterday"])
def test_max_age_needs_a_valid_created(env, created):
    doc = movie_plan(env).to_dict()
    if created is None:
        del doc["created"]
    else:
        doc["created"] = created
    r = env.run(doc, "--max-age", "24h")
    assert r.code == 3
    assert "created" in r.err


def test_max_age_from_config(env):
    env.write_config('[safety]\nmax_age = "1h"\n')
    assert env.run(_aged(movie_plan(env), 2)).code == 3


def test_require_approve(env):
    env.write_config("[safety]\nrequire_approve = true\n")
    plan = movie_plan(env)
    r = env.execute(plan)
    assert r.code == 3
    assert "--approve" in r.err
    assert env.execute(plan, "--approve", plan.plan_id).code == 0


def test_unknown_only_or_skip_id_exit_3(env):
    plan = movie_plan(env)
    assert env.run(plan, "--only", "nope").code == 3
    assert env.run(plan, "--skip", "nope").code == 3
