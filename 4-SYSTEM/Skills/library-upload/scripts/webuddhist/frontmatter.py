"""Read a note's frontmatter; write ids into it without touching anything else.

Only the ids of vault.FRONTMATTER_ENV are written, and only into an empty key (or one that
already holds the same value): a different id already there is never overwritten — that
is reported instead. The one exception is a *superseded* id: one the caller has just
deleted on the server, or confirmed is no longer there (e.g. the table of contents that
`replace` or `update --only toc` replaced). That id is replaced by its successor, so the
frontmatter never points at something gone. Line endings (CRLF/LF) and every other line
stay as they are.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ID_KEYS = ("text_id", "edition_id", "toc_id", "aligned_to_edition_id")


def read(path) -> dict:
    raw = Path(path).read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
    if not raw.startswith("---\n"):
        raise ValueError("no YAML frontmatter")
    end = raw.find("\n---", 3)
    if end < 0:
        raise ValueError("frontmatter not closed by ---")
    fm = yaml.safe_load(raw[4:end]) or {}
    if not isinstance(fm, dict):
        raise ValueError("frontmatter is not a mapping")
    return fm


def ids(path) -> dict:
    fm = read(path)
    return {k: str(fm[k]).strip() for k in ID_KEYS if fm.get(k) not in (None, "")}


def write_ids(path, supersedes=None, **values) -> list:
    """Set id keys in the frontmatter. -> list of keys written. Raises if a key holds another id,
    unless that id is in supersedes[key] (ids confirmed gone from the server)."""
    supersedes = {k: {str(x) for x in v} for k, v in (supersedes or {}).items()}
    path = Path(path)
    raw = path.read_bytes().decode("utf-8-sig")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    if lines[0] != "---":
        raise ValueError(f"{path.name}: no frontmatter")
    end = lines.index("---", 1)
    written = []
    for key, value in values.items():
        if not value:
            continue
        pat = re.compile(rf"^{re.escape(key)}:\s*(.*)$")
        idx = next((i for i in range(1, end) if pat.match(lines[i])), None)
        if idx is None:
            lines.insert(end, f"{key}: {value}")
            end += 1
            written.append(key)
            continue
        current = pat.match(lines[idx]).group(1).strip().strip("'\"")
        if current == value:
            continue
        if current and current not in supersedes.get(key, ()):
            raise ValueError(f"{path.name}: {key} is {current}, not {value}: not overwritten")
        lines[idx] = f"{key}: {value}"
        written.append(key)
    if written:
        path.write_bytes(nl.join(lines).encode("utf-8"))
    return written
