"""Public-repo hygiene (PLAN §13): no personal paths, IPs or e-mail addresses in files that git would publish.

An extra denylist (host names, real names, ...) can live in the gitignored ``local/hygiene-denylist.txt``:
one case-insensitive substring per line, ``#`` starts a comment.
"""

import os
import pwd
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
THIS_FILE = Path(__file__).resolve()
# Captured at import, before the autouse fixture points HOME at a temp dir.
REAL_HOME = pwd.getpwuid(os.getuid()).pw_dir

HOME_PATH = re.compile(r"/home/[A-Za-z0-9_.-]+")
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ALLOWED_EMAIL = re.compile(r"(noreply|no-reply)[^@]*@|@users\.noreply\.github\.com$|@example\.(com|org|net)$", re.I)


def _git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def _publishable_files():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    out = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    assert out.returncode == 0, out.stderr
    for name in sorted(set(filter(None, out.stdout.split("\0")))):
        path = REPO / name
        if path.resolve() == THIS_FILE or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data[:4096]:
            continue
        yield name, data.decode("utf-8", errors="replace")


def _denylist():
    path = REPO / "local" / "hygiene-denylist.txt"
    if not path.exists():
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            words.append(line.lower())
    return words


def _is_ipv4(match):
    return all(0 <= int(part) <= 255 for part in match.groups())


def test_no_personal_details_in_publishable_files():
    deny = _denylist()
    problems = []
    for name, text in _publishable_files():
        lower = text.lower()
        if REAL_HOME != "/" and REAL_HOME.lower() in lower:
            problems.append(f"{name}: contains the real home path")
        for m in HOME_PATH.finditer(text):
            problems.append(f"{name}: home path {m.group(0)!r}")
        for m in IPV4.finditer(text):
            if _is_ipv4(m):
                problems.append(f"{name}: IPv4 address {m.group(0)!r}")
        for m in EMAIL.finditer(text):
            if not ALLOWED_EMAIL.search(m.group(0)):
                problems.append(f"{name}: e-mail address {m.group(0)!r}")
        for word in deny:
            if word in lower:
                problems.append(f"{name}: denylisted term (see local/hygiene-denylist.txt)")
    assert not problems, "\n".join(problems)


def test_commit_emails_are_noreply():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    if _git("rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        pytest.skip("no commits yet")
    out = _git("log", "--format=%ae%n%ce")
    assert out.returncode == 0, out.stderr
    bad = sorted({e for e in out.stdout.split() if not ALLOWED_EMAIL.search(e)})
    assert not bad, f"non-noreply commit e-mails: {bad}"


def test_denylist_file_is_gitignored():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    out = _git("check-ignore", "-q", "local/hygiene-denylist.txt")
    assert out.returncode == 0, "local/ must be gitignored"
