"""Fixtures for tidyshelf-apply tests: a fake NAS with three roots, a config, and a runner."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import tidyshelf_apply as ta
from helpers.tree import list_tree, make_tree
from tidyshelf.plan import Plan, file_stat


@dataclass
class Result:
    code: int
    out: str
    err: str

    def json(self):
        return json.loads(self.out)


@dataclass
class Env:
    tmp: Path
    capsys: object
    nas: Path = field(init=False)
    source: Path = field(init=False)
    movies: Path = field(init=False)
    series: Path = field(init=False)
    config_path: Path = field(init=False)
    plan_path: Path = field(init=False)

    def __post_init__(self):
        self.nas = self.tmp / "nas"
        self.source = self.nas / "to-sort"
        self.movies = self.nas / "Movies"
        self.series = self.nas / "Series"
        for p in (self.source, self.movies, self.series):
            p.mkdir(parents=True)
        self.plans = self.tmp / "plans"
        self.plans.mkdir()
        self.plan_path = self.plans / "plan.json"
        self.config_path = self.tmp / "apply.toml"
        self.write_config()

    def write_config(self, extra: str = "") -> None:
        self.config_path.write_text(
            "[roots]\n"
            f'source = "{self.source}"\nmovies = "{self.movies}"\nseries = "{self.series}"\n' + extra,
            encoding="utf-8",
        )

    def tree(self, root: str, listing: str) -> Path:
        return make_tree(getattr(self, root), listing)

    def path(self, ref: str) -> Path:
        root, rel = ref.split(":", 1)
        return getattr(self, root) / rel

    def stat(self, ref: str) -> tuple[int, int]:
        return file_stat(self.path(ref))

    def snapshot(self) -> list[str]:
        return list_tree(self.nas)

    def new_plan(self) -> Plan:
        return Plan()

    def write_plan(self, plan) -> Path:
        text = plan if isinstance(plan, str) else (plan.dumps() if isinstance(plan, Plan) else json.dumps(plan))
        self.plan_path.write_text(text, encoding="utf-8")
        return self.plan_path

    def run(self, plan=None, *args: str, config: bool = True) -> Result:
        argv = []
        if plan is not None:
            argv.append(str(self.write_plan(plan)))
        if config:
            argv += ["--config", str(self.config_path)]
        argv += list(args)
        self.capsys.readouterr()
        code = ta.main(argv)
        out, err = self.capsys.readouterr()
        return Result(code, out, err)

    def execute(self, plan, *args: str) -> Result:
        return self.run(plan, "--execute", "--yes", *args)

    @property
    def journal_path(self) -> Path:
        return self.plans / "plan.journal.jsonl"

    def journal(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        return [json.loads(line) for line in self.journal_path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def env(tmp_path, capsys):
    return Env(tmp_path, capsys)
