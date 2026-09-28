"""The repository stays neutral: no local paths, no real e-mail domains, no credentials, none of the
generic forbidden terms, and - when a private blocklist is supplied through POPHEALTH_PRIVATE_BLOCKLIST -
none of the terms on it. The private list never lives in the repository; CI runs the generic checks."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "build",
    "workspace",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "logs",
    "inbox",
    ".rlib",
    "dbt_packages",
}
SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
    ".html",
    ".csv",
    ".sql",
    ".R",
    ".cfg",
    ".jsonl",
    ".gitignore",
    ".gitattributes",
}

CHECKS = {
    "local path": re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/](?![\\/])|\\\\[A-Za-z0-9_.$-]+\\"),
    "e-mail outside example domains": re.compile(r"[\w.+-]+@(?!example\.(?:org|com)\b)[\w-]+\.[a-z]{2,}", re.I),
    "credential": re.compile(
        r"(?:password|passwd|api[_-]?key|secret[_-]?key|access[_-]?token)\s*[=:]\s*['\"]?\w{6,}", re.I
    ),
    "internal host": re.compile(r"\b[a-z0-9-]+\.(?:corp|internal|lan|local)\b", re.I),
}


def _files() -> list[Path]:
    out = []
    for path in REPO.rglob("*"):
        if (
            path.is_file()
            and not SKIP_DIRS & set(path.relative_to(REPO).parts)
            and (path.suffix in SUFFIXES or path.name in SUFFIXES)
        ):
            out.append(path)
    return out


def _terms(path: Path) -> list[tuple[str, re.Pattern]]:
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("word:"):
            word = line[5:].strip()
            terms.append((word, re.compile(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", re.I)))
        else:
            terms.append((line, re.compile(re.escape(line), re.I)))
    return terms


def test_no_local_paths_addresses_or_credentials():
    hits = []
    for path in _files():
        if path.name == "test_privacy.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in CHECKS.items():
            m = pattern.search(text)
            if m:
                hits.append(f"{path.relative_to(REPO)}: {name}: {m.group()[:40]}")
    assert not hits, hits[:10]


def test_no_generic_forbidden_terms():
    terms = _terms(REPO / "privacy" / "forbidden_terms.txt")
    hits = [
        f"{p.relative_to(REPO)}: {t}"
        for p in _files()
        if p.parent.name != "privacy"
        for t, rx in terms
        if rx.search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not hits, hits[:10]


@pytest.mark.skipif(not os.environ.get("POPHEALTH_PRIVATE_BLOCKLIST"), reason="no private blocklist supplied")
def test_no_private_terms():
    terms = _terms(Path(os.environ["POPHEALTH_PRIVATE_BLOCKLIST"]))
    hits = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [f"{path.relative_to(REPO)}: {t}" for t, rx in terms if rx.search(text)]
    assert not hits, hits[:20]
